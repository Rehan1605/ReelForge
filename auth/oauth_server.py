"""
auth/oauth_server.py
--------------------
ReelForge V3.3 Layer 2: browser-based Microsoft OAuth authorization-code flow
with PKCE (public client, no client secret).

Responsibilities (THIS layer):
  - Start OAuth for a ReelForge user: generate + sign state, delegate PKCE and
    authorization-URL construction to MSAL, persist the OAuth session.
  - Host a lightweight local callback endpoint (GET <redirect path>).
  - Validate state/session (signature, expiry, single-use, user binding).
  - Exchange the authorization code for tokens via MSAL (PKCE verifier).
  - Look up Microsoft Graph /me and persist the connection metadata in the
    user's 'microsoft' subdocument (schema from Layer 1).

Deliberately NOT implemented here (later layers):
  - per-user GraphClient / OneNote publishing
  - notebook/section discovery
  - /status or /disconnect UX
  - async workers / deployment / admin dashboard

Security invariants:
  - Access tokens, refresh tokens, token caches, authorization codes, raw
    OAuth state, PKCE verifiers and the MSAL nonce are never logged and never
    appear in HTTP responses or user-facing messages.
  - State is signed with OAUTH_STATE_SECRET (mandatory), stored only as a
    SHA-256 hash, bound to the ReelForge user, expiring, and single-use.
  - The OAuth session is consumed atomically (replay-safe).
  - The non-secret MSAL flow fields (nonce, decorated scope,
    claims_challenge) are persisted so the callback can reconstruct the exact
    flow returned by initiate_auth_code_flow(); without the nonce MSAL's OIDC
    validation raises KeyError on the success path, and without the decorated
    scope offline_access would be dropped from the token request.
"""

from __future__ import annotations

import html
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import config
import msal
import requests
from msal import SerializableTokenCache

from auth.state import (
    compose_signed_state,
    generate_state,
    require_state_secret,
    sign_state,
    split_signed_state,
    verify_state_signature,
)
from onenote.graph_client import GRAPH_SCOPES
from pymongo.errors import DuplicateKeyError
from storage.oauth_session import (
    consume_oauth_session,
    create_oauth_session,
    get_oauth_session_by_state_hash,
    hash_state,
)
from storage.user import get_user_by_id, update_user_microsoft

# Delegated permissions required for the OneNote saving feature. 'offline_access'
# is a reserved scope that MSAL appends automatically at request time (it enables
# refresh-token issuance); it must NOT be listed here explicitly.
MICROSOFT_SCOPES = list(GRAPH_SCOPES)  # ['User.Read', 'Notes.ReadWrite']

# Microsoft Graph endpoint for the signed-in user's own profile.
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"

# User-facing safe messages (no secrets, no exception details, no identities).
_MSG_STATE_INVALID = "Invalid or tampered OAuth state."
_MSG_STATE_MISSING = "Missing OAuth state parameter."
_MSG_SESSION_UNKNOWN = "Unknown or invalid OAuth session."
_MSG_SESSION_EXPIRED = "This connection request has expired. Please start again from the ReelForge bot."
_MSG_SESSION_USED = "This connection request has already been used."
_MSG_CODE_MISSING = "Missing authorization code parameter."
_MSG_PROVIDER_ERROR = "Microsoft sign-in was not completed. Please try again from the ReelForge bot."
_MSG_EXCHANGE_FAILED = "Could not complete the Microsoft connection. Please try again from the ReelForge bot."
_MSG_SAVE_FAILED = "Could not save the Microsoft connection. Please try again."
_MSG_DUPLICATE = "This Microsoft account is already connected to another ReelForge account."
_MSG_SUCCESS = "Microsoft account connected successfully. You can close this window."


class CallbackRejectedError(Exception):
    """Raised for OAuth callbacks that must be rejected safely."""


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_is_valid(session: dict) -> bool:
    """Return False for expired or structurally-invalid OAuth sessions."""
    expires_at = session.get("expires_at")
    if not expires_at:
        return False
    try:
        # PyMongo returns naive UTC datetimes for stored BSON dates.
        # Compare against aware-or-naive accordingly.
        if expires_at.tzinfo is not None:
            return expires_at > datetime.now(timezone.utc)
        return expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return False


def _build_public_client(token_cache: SerializableTokenCache | None = None) -> msal.PublicClientApplication:
    """Construct the public client application from the active configuration."""
    return msal.PublicClientApplication(
        config.MICROSOFT_CLIENT_ID,
        authority=config.MICROSOFT_AUTHORITY,
        token_cache=token_cache,
    )


def _verify_pkce_in_url(auth_url: str) -> None:
    """Fail loudly if the generated authorization URL lacks S256 PKCE params."""
    query = parse_qs(urlsplit(auth_url).query)
    if not query.get("code_challenge"):
        raise RuntimeError("Generated Microsoft authorization URL is missing the PKCE code_challenge.")
    if query.get("code_challenge_method", [""])[0] != "S256":
        raise RuntimeError("Generated Microsoft authorization URL must use PKCE S256.")


def start_oauth_for_user(user_id: str) -> str:
    """
    Begin the OAuth authorization-code flow for a ReelForge user.

    Steps:
      1. Require OAUTH_STATE_SECRET (fails clearly if unset).
      2. Verify the ReelForge user exists.
      3. Generate an unguessable raw state and sign it.
      4. Let MSAL build the authorization URL with PKCE (S256) params.
      5. Persist the OAuth session bound to user_id: the state hash, the PKCE
         verifier, and the non-secret MSAL flow fields (nonce, decorated scope,
         claims_challenge) are stored. Raw state is never stored/logged.
         nonce/scope are needed by MSAL's exchange to validate the ID token and
         to keep offline_access in the token request.

    Returns the Microsoft authorization URL for the caller (bot, CLI, etc.).
    Raises ValueError for unknown users and RuntimeError for configuration or
    storage failures.
    """
    require_state_secret()

    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError(f"Unknown ReelForge user_id: {user_id}")

    raw_state = generate_state()
    signed_state = compose_signed_state(raw_state)

    public_client = _build_public_client(SerializableTokenCache())
    flow = public_client.initiate_auth_code_flow(
        MICROSOFT_SCOPES,
        redirect_uri=config.OAUTH_REDIRECT_URI,
        state=signed_state,
        response_mode="query",
    )

    auth_url = flow.get("auth_uri") or ""
    if not auth_url:
        raise RuntimeError("MSAL did not produce an authorization URL.")
    _verify_pkce_in_url(auth_url)

    state_hash = hash_state(raw_state)
    session_id = create_oauth_session(
        user_id,
        state_hash,
        code_verifier=flow.get("code_verifier") or "",
        nonce=flow.get("nonce"),
        scope=flow.get("scope"),
        claims_challenge=flow.get("claims_challenge"),
    )
    if session_id is None:
        raise RuntimeError("Could not persist the OAuth session (storage unavailable).")

    return auth_url


def exchange_auth_code(signed_state: str, session: dict, code: str) -> tuple[str, str, dict]:
    """
    Redeem the authorization code for tokens using the session's PKCE verifier.

    The MSAL flow dict returned by initiate_auth_code_flow() is reconstructed
    here from the persisted session fields: state, redirect_uri, decorated
    scope, code_verifier, plus the raw nonce (required by MSAL's OIDC success
    path) and claims_challenge when present. Without the nonce, MSAL raises
    KeyError as soon as the token response contains an ID token; without the
    decorated scope, offline_access/openid/profile would vanish from the token
    request.

    Returns (access_token, serialized_token_cache, msal_result). Raises
    CallbackRejectedError on any failure; never returns or exposes error details
    that might contain sensitive information.
    """
    cache = SerializableTokenCache()
    public_client = _build_public_client(cache)

    code_verifier = session.get("code_verifier") or ""
    if not code_verifier:
        raise CallbackRejectedError(_MSG_EXCHANGE_FAILED)

    flow = {
        "state": signed_state,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "scope": session.get("scope") or MICROSOFT_SCOPES,
        "code_verifier": code_verifier,
    }
    nonce = session.get("nonce")
    if nonce:
        flow["nonce"] = nonce
    claims_challenge = session.get("claims_challenge")
    if claims_challenge:
        flow["claims_challenge"] = claims_challenge

    try:
        result = public_client.acquire_token_by_auth_code_flow(
            flow,
            {"code": code, "state": signed_state},
        )
    except Exception as exc:  # CSRF/state mismatch/value errors surface as ValueError
        print(f"Notice: token exchange rejected: {type(exc).__name__}")
        raise CallbackRejectedError(_MSG_EXCHANGE_FAILED) from exc

    if not result or "access_token" not in result:
        # Safe diagnostic only: error_description/error_uri are never logged in
        # stdout (they may embed PII or URLs); only the non-secret error code,
        # the numeric error_codes and the correlation_id are surfaced.
        if isinstance(result, dict):
            safe = {k: result.get(k) for k in ("error", "error_codes", "correlation_id", "suberror")}
            safe = {k: v for k, v in safe.items() if v is not None}
            print(f"Notice: token exchange failed (no access_token). MSAL diagnostic: {safe}")
        else:
            print("Notice: token exchange failed (no access_token).")
        raise CallbackRejectedError(_MSG_EXCHANGE_FAILED)

    return result["access_token"], cache.serialize(), result


def fetch_user_profile(access_token: str) -> dict:
    """
    Call Microsoft Graph GET /me using the newly acquired access token.

    Only the fields needed for the user connection are consumed by callers.
    Raises CallbackRejectedError on transport/http failures.
    """
    try:
        response = requests.get(
            GRAPH_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
    except Exception as exc:
        print(f"Notice: Graph /me request failed: {type(exc).__name__}")
        raise CallbackRejectedError(_MSG_EXCHANGE_FAILED) from exc

    if response.status_code != 200:
        print(f"Notice: Graph /me returned HTTP {response.status_code}")
        raise CallbackRejectedError(_MSG_EXCHANGE_FAILED)

    try:
        return response.json()
    except ValueError as exc:
        raise CallbackRejectedError(_MSG_EXCHANGE_FAILED) from exc


def _reject_message(status_code: int, message: str) -> dict:
    """Build a safe, HTML-rendered rejection result with no sensitive data."""
    return {
        "success": False,
        "http_status": status_code,
        "content_type": "text/html; charset=utf-8",
        "message": message,
        "body": _render_html(False, message),
    }


def _success_message(message: str) -> dict:
    return {
        "success": True,
        "http_status": 200,
        "content_type": "text/html; charset=utf-8",
        "message": message,
        "body": _render_html(True, message),
    }


def _render_html(success: bool, message: str) -> str:
    """Minimal, dependency-free result page. Never receives credentials."""
    safe_message = html.escape(str(message))
    heading = "Connection successful" if success else "Connection not completed"
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{heading}</title></head>"
        "<body style=\"font-family:sans-serif;margin:2rem\">"
        f"<h2>{heading}</h2><p>{safe_message}</p></body></html>"
    )


def complete_oauth_callback(params: dict) -> dict:
    """
    Process a Microsoft OAuth callback.

    'params' is the flattened query-string mapping (e.g. {'code': ..., 'state': ...}
    or {'error': ..., 'state': ...}). Returns a dict containing a safe HTTP
    status, content type, user-facing message, and rendered HTML body.

    Rejection/replay guarantees:
      - Missing/unknown/tampered/expired/consumed state is rejected.
      - The session is consumed exactly once; successful completion and
        OAuth-error completion cannot be replayed.
      - No tokens, codes, or secrets ever reach the response body.
    """
    # Safe operational log line: NO query parameters or state are ever printed.
    print(f"OAuth callback received: {_now_iso()}")
    signed_state = params.get("state") or ""
    if not signed_state:
        return _reject_message(400, _MSG_STATE_MISSING)

    parts = split_signed_state(signed_state)
    if parts is None:
        return _reject_message(400, _MSG_STATE_INVALID)
    raw_state, signature = parts

    # Tamper detection before any database interaction.
    if not verify_state_signature(raw_state, signature):
        return _reject_message(400, _MSG_STATE_INVALID)

    session = get_oauth_session_by_state_hash(hash_state(raw_state))
    if session is None:
        return _reject_message(400, _MSG_SESSION_UNKNOWN)

    if session.get("consumed"):
        return _reject_message(400, _MSG_SESSION_USED)

    if not _session_is_valid(session):
        return _reject_message(400, _MSG_SESSION_EXPIRED)

    user_id = session.get("user_id")
    if not user_id:
        return _reject_message(400, _MSG_SESSION_UNKNOWN)

    session_id = session.get("id")
    if not session_id:
        return _reject_message(400, _MSG_SESSION_UNKNOWN)

    # Microsoft returned an error: consume the session, reply safely.
    if params.get("error"):
        consume_oauth_session(session_id)
        return _reject_message(200, _MSG_PROVIDER_ERROR)

    code = params.get("code")
    if not code:
        return _reject_message(400, _MSG_CODE_MISSING)

    # Redeem the code with the stored PKCE verifier and fetch /me.
    try:
        access_token, cache_serialized, result = exchange_auth_code(signed_state, session, code)
        profile = fetch_user_profile(access_token)
    except CallbackRejectedError as exc:
        consume_oauth_session(session_id)
        return _reject_message(400, str(exc))

    if not profile or not profile.get("id"):
        consume_oauth_session(session_id)
        return _reject_message(400, _MSG_EXCHANGE_FAILED)

    # Consume the session exactly once before finalizing the connection.
    consumed = consume_oauth_session(session_id)
    if consumed is None:
        return _reject_message(400, _MSG_SESSION_USED)

    claims = result.get("id_token_claims") or {}
    now = _now_iso()
    microsoft_data = {
        "connected": True,
        "microsoft_user_id": profile.get("id"),
        "display_name": profile.get("displayName"),
        "email": profile.get("mail") or profile.get("userPrincipalName"),
        "tenant_id": claims.get("tid"),
        "token_cache": cache_serialized,
        "token_refreshed_at": now,
        "connected_at": now,
    }

    try:
        update_user_microsoft(user_id, microsoft_data)
    except DuplicateKeyError:
        return _reject_message(409, _MSG_DUPLICATE)
    except Exception as exc:
        print(f"Notice: storing Microsoft connection failed: {type(exc).__name__}")
        return _reject_message(500, _MSG_SAVE_FAILED)

    return _success_message(_MSG_SUCCESS)


# ---------------------------------------------------------------------------
# Lightweight local callback server (stdlib only; usable now, deployable later).
# ---------------------------------------------------------------------------


def _callback_path(redirect_uri: str) -> str:
    """Extract the path from the configured redirect URI (e.g. /auth/callback)."""
    return urlsplit(redirect_uri).path or "/"


def make_callback_handler(callback_path: str):
    """
    Build a BaseHTTPRequestHandler subclass serving GET <callback_path>.

    Non-matching paths return 404. Query strings are never logged so an
    authorization code arriving via '?code=...' stays out of logs.
    """

    class OAuthCallbackHTTPHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib naming)
            parsed = urlsplit(self.path)
            if parsed.path != callback_path:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not Found")
                return

            params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            result = complete_oauth_callback(params)
            body = result["body"].encode("utf-8")

            self.send_response(result["http_status"])
            self.send_header("Content-Type", result["content_type"])
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # noqa: A003
            # Deliberately silent: request lines contain the query string.
            return

    return OAuthCallbackHTTPHandler


class OAuthCallbackServer:
    """
    Programmatic lifecycle for the local OAuth callback server.

    host/port/redirect_uri default to configuration; the server binds on start()
    (port 0 selects an ephemeral port, readable via .port).
    """

    def __init__(self, host: str | None = None, port: int | None = None, redirect_uri: str | None = None):
        self.host = host or config.OAUTH_HOST
        self.port = port if port is not None else config.OAUTH_LISTEN_PORT
        self.redirect_uri = redirect_uri or config.OAUTH_REDIRECT_URI
        self.path = _callback_path(self.redirect_uri)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        """Resolve the actual bound port (useful when initialized with 0)."""
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        self._port = int(value)

    def start(self) -> "OAuthCallbackServer":
        if self._httpd is not None:
            return self
        handler = make_callback_handler(self.path)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="reelforge-oauth-callback",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None

    def __enter__(self) -> "OAuthCallbackServer":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()


def main() -> None:
    """Run the callback server in the foreground (e.g. `python -m auth.oauth_server`)."""
    require_state_secret()
    server = OAuthCallbackServer().start()
    print(
        f"OAuth callback server listening on http://{server.host}:{server.port}{server.path}\n"
        "Waiting for Microsoft redirects... (Ctrl+C to stop)"
    )
    try:
        if server._httpd is not None:
            server._httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping OAuth callback server.")
    finally:
        server.stop()


if __name__ == "__main__":
    main()