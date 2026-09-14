"""
storage/oauth_session.py
------------------------
OAuth state/session persistence for the ReelForge V3.3 Microsoft connection flow.

Layer 1 establishes ONLY the storage mechanism. State generation, PKCE
verification, and the callback endpoint are implemented in later layers.

Design rules:
  - Sessions are bound to a ReelForge user_id (usr_...).
  - Session IDs and state references are unguessable.
  - Raw OAuth state strings and authorization codes are NEVER stored.
    Only a SHA-256 hash of the state is persisted.
  - code_verifier (for PKCE) is stored per session and never logged.
  - The non-secret MSAL flow fields are stored verbatim so the callback can
    reconstruct the exact flow that MSAL returns from initiate_auth_code_flow:
      * nonce            – raw MSAL OIDC nonce (required by MSAL's OIDC
                           validation on the success path; not a credential).
      * scope            – the decorated scope set (includes openid/profile/
                           offline_access appended by MSAL), preserved so the
                           token request keeps the offline_access capabilities.
      * claims_challenge – optional claims challenge dictionary when the app
                           requests claims (may apply during provisioning).
  - Authorization codes, access/refresh tokens and serialized token caches are
    NEVER stored in oauth_sessions.
  - TTL index on expires_at removes stale sessions automatically; an explicit
    cleanup helper is also provided.
  - No credential material is ever written into Brain Objects.
"""

from __future__ import annotations

import hashlib
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from storage.db import ensure_oauth_session_indexes, get_collection
except ImportError:
    ensure_oauth_session_indexes = None
    get_collection = None

# Global flag to ensure OAuth session indexes are initialized only once
_oauth_indexes_initialized: bool = False

DEFAULT_TTL_SECONDS = 600  # 10 minutes


def _get_oauth_col() -> Any | None:
    """
    Retrieve the MongoDB 'oauth_sessions' collection instance, or None if unavailable.
    Lazily initializes indexes on first call.
    """
    global _oauth_indexes_initialized
    if get_collection is None:
        return None
    try:
        col = get_collection("oauth_sessions")
        if not _oauth_indexes_initialized and ensure_oauth_session_indexes is not None:
            ensure_oauth_session_indexes(col)
            _oauth_indexes_initialized = True
        return col
    except Exception:
        return None


def _now_utc() -> datetime:
    """Return current aware UTC datetime (BSON date for TTL index)."""
    return datetime.now(timezone.utc)


def hash_state(state: str) -> str:
    """
    Return a SHA-256 hex digest of an OAuth state string.

    Only the digest is ever persisted. Raises ValueError on empty input.
    """
    if not state or not isinstance(state, str):
        raise ValueError("state must be a non-empty string")
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def create_oauth_session(
    user_id: str,
    state_hash: str,
    code_verifier: str | None = None,
    nonce: str | None = None,
    scope: list | None = None,
    claims_challenge: dict | str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str | None:
    """
    Persist a new OAuth session bound to a ReelForge user.

    Returns the unguessable session id string, or None if storage is unavailable.

    Args:
        user_id: canonical ReelForge user_id ('usr_...').
        state_hash: SHA-256 hex digest of the OAuth state (never the raw state).
        code_verifier: PKCE code verifier used at callback time (optional in Layer 1).
        nonce: raw MSAL OIDC nonce from initiate_auth_code_flow(). Stored (not a
            credential) so the callback can reconstruct the flow MSAL expects.
        scope: the decorated scope list from initiate_auth_code_flow() (includes
            openid/profile/offline_access). Preserved verbatim so the token
            request keeps those capabilities.
        claims_challenge: optional claims challenge from flow['claims_challenge'].
        ttl_seconds: lifetime of the session; the document expires afterwards.

    Never stores: raw state, authorization codes, access/refresh tokens, or
    serialized token caches.
    """
    if not user_id or not isinstance(user_id, str):
        raise ValueError("user_id must be a non-empty string")
    if not state_hash or not isinstance(state_hash, str):
        raise ValueError("state_hash must be a non-empty string")

    col = _get_oauth_col()
    if col is None:
        return None

    session_id = secrets.token_urlsafe(32)
    now = _now_utc()
    doc = {
        "_id": session_id,
        "id": session_id,
        "user_id": user_id,
        "state_hash": state_hash,
        "code_verifier": code_verifier,
        "nonce": nonce,
        "scope": scope,
        "claims_challenge": claims_challenge,
        "created_at": now,
        "expires_at": now + timedelta(seconds=max(ttl_seconds, 0)),
        "consumed": False,
    }

    try:
        col.insert_one(doc)
    except Exception as e:
        print(f"Notice: create_oauth_session failed: {e}")
        return None

    return session_id


def _clean_session_doc(doc: dict | None) -> dict | None:
    """Strip internal '_id' while preserving the public 'id' field."""
    if not doc or not isinstance(doc, dict):
        return None
    cleaned = dict(doc)
    cleaned.pop("_id", None)
    return cleaned


def get_oauth_session(session_id: str) -> dict | None:
    """
    Return an OAuth session by its session id, or None if missing/expired/consumed.
    Never exposes raw tokens; the stored state_hash may not equal the input only
    because callers compare hashes themselves.
    """
    if not session_id or not isinstance(session_id, str):
        return None

    col = _get_oauth_col()
    if col is None:
        return None

    try:
        doc = col.find_one({"_id": session_id})
        return _clean_session_doc(doc)
    except Exception as e:
        print(f"Notice: get_oauth_session failed: {e}")
        return None


def get_oauth_session_by_state_hash(state_hash: str) -> dict | None:
    """Return an OAuth session by its stored state hash (unique index backed)."""
    if not state_hash or not isinstance(state_hash, str):
        return None

    col = _get_oauth_col()
    if col is None:
        return None

    try:
        doc = col.find_one({"state_hash": state_hash})
        return _clean_session_doc(doc)
    except Exception as e:
        print(f"Notice: get_oauth_session_by_state_hash failed: {e}")
        return None


def consume_oauth_session(session_id: str) -> dict | None:
    """
    Atomically mark an OAuth session as consumed (single-use).

    Returns the updated session dict, or None if the session does not exist.
    """
    if not session_id or not isinstance(session_id, str):
        return None

    col = _get_oauth_col()
    if col is None:
        return None

    try:
        doc = col.find_one_and_update(
            {"_id": session_id, "consumed": False},
            {"$set": {"consumed": True}},
            return_document=True,
        )
        return _clean_session_doc(doc)
    except Exception as e:
        print(f"Notice: consume_oauth_session failed: {e}")
        return None


def cleanup_expired_oauth_sessions() -> int:
    """
    Manually delete expired OAuth sessions (expires_at in the past).

    The TTL index also handles automatic cleanup; this helper gives immediate
    cleanup for tests and for callers that need deterministic behavior.
    Returns the number of sessions removed.
    """
    col = _get_oauth_col()
    if col is None:
        return 0

    try:
        result = col.delete_many({"expires_at": {"$lt": _now_utc()}})
        return result.deleted_count
    except Exception as e:
        print(f"Notice: cleanup_expired_oauth_sessions failed: {e}")
        return 0