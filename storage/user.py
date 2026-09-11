"""
storage/user.py
---------------
Multi-user management, identity resolution, and profile persistence for ReelForge V3.

Manages user registration, Telegram identity mapping, activity timestamps,
and profile stats in MongoDB Atlas.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError
    from storage.db import ensure_user_indexes, get_collection
except ImportError:
    ReturnDocument = None
    DuplicateKeyError = None
    ensure_user_indexes = None
    get_collection = None

# Global flag to ensure MongoDB collection indexes are initialized only once
_user_indexes_initialized: bool = False


def _generate_user_id() -> str:
    """Generate a canonical, URL-safe, prefix-tagged ReelForge user identifier."""
    return f"usr_{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_users_col() -> Any | None:
    """
    Retrieve the MongoDB 'users' collection instance, or None if unavailable.
    Lazily initializes indexes on first call.
    """
    global _user_indexes_initialized
    if get_collection is None:
        return None
    try:
        col = get_collection("users")
        if not _user_indexes_initialized and ensure_user_indexes is not None:
            ensure_user_indexes(col)
            _user_indexes_initialized = True
        return col
    except Exception:
        return None


def _clean_user_doc(doc: dict | None) -> dict | None:
    """
    Sanitize a MongoDB user document for consumption:
    - Strips MongoDB internal '_id' or ensures string serialization
    - Guarantees standard 'user_id' field is present
    """
    if not doc or not isinstance(doc, dict):
        return None

    cleaned = dict(doc)
    mongo_id = cleaned.pop("_id", None)
    if not cleaned.get("user_id") and mongo_id:
        cleaned["user_id"] = str(mongo_id)

    return cleaned


def _extract_telegram_info(telegram_user: Any) -> dict[str, Any]:
    """
    Safely extract standard fields from a python-telegram-bot User object or dictionary.
    """
    if isinstance(telegram_user, dict):
        t_id = telegram_user.get("id")
        username = telegram_user.get("username")
        first_name = telegram_user.get("first_name") or ""
        last_name = telegram_user.get("last_name")
        language_code = telegram_user.get("language_code")
        is_bot = bool(telegram_user.get("is_bot", False))
    else:
        t_id = getattr(telegram_user, "id", None)
        username = getattr(telegram_user, "username", None)
        first_name = getattr(telegram_user, "first_name", "") or ""
        last_name = getattr(telegram_user, "last_name", None)
        language_code = getattr(telegram_user, "language_code", None)
        is_bot = bool(getattr(telegram_user, "is_bot", False))

    if t_id is None:
        raise ValueError("Invalid Telegram user: missing 'id'.")

    # Clean username: strip '@' if present
    clean_username = username.strip().lstrip("@") if username else None

    return {
        "id": int(t_id),
        "username": clean_username,
        "first_name": str(first_name).strip(),
        "last_name": str(last_name).strip() if last_name else None,
        "language_code": str(language_code).strip() if language_code else None,
        "is_bot": is_bot,
    }


def get_user_by_telegram_id(telegram_id: int | str) -> dict | None:
    """
    Look up an existing ReelForge user document by Telegram ID.
    Returns cleaned user dict or None if not found or DB unavailable.
    """
    try:
        t_id_int = int(telegram_id)
    except (ValueError, TypeError):
        return None

    col = _get_users_col()
    if col is None:
        return None

    try:
        doc = col.find_one({"telegram.id": t_id_int})
        return _clean_user_doc(doc)
    except Exception as e:
        print(f"Notice: MongoDB lookup by telegram.id failed: {e}")
        return None


def get_user_by_id(user_id: str) -> dict | None:
    """
    Look up an existing ReelForge user document by canonical user_id (e.g. 'usr_...').
    """
    if not user_id or not isinstance(user_id, str):
        return None

    col = _get_users_col()
    if col is None:
        return None

    try:
        doc = col.find_one({"_id": user_id}) or col.find_one({"user_id": user_id})
        return _clean_user_doc(doc)
    except Exception as e:
        print(f"Notice: MongoDB lookup by user_id failed: {e}")
        return None


def create_user_from_telegram(telegram_user: Any, role: str = "user") -> dict:
    """
    Initialize and persist a new ReelForge user document from a Telegram User object.
    """
    t_info = _extract_telegram_info(telegram_user)
    user_id = _generate_user_id()
    now = _now_iso()

    display_name = t_info["first_name"] or t_info["username"] or "User"

    user_doc = {
        "_id": user_id,
        "user_id": user_id,
        "status": "active",
        "role": role,
        "telegram": t_info,
        "profile": {
            "display_name": display_name,
        },
        "settings": {
            "preferred_modality": "auto",
            "notifications_enabled": True,
        },
        "stats": {
            "reels_saved": 0,
            "reels_processed": 0,
            "last_active_at": now,
        },
        "timestamps": {
            "created_at": now,
            "updated_at": now,
            "last_seen_at": now,
        },
    }

    col = _get_users_col()
    if col is not None:
        try:
            col.insert_one(user_doc)
        except Exception as e:
            # Handle possible race condition if user registered concurrently
            if DuplicateKeyError and isinstance(e, DuplicateKeyError):
                existing = get_user_by_telegram_id(t_info["id"])
                if existing:
                    return existing
            raise

    return _clean_user_doc(user_doc)


def touch_user_activity(user_id: str, telegram_user: Any = None) -> dict | None:
    """
    Update last_seen_at timestamp and sync any changed Telegram profile attributes
    (e.g., username change, name change).
    """
    if not user_id or not isinstance(user_id, str):
        return None

    col = _get_users_col()
    if col is None:
        return None

    now = _now_iso()
    update_fields: dict[str, Any] = {
        "timestamps.last_seen_at": now,
        "stats.last_active_at": now,
    }

    if telegram_user is not None:
        try:
            t_info = _extract_telegram_info(telegram_user)
            update_fields["telegram.username"] = t_info["username"]
            update_fields["telegram.first_name"] = t_info["first_name"]
            update_fields["telegram.last_name"] = t_info["last_name"]
            update_fields["telegram.language_code"] = t_info["language_code"]
            if t_info["first_name"]:
                update_fields["profile.display_name"] = t_info["first_name"]
        except Exception:
            pass

    try:
        doc = col.find_one_and_update(
            {"_id": user_id},
            {"$set": update_fields},
            return_document=ReturnDocument.AFTER if ReturnDocument else True,
        )
        if doc is None:
            doc = col.find_one_and_update(
                {"user_id": user_id},
                {"$set": update_fields},
                return_document=ReturnDocument.AFTER if ReturnDocument else True,
            )
        return _clean_user_doc(doc)
    except Exception as e:
        print(f"Notice: touch_user_activity failed: {e}")
        return None


def get_or_create_user(telegram_user: Any) -> tuple[dict, bool]:
    """
    Resolve a Telegram user to a ReelForge account.
    
    Returns:
        tuple (user_dict, is_new_user: bool)
    """
    t_info = _extract_telegram_info(telegram_user)
    t_id = t_info["id"]

    existing = get_user_by_telegram_id(t_id)
    if existing:
        updated = touch_user_activity(existing["user_id"], telegram_user)
        return (updated or existing), False

    # New user: create account
    new_user = create_user_from_telegram(telegram_user)
    return new_user, True


def update_user_stats(
    user_id: str,
    reels_saved_delta: int = 0,
    reels_processed_delta: int = 0,
) -> dict | None:
    """
    Atomically increment or decrement user metrics.
    """
    if not user_id or not isinstance(user_id, str):
        return None

    col = _get_users_col()
    if col is None:
        return None

    inc_fields = {}
    if reels_saved_delta != 0:
        inc_fields["stats.reels_saved"] = reels_saved_delta
    if reels_processed_delta != 0:
        inc_fields["stats.reels_processed"] = reels_processed_delta

    if not inc_fields:
        return get_user_by_id(user_id)

    now = _now_iso()
    try:
        doc = col.find_one_and_update(
            {"_id": user_id},
            {
                "$inc": inc_fields,
                "$set": {"timestamps.updated_at": now, "stats.last_active_at": now},
            },
            return_document=ReturnDocument.AFTER if ReturnDocument else True,
        )
        if doc is None:
            doc = col.find_one_and_update(
                {"user_id": user_id},
                {
                    "$inc": inc_fields,
                    "$set": {"timestamps.updated_at": now, "stats.last_active_at": now},
                },
                return_document=ReturnDocument.AFTER if ReturnDocument else True,
            )
        return _clean_user_doc(doc)
    except Exception as e:
        print(f"Notice: update_user_stats failed: {e}")
        return None
