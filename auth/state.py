"""
auth/state.py
-------------
Cryptographic primitives for the ReelForge V3.3 Microsoft OAuth flow:

- Secure, unguessable OAuth state generation.
- RFC 7636 PKCE code_verifier generation and S256 code_challenge.
- HMAC-SHA256 signing/verification of OAuth state via OAUTH_STATE_SECRET.
- Signed-state composition/parsing helpers.

Security invariants:
  - Only cryptographically secure randomness is used (secrets module).
  - Raw state is never persisted; storage layers persist hash_state(state).
  - Raw state is never logged.
  - The PKCE verifier is never logged and only stored inside the OAuth session.
  - User IDs or other sensitive identity data are never embedded in the raw
    state; user binding is provided by the OAuth session record.
  - OAUTH_STATE_SECRET is mandatory for operational signing; no hardcoded
    fallback exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import config

# Number of random bytes backing the OAuth state (token_urlsafe(32) == 43+ chars).
_OAUTH_STATE_BYTES = 32
# Number of random bytes backing the PKCE verifier (43-128 chars per RFC 7636).
_PKCE_VERIFIER_BYTES = 32

# Separator between the raw state and its HMAC signature inside a signed token.
# token_urlsafe() output is restricted to base64url characters (A-Za-z0-9-_),
# so a '.' can never collide with the state itself.
_STATE_SIGNATURE_SEPARATOR = "."

# OAuth state session lifetime in seconds (matches storage.oauth_session default).
OAUTH_SESSION_TTL_SECONDS = 600


def generate_state() -> str:
    """
    Return a fresh, unguessable OAuth state string.

    The state is a random token with no relation to the ReelForge user id or any
    other identity data. Callers sign it with sign_state() before use.
    """
    return secrets.token_urlsafe(_OAUTH_STATE_BYTES)


def generate_pkce_verifier() -> str:
    """
    Return a cryptographically random PKCE code_verifier.

    RFC 7636 requires 43-128 characters drawn from the 'unreserved' set
    [A-Za-z0-9-._~]. secrets.token_urlsafe() yields A-Za-z0-9-_ (43 chars for
    32 bytes), a strict subset of the unreserved set, so the result is
    compliant.
    """
    return secrets.token_urlsafe(_PKCE_VERIFIER_BYTES)


def pkce_s256_challenge(verifier: str) -> str:
    """
    Compute the RFC 7636 S256 code_challenge for a code_verifier.

    challenge = base64url(sha256(ascii(verifier))) with trailing '=' padding
    stripped, as required by the PKCE spec.

    Raises ValueError for empty/non-string verifiers.
    """
    if not verifier or not isinstance(verifier, str):
        raise ValueError("code_verifier must be a non-empty string")
    digest = hashlib.sha256(verifier.encode("ascii", errors="strict")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def require_state_secret() -> str:
    """
    Return the configured OAUTH_STATE_SECRET or raise a clear RuntimeError.

    The secret is mandatory for actual OAuth operation; there is intentionally
    no hardcoded fallback. Callers must generate one and set it in the
    environment (see .env.example).
    """
    secret = config.OAUTH_STATE_SECRET
    if not secret or not isinstance(secret, str) or not secret.strip():
        raise RuntimeError(
            "OAUTH_STATE_SECRET is not configured. It is required to sign OAuth "
            "state. Generate one with: python -c \"import secrets; "
            "print(secrets.token_hex(32))\" and add it to your environment."
        )
    return secret.strip()


def sign_state(state: str) -> str:
    """
    Return the HMAC-SHA256 signature (hex) of the OAuth state using
    OAUTH_STATE_SECRET.

    This binds every state token to the server secret so tampered or
    attacker-injected state is rejected before any database lookup.
    """
    if not state or not isinstance(state, str):
        raise ValueError("state must be a non-empty string")
    secret = require_state_secret()
    return hmac.new(
        secret.encode("utf-8"),
        state.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def compose_signed_state(state: str) -> str:
    """Wrap a raw state with its server signature: '<state>.<signature>'."""
    if not state or not isinstance(state, str):
        raise ValueError("state must be a non-empty string")
    return f"{state}{_STATE_SIGNATURE_SEPARATOR}{sign_state(state)}"


def split_signed_state(signed_state: str) -> tuple[str, str] | None:
    """
    Split '<raw>.<signature>' into (raw_state, signature), or None if malformed.

    The raw state cannot contain the separator (token_urlsafe charset), so
    rsplit on the last separator uniquely identifies the signature tail.
    """
    if not signed_state or not isinstance(signed_state, str):
        return None
    if _STATE_SIGNATURE_SEPARATOR not in signed_state:
        return None
    raw_state, signature = signed_state.rsplit(_STATE_SIGNATURE_SEPARATOR, 1)
    if not raw_state or not signature:
        return None
    return raw_state, signature


def verify_state_signature(state: str, signature: str) -> bool:
    """
    Constant-time verification that 'signature' matches sign_state(state).

    Returns False (never raises) when the secret is missing or inputs are
    malformed, so callback handling can reject safely.
    """
    if not state or not isinstance(state, str) or not signature:
        return False
    try:
        expected = sign_state(state)
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, signature)