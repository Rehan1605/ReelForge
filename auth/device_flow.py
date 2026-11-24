"""
auth/device_flow.py
-------------------
Multi-user Microsoft OAuth Device Code Flow (RFC 8628) for ReelForge.

Replaces public OAuth callback requirements with direct device code authentication:
- Generates user-code and verification URL via MSAL initiate_device_flow()
- Performs non-blocking single-step polling via exit_condition callback
- Isolates MSAL client instances and token caches per user
- Fetches Microsoft Graph /me profile on completion
- Persists credentials securely to the user's MongoDB record
- Preserves existing unique Microsoft account linking protection

Security invariants:
- Access tokens, refresh tokens, token caches, device codes, and raw responses
  are NEVER logged and never exposed in error messages.
- Polling is non-blocking (single-step execution delegated via asyncio.to_thread).
- Token caches are instantiated freshly per flow and never shared between users.
- The same Microsoft account cannot be linked to two different ReelForge users.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import config
import msal
import requests
from msal import SerializableTokenCache
from onenote.graph_client import GRAPH_SCOPES
from pymongo.errors import DuplicateKeyError
from storage.user import get_user_by_id, update_user_microsoft

logger = logging.getLogger(__name__)

# Delegated permissions required for ReelForge OneNote publishing.
# 'offline_access', 'openid', and 'profile' are appended automatically by MSAL.
MICROSOFT_SCOPES = list(GRAPH_SCOPES)  # ['User.Read', 'Notes.ReadWrite']

# Microsoft Graph endpoint for user profile retrieval
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"

# User-facing standard status/error messages
MSG_DUPLICATE_ACCOUNT = "This Microsoft account is already connected to another ReelForge user."
MSG_CONNECTION_FAILED = "Could not complete the Microsoft connection. Please try again."
MSG_PROFILE_FAILED = "Could not retrieve Microsoft account profile."


class DeviceFlowError(Exception):
    """Base exception for Device Code Flow failures."""


class DeviceFlowDuplicateAccountError(DeviceFlowError):
    """Raised when the Microsoft account is already linked to another ReelForge user."""


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_device_flow_client(
    token_cache: SerializableTokenCache | None = None,
) -> msal.PublicClientApplication:
    """
    Construct a fresh public client application for Device Code Flow.
    Never accepts or uses client secrets.
    """
    return msal.PublicClientApplication(
        config.MICROSOFT_CLIENT_ID,
        authority=config.MICROSOFT_AUTHORITY,
        token_cache=token_cache,
    )


def initiate_device_flow_for_user(
    user_id: str,
    client: msal.PublicClientApplication | None = None,
    cache: SerializableTokenCache | None = None,
) -> tuple[dict[str, Any], SerializableTokenCache, msal.PublicClientApplication]:
    """
    Initiate Microsoft Device Code Flow for a specific ReelForge user.

    Args:
        user_id: Canonical ReelForge user identifier ('usr_...').
        client: Optional pre-constructed PublicClientApplication (used in tests).
        cache: Optional pre-constructed SerializableTokenCache (used in tests).

    Returns:
        tuple (flow_dict, token_cache, public_client)
        where flow_dict contains:
          - 'user_code': Code the user enters on microsoft.com/devicelogin
          - 'device_code': Verification identifier used by polling
          - 'verification_uri': Microsoft URL for user verification
          - 'verification_uri_complete': Optional URL with pre-filled code
          - 'expires_in': Flow lifetime in seconds (typically 900)
          - 'expires_at': Calculated epoch expiry timestamp
          - 'interval': Polling interval in seconds (typically 5)
          - 'message': User instructions text from Microsoft

    Raises:
        ValueError: If user_id is unknown or invalid.
        DeviceFlowError: If MSAL fails to initiate the device flow.
    """
    if not user_id or not isinstance(user_id, str):
        raise ValueError("A valid user_id string is required.")

    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError(f"Unknown ReelForge user_id: {user_id}")

    token_cache = cache if cache is not None else SerializableTokenCache()
    public_client = client if client is not None else _build_device_flow_client(token_cache=token_cache)

    try:
        flow = public_client.initiate_device_flow(scopes=MICROSOFT_SCOPES)
    except Exception as exc:
        logger.warning("Device flow initiation network exception: %s", type(exc).__name__)
        raise DeviceFlowError("Failed to initiate Microsoft device flow.") from exc

    if not isinstance(flow, dict) or "user_code" not in flow:
        error_code = flow.get("error") if isinstance(flow, dict) else "unknown"
        logger.warning("Device flow initiation rejected by identity provider: %s", error_code)
        raise DeviceFlowError(f"Microsoft device flow rejected: {error_code}")

    return flow, token_cache, public_client


def poll_device_flow_step(
    client: msal.PublicClientApplication,
    flow: dict[str, Any],
) -> dict[str, Any]:
    """
    Perform a single non-blocking polling request to Microsoft's token endpoint.

    Uses exit_condition=lambda f: True to force MSAL's acquire_token_by_device_flow
    to run exactly one round without blocking the calling thread.

    Returns:
        dict: MSAL token result or error dict:
          - If successful: contains 'access_token', 'refresh_token', etc.
          - If still waiting: {'error': 'authorization_pending'}
          - If polling too fast: {'error': 'slow_down'} (MSAL automatically increments flow['interval'])
          - If expired: {'error': 'expired_token'}
          - If user denied: {'error': 'access_denied'}
    """
    if not isinstance(flow, dict) or "device_code" not in flow:
        return {"error": "invalid_flow", "error_description": "Invalid device flow object."}

    try:
        return client.acquire_token_by_device_flow(
            flow,
            exit_condition=lambda f: True,
        )
    except Exception as exc:
        logger.warning("Device flow single-step polling exception: %s", type(exc).__name__)
        return {"error": "polling_exception", "error_description": str(type(exc).__name__)}


def fetch_user_profile(access_token: str) -> dict[str, Any]:
    """
    Call Microsoft Graph GET /me using the newly acquired access token.

    Never logs tokens or sensitive headers.
    Returns:
        dict: Parsed JSON response containing 'id', 'displayName', 'mail', etc.
    Raises:
        DeviceFlowError: If the HTTP request fails or returns non-200.
    """
    if not access_token or not isinstance(access_token, str):
        raise DeviceFlowError("A valid access_token is required to fetch profile.")

    try:
        response = requests.get(
            GRAPH_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
    except Exception as exc:
        logger.warning("Graph /me request failed: %s", type(exc).__name__)
        raise DeviceFlowError(MSG_PROFILE_FAILED) from exc

    if response.status_code != 200:
        logger.warning("Graph /me returned non-200 status: %d", response.status_code)
        raise DeviceFlowError(MSG_PROFILE_FAILED)

    try:
        data = response.json()
        if not isinstance(data, dict) or not data.get("id"):
            raise DeviceFlowError("Profile response missing Microsoft user ID.")
        return data
    except (ValueError, TypeError) as exc:
        raise DeviceFlowError("Invalid JSON from Microsoft Graph /me") from exc


def complete_device_flow_connection(
    user_id: str,
    cache: SerializableTokenCache,
    result: dict[str, Any],
    profile_fetcher: Any = None,
) -> dict[str, Any]:
    """
    Finalize successful authentication: extract profile, serialize cache,
    and persist the Microsoft connection record into MongoDB Atlas.

    Args:
        user_id: ReelForge user_id ('usr_...').
        cache: The SerializableTokenCache holding the acquired tokens.
        result: The successful token dict from acquire_token_by_device_flow.
        profile_fetcher: Optional callable for testing (defaults to fetch_user_profile).

    Returns:
        dict: The updated 'microsoft' connection subdocument.

    Raises:
        DeviceFlowDuplicateAccountError: If the Microsoft account is already
            connected to another ReelForge user.
        DeviceFlowError: If profile retrieval or database persistence fails.
    """
    access_token = result.get("access_token")
    if not access_token:
        raise DeviceFlowError("Cannot complete connection without a valid access_token.")

    fetcher = profile_fetcher if profile_fetcher is not None else fetch_user_profile
    profile = fetcher(access_token)

    ms_user_id = profile.get("id")
    if not ms_user_id:
        raise DeviceFlowError("Microsoft profile missing required user identifier.")

    claims = result.get("id_token_claims") or {}
    now = _now_iso()
    cache_serialized = cache.serialize()

    microsoft_data = {
        "connected": True,
        "microsoft_user_id": ms_user_id,
        "display_name": profile.get("displayName"),
        "email": profile.get("mail") or profile.get("userPrincipalName"),
        "tenant_id": claims.get("tid"),
        "token_cache": cache_serialized,
        "token_refreshed_at": now,
        "connected_at": now,
    }

    try:
        updated = update_user_microsoft(user_id, microsoft_data)
        if updated is None:
            raise DeviceFlowError("User record not found during connection persistence.")
        return updated
    except DuplicateKeyError as exc:
        logger.info("Connection rejected: Microsoft account %s already linked.", ms_user_id)
        raise DeviceFlowDuplicateAccountError(MSG_DUPLICATE_ACCOUNT) from exc
    except Exception as exc:
        logger.warning("Persistence error updating Microsoft connection: %s", type(exc).__name__)
        raise DeviceFlowError(MSG_CONNECTION_FAILED) from exc
