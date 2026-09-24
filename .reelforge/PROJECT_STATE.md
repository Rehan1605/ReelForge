# ReelForge Project State — Single Source of Truth

> **Current as of**: 2026-09-24
> **Branch**: `main` (last commit `ce33ca5`, ahead of `origin/main` by 2 commits)
> **Current Version**: V3.6
> **Current Layer**: Layer 3 (Managed OAuth Callback Server Lifecycle & Cloud Configuration)
> **Layer Status**: IMPLEMENTED & VERIFIED (Tests: 258/258 passing)

---

## 1. Executive Snapshot

"If I know absolutely nothing about ReelForge, where are we right now?"

ReelForge is an automated Instagram reel knowledge extraction and personal intelligence engine. It downloads reels, transcribes audio (Whisper), performs multimodal visual analysis (OmniRoute LLM / Gemini / Llama vision), categorizes domain knowledge, stores structured Brain Objects in MongoDB Atlas (authoritative) with local JSON backup, and publishes rich formatted notes into user-specific Microsoft OneNote notebooks via Microsoft Graph.

The system is actively progressing through the **Cloud Runtime Deployment** milestone:
- V3.1 through V3.3 were previously committed (`68a5084`, `7241185`, `87739d9`, `d8bebfe`).
- V3.4 (Cloud Portability), V3.5 (Durable Jobs & Worker Reliability), V3.6.1 (Storage Audit), and V3.6.2 (Worker Workspace Isolation) were committed in `d500583`.
- V3.6 test suite was migrated to tracked `tests/` in commit `ce33ca5` (244 tests).
- V3.6.3 implements production-manageable OAuth callback server configuration (`OAUTH_HOST`, `REELFORGE_START_OAUTH_SERVER`), thread-safe `RLock` lifecycle management (`start_oauth_server`, `stop_oauth_server`), and Telegram bot hook integration (`_on_post_init`, `_on_post_shutdown`).
- All 258 V3 unit and integration tests are tracked in `tests/` and pass (100% pass rate).

---

## 2. Version Verification Checklist

The distinction between **Committed**, **Implemented**, **Tested**, **Verified**, **Partially Implemented**, and **Not Started** is strictly preserved below based on repository evidence:

| Version | Feature Area | Engineering Status | Git Status | Evidence & Test File |
|---|---|---|---|---|
| **V1.0** | Core Reel Pipeline & OneNote Publishing | Verified | Committed (`220e540`) | Historical commit, `main.py` |
| **V2.3** | OmniRoute Gateway & Multimodal Extraction | Verified | Committed (`f5906f9`, tag `v2.3.0`) | Tag `v2.3.0`, prompts, analyzer |
| **V2.4** | State Safety & Extraction Reliability | Verified | Committed (`268afc9`, tag `v2.4.0`) | Tag `v2.4.0` |
| **V2.5** | Rich OneNote Experience & Duplicate Detection | Verified | Committed (`4fda5a1`, tag `v2.5.0`) | Tag `v2.5.0` |
| **V2.5.1** | Telegram Knowledge Discovery | Verified | Committed (`85d6445`, tag `v2.5.1`) | Tag `v2.5.1` |
| **V2.6** | Knowledge Lifecycle & Cross-Reel Linking | Verified | Committed (`aa56157`, `77e1640`, tag `v2.6.1`) | Tags `v2.6.0`, `v2.6.1` |
| **V2.7** | Grounded Evaluation System & Migration | Verified | Committed (`af43757`, tag `v2.7.0`) | Evaluation module, tag `v2.7.0` |
| **V3.1** | MongoDB Atlas Cloud Storage & User Layer | Verified | Committed (`68a5084`) | `storage/db.py`, `storage/user.py`, `scratch/test_v3_user_layer.py` |
| **V3.2** | Multi-User Brain Object Ownership | Verified | Committed (`7241185`) | `storage/brain_object.py`, `scratch/test_v3_ownership.py` |
| **V3.2.1** | MongoDB Ownership Index Hotfix | Verified | Committed (`87739d9`) | Index fix in `storage/db.py`, self-healing |
| **V3.3** | Per-User Microsoft OAuth & OneNote Writer | Verified | Committed (`87739d9`, `d8bebfe`) | `auth/oauth_server.py`, `scratch/test_v3_oauth_flow.py`, `scratch/test_v3_onenote_user.py` |
| **V3.4** | Cloud Portability & Runtime Independence | Verified | Committed (`d500583`) | Layers 1–4: `config.py` ROOT_DIR, system PATH ffmpeg, remote OmniRoute; tests: `tests/test_v34_*.py` (pass) |
| **V3.5** | Durable Job Queue & Worker Reliability | Verified | Committed (`d500583`) | Layers 1–5: `storage/job.py`, `storage/publication.py`, `processing/worker.py`, `bot/notifier.py`; tests: `tests/test_v35_layer*.py` (pass) |
| **V3.6.1** | Storage Lifecycle Audit & Operations Doc | Verified | Committed (`d500583`) | `WORKER_OPERATIONS.md`, `tests/test_v36_layer1_storage_audit.py` (19/19 pass) |
| **V3.6.2** | Worker Workspace Isolation | Verified | Committed (`d500583`) | `processing/worker_workspace.py`, pipeline routing; `tests/test_v36_layer2_workspace.py` (59/59 pass) |
| **V3.6.3** | Managed OAuth Lifecycle & Cloud Configuration | Verified | Implemented (Working Tree) | `auth/oauth_server.py`, `bot/telegram_bot.py`, `config.py`; `tests/test_v36_oauth_lifecycle.py` (14/14 pass) |

---

## 3. Working Tree & Git Status

Current Git Status on branch `main`:
- **Last Committed Checkpoint**: `ce33ca5` (*test(v3.6): establish tracked V3 regression test suite*).
- **Current Working Tree State**: Working tree contains V3.6.3 OAuth lifecycle changes and new test suite `tests/test_v36_oauth_lifecycle.py`. 258/258 tests pass.

---

## 4. Test Verification Summary

All test suites verified from tracked `tests/` on 2026-09-24:
- **V3.6 Layer 3 Managed OAuth Lifecycle**: `tests/test_v36_oauth_lifecycle.py` — **14/14 PASSED** (0 failures, 0 errors in 3.62s).
- **V3.6 Layer 2 Workspace Isolation**: `tests/test_v36_layer2_workspace.py` — **59/59 PASSED** (0 failures, 0 errors in 0.75s).
- **V3.6 Layer 1 Storage Lifecycle Audit**: `tests/test_v36_layer1_storage_audit.py` — **19/19 PASSED** (0 failures, 0 errors in 0.68s).
- **Full V3 Test Suite**: `python -m unittest discover -s tests -p "test_v3*.py"` — **258/258 PASSED** (0 failures, 0 errors in 5.15s).

> **Test Tracking Status**:
> The complete active V3 regression test suite (12 test files, 258 tests) is tracked in version control under `tests/`. Exploratory, manual verification, and evaluation scripts remain in `scratch/`.

---

## 5. Architectural Invariants (Current System)

1. **MongoDB Atlas is the Authoritative Store**:
   - Brain Objects, user profiles, per-user Microsoft token caches, and durable job states live in MongoDB Atlas.
   - Local JSON files in `brains/*.json` are atomic write-behind backups and offline/test fallbacks only.
2. **Reel Media is Ephemeral Scratch**:
   - MP4 video files, thumbnails, and `*.info.json` are ephemeral.
   - Under standalone worker mode, each worker process automatically receives a dedicated isolated directory: `WORKSPACE_DIR/workers/w-<pid>-<random>/`.
   - Embedded mode (local development bot) retains the flat `WORKSPACE_DIR` for zero-friction inspection.
   - Reel media is deleted after processing (`KEEP_VIDEOS=False`).
3. **OneNote Publishing is Idempotent**:
   - Pages are published per `(reel_id, user_id)` through slot claims in `storage.publication`.
   - Crash recovery or retries reuse existing OneNote page IDs to prevent duplicate pages.
4. **Git Checkpoint Boundary**:
   - Commits are local recovery checkpoints created only after human review.
   - Pushes are separate operations. Agents never independently commit or push.
