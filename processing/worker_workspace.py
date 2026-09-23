"""
ReelForge V3.6 Layer 2 — Worker workspace isolation.

Media scratch is ephemeral and worker-local. A standalone worker process
(``python -m processing.worker``) automatically activates a private, process-
lifetime workspace underneath the configured ``WORKSPACE_DIR``::

    WORKSPACE_DIR/
        workers/
            w-<pid>-<random>/        # this worker's media (mp4, jpg, info.json)

Embedded/CLI code paths (single-process local development, ``main.py``) never
activate isolation and keep using the configured ``WORKSPACE_DIR`` directly,
preserving the legacy flat layout and the "download then inspect via CLI"
local loop.

All pipeline components that read or write Reel media resolve the effective
workspace through :func:`effective_workspace` at call time, so downloader,
pipelines, transcription, vision, and cleanup always agree on one directory.

Persistent Brain Objects are deliberately NOT part of the workspace:
``BRAINS_DIR`` is untouched by this module.
"""
import os
import secrets
import threading
from pathlib import Path

from config import WORKSPACE_DIR

# Active worker workspace state (set once per process by activate_*).
_active_workspace: Path | None = None
_active_worker_id: str | None = None
_activation_lock = threading.Lock()


def workspace_root() -> Path:
    """The configured (non-isolated) workspace root."""
    return Path(WORKSPACE_DIR)


def _generate_worker_id() -> str:
    """
    Filesystem-safe, process-unique, process-lifetime worker id.

    PID alone would collide on restart; the random hex suffix makes two
    simultaneously started workers distinct even if they share a PID space.
    """
    return f"w-{os.getpid()}-{secrets.token_hex(4)}"


def is_worker_isolated() -> bool:
    """True once this process activated its private worker workspace."""
    return _active_workspace is not None


def worker_id() -> str | None:
    """The active worker id (directory name), or None when not isolated."""
    return _active_worker_id


def activate_worker_workspace() -> Path:
    """
    Assign this process a private worker workspace and create it.

    Idempotent: repeated calls return the same workspace. Thread-safe: the
    first call wins; concurrent callers that arrive before the first completes
    will all receive the same workspace. Directories are created automatically
    (``mkdir(parents=True, exist_ok=True)``), so no operator setup is required
    and fresh/ephemeral filesystems work.
    """
    global _active_workspace, _active_worker_id

    # Fast path: already activated.
    if _active_workspace is not None:
        return _active_workspace

    with _activation_lock:
        # Double-checked locking: re-test after acquiring the lock in case
        # another thread activated between our first test and acquiring the lock.
        if _active_workspace is not None:
            return _active_workspace

        _active_worker_id = _generate_worker_id()
        _active_workspace = workspace_root() / "workers" / _active_worker_id
        _active_workspace.mkdir(parents=True, exist_ok=True)

    return _active_workspace


def effective_workspace() -> Path:
    """
    The workspace that Reel-media reads/writes must use right now.

    Returns the active isolated worker workspace when one has been activated
    (standalone worker), otherwise the configured ``WORKSPACE_DIR``.
    """
    if _active_workspace is not None:
        return _active_workspace
    return workspace_root()


def reset() -> None:
    """Clear isolation state (test hook only)."""
    global _active_workspace, _active_worker_id
    with _activation_lock:
        _active_workspace = None
        _active_worker_id = None