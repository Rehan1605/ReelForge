"""
storage/db.py
-------------
MongoDB Atlas database client management for ReelForge V3.

Provides lazy, connection-pooled PyMongo client access with fail-fast timeouts,
collection helpers, safe URI masking, and connectivity validation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import ConnectionFailure, ConfigurationError, PyMongoError

from config import MONGODB_DB_NAME, MONGODB_URI

if TYPE_CHECKING:
    pass

# Global singleton client instance
_client: MongoClient | None = None

# Sensible timeout defaults (milliseconds)
SERVER_SELECTION_TIMEOUT_MS = 5000
CONNECT_TIMEOUT_MS = 5000
SOCKET_TIMEOUT_MS = 10000


def mask_mongo_uri(uri: str) -> str:
    """
    Safely mask username and password credentials from a MongoDB URI for logging/display.
    Example: mongodb+srv://user:pass@cluster.mongodb.net -> mongodb+srv://****:****@cluster.mongodb.net
    """
    if not uri or not isinstance(uri, str):
        return "<not-configured>"

    try:
        # Regex replacement for standard mongo URI auth section: ://user:pass@ -> ://****:****@
        masked = re.sub(r"://([^:]+):([^@]+)@", r"://****:****@", uri)
        if masked != uri:
            return masked

        # If user only with no password: ://user@ -> ://****@
        masked_user = re.sub(r"://([^@]+)@", r"://****@", uri)
        return masked_user
    except Exception:
        return "<masked-uri>"


def get_mongo_client(uri: str | None = None, force_reconnect: bool = False) -> MongoClient:
    """
    Retrieve or initialize the singleton PyMongo MongoClient instance.
    Lazy initialization with connection pooling and fail-fast timeouts.
    """
    global _client

    target_uri = (uri or MONGODB_URI).strip()

    if not target_uri:
        raise ValueError(
            "MongoDB URI is not configured. Please set MONGODB_URI in .env or atlas-credentials.env"
        )

    if _client is None or force_reconnect:
        if _client is not None and force_reconnect:
            try:
                _client.close()
            except Exception:
                pass

        _client = MongoClient(
            target_uri,
            serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
            connectTimeoutMS=CONNECT_TIMEOUT_MS,
            socketTimeoutMS=SOCKET_TIMEOUT_MS,
            maxPoolSize=20,
            minPoolSize=1,
            retryWrites=True,
            appname="ReelForge-V3",
        )

    return _client


def get_database(db_name: str | None = None) -> Database:
    """
    Get the MongoDB Database instance. Defaults to config.MONGODB_DB_NAME ('reelforge').
    """
    client = get_mongo_client()
    target_db_name = (db_name or MONGODB_DB_NAME or "reelforge").strip()
    return client[target_db_name]


def get_collection(collection_name: str = "brains", db_name: str | None = None) -> Collection:
    """
    Get a MongoDB Collection instance from the target database.
    """
    db = get_database(db_name)
    return db[collection_name]


def ensure_user_indexes(col: Collection | None = None) -> None:
    """
    Ensure required performance and uniqueness indexes exist on the MongoDB users collection.
    Executes safely and idempotently.
    """
    if col is None:
        try:
            col = get_collection("users")
        except Exception:
            return

    try:
        col.create_index([("telegram.id", 1)], unique=True, sparse=True, name="idx_telegram_id_unique")
        col.create_index([("user_id", 1)], unique=True, name="idx_user_id_unique")
        col.create_index([("status", 1)], name="idx_status")
        col.create_index([("timestamps.created_at", -1)], name="idx_user_created_at")
    except Exception:
        # Non-fatal if index creation fails due to permissions or cluster state
        pass


def ensure_brain_indexes(col: Collection | None = None) -> None:
    """
    Ensure required performance, ownership, and query indexes exist on the MongoDB brains collection.
    Executes safely and idempotently.
    """
    if col is None:
        try:
            col = get_collection("brains")
        except Exception:
            return

    try:
        col.create_index([("id", 1)], name="idx_reel_id")
        col.create_index([("is_archived", 1)], name="idx_is_archived")
        col.create_index([("timestamps.processed_at", -1)], name="idx_processed_at")
        col.create_index([("knowledge.category", 1)], name="idx_category")
        col.create_index([("creator.username", 1)], name="idx_creator_username")
        col.create_index([("knowledge.tags", 1)], name="idx_tags")
        col.create_index(
            [("is_archived", 1), ("timestamps.processed_at", -1)],
            name="idx_archived_processed_at",
        )
        col.create_index([("ownership.saved_by", 1)], name="idx_ownership_saved_by")
        col.create_index([("ownership.created_by", 1)], name="idx_ownership_created_by")
        col.create_index(
            [("ownership.saved_by", 1), ("ownership.archived_by", 1), ("timestamps.processed_at", -1)],
            name="idx_user_library_active",
        )
    except Exception:
        pass


def close_mongo_client() -> None:
    """
    Cleanly close the MongoDB client connection pool.
    """
    global _client
    if _client is not None:
        try:
            _client.close()
        finally:
            _client = None


def is_mongo_available() -> bool:
    """
    Quick non-throwing boolean check to verify whether MongoDB Atlas is configured and reachable.
    """
    if not MONGODB_URI:
        return False
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        return True
    except Exception:
        return False


def test_connection(verbose: bool = True) -> tuple[bool, str]:
    """
    Verify MongoDB Atlas connectivity, execute a ping command, and report status.
    Guarantees no raw passwords or secret tokens are leaked in output.
    """
    masked_uri = mask_mongo_uri(MONGODB_URI)

    if not MONGODB_URI:
        msg = "MongoDB connection check skipped: MONGODB_URI is not set."
        if verbose:
            print(f"[FAIL] {msg}")
        return False, msg

    try:
        client = get_mongo_client(force_reconnect=True)
        # The ping command is cheap and does not require auth on the admin DB in most clusters
        ping_result = client.admin.command("ping")

        db_name = MONGODB_DB_NAME or "reelforge"
        db = client[db_name]
        collections = db.list_collection_names()

        msg = (
            f"Successfully connected to MongoDB Atlas!\n"
            f"  URI: {masked_uri}\n"
            f"  Database: {db_name}\n"
            f"  Ping response: {ping_result.get('ok', 1.0)}\n"
            f"  Existing collections: {collections if collections else '[] (Empty / Ready)'}"
        )

        if verbose:
            print(f"[OK] {msg}")

        return True, msg

    except ConfigurationError as ce:
        msg = f"MongoDB Configuration Error: {ce}"
        if verbose:
            print(f"[FAIL] {msg}")
        return False, msg
    except ConnectionFailure as cf:
        msg = f"MongoDB Connection Failure (Network / Timeout / IP Access List): {cf}"
        if verbose:
            print(f"[FAIL] {msg}")
        return False, msg
    except PyMongoError as pe:
        msg = f"MongoDB Driver Error: {pe}"
        if verbose:
            print(f"[FAIL] {msg}")
        return False, msg
    except Exception as e:
        msg = f"Unexpected Error connecting to MongoDB: {e}"
        if verbose:
            print(f"[FAIL] {msg}")
        return False, msg


if __name__ == "__main__":
    print("Testing ReelForge V3 MongoDB Atlas connection...")
    success, _ = test_connection(verbose=True)
    if not success:
        raise SystemExit(1)
