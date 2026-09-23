# ReelForge Project State — Single Source of Truth

> **Current as of**: 2026-09-23
> **Branch**: `main` (last commit `d500583`)
> **Current Version**: V3.6
> **Current Layer**: Layer 2 (Worker Workspace Isolation)
> **Layer Status**: COMPLETE & COMMITTED (Checkpoint 1: `d500583`)

---

## 1. Executive Snapshot

"If I know absolutely nothing about ReelForge, where are we right now?"

ReelForge is an automated Instagram reel knowledge extraction and personal intelligence engine. It downloads reels, transcribes audio (Whisper), performs multimodal visual analysis (OmniRoute LLM / Gemini / Llama vision), categorizes domain knowledge, stores structured Brain Objects in MongoDB Atlas (authoritative) with local JSON backup, and publishes rich formatted notes into user-specific Microsoft OneNote notebooks via Microsoft Graph.

The system has completed **V3.6 Layer 2** of the V3 multi-tenant and cloud worker architecture:
- V3.1 through V3.3 were previously committed (`68a5084`, `7241185`, `87739d9`, `d8bebfe`).
- V3.4 (Cloud Portability), V3.5 (Durable Jobs & Worker Reliability), V3.6.1 (Storage Audit), and V3.6.2 (Worker Workspace Isolation) have been implemented, tested, and committed in Checkpoint 1 ([`d500583`](file:///c:/Users/Rehan's%20Lenovo/OneDrive/Desktop/InstaBrain/config.py)).
- All 244 V3 unit and integration tests pass (100% pass rate).
- Application code is completely clean and committed.
- Checkpoint 2 establishes the permanent Project Brain (`.reelforge/` and `AGENTS.md`).

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
| **V3.1** | MongoDB Atlas Cloud Storage & User Layer | Verified | Committed (`68a5084`) | `storage/db.py`, `storage/user.py`, `test_v3_user_layer.py` |
| **V3.2** | Multi-User Brain Object Ownership | Verified | Committed (`7241185`) | `storage/brain_object.py`, `test_v3_ownership.py` |
| **V3.2.1** | MongoDB Ownership Index Hotfix | Verified | Committed (`87739d9`) | Index fix in `storage/db.py`, self-healing |
| **V3.3** | Per-User Microsoft OAuth & OneNote Writer | Verified | Committed (`87739d9`, `d8bebfe`) | `auth/oauth_server.py`, `test_v3_oauth_flow.py`, `test_v3_onenote_user.py` |
| **V3.4** | Cloud Portability & Runtime Independence | Verified | Committed (`d500583`) | Layers 1–4: `config.py` ROOT_DIR, system PATH ffmpeg, remote OmniRoute; tests: `test_v34_*.py` (pass) |
| **V3.5** | Durable Job Queue & Worker Reliability | Verified | Committed (`d500583`) | Layers 1–5: `storage/job.py`, `storage/publication.py`, `processing/worker.py`, `bot/notifier.py`; tests: `test_v35_layer*.py` (pass) |
| **V3.6.1** | Storage Lifecycle Audit & Operations Doc | Verified | Committed (`d500583`) | `WORKER_OPERATIONS.md`, `scratch/test_v36_layer1_storage_audit.py` (19/19 pass) |
| **V3.6.2** | Worker Workspace Isolation | Verified | Committed (`d500583`) | `processing/worker_workspace.py`, pipeline routing; `scratch/test_v36_layer2_workspace.py` (59/59 pass) |

---

## 3. Working Tree & Git Status

Current Git Status on branch `main`:
- **HEAD Commit**: `d500583` (*feat(v3): cloud portability, durable jobs, and worker workspace isolation*)
- **Working Tree State**: All application code committed. Permanent brain files (`AGENTS.md`, `.reelforge/`) staged for Checkpoint 2.

---

## 4. Test Verification Summary

All test suites verified on 2026-09-23:
- **V3.6 Layer 2 Workspace Isolation**: `scratch/test_v36_layer2_workspace.py` — **59/59 PASSED** (0 failures, 0 errors).
- **V3.6 Layer 1 Storage Lifecycle Audit**: `scratch/test_v36_layer1_storage_audit.py` — **19/19 PASSED** (0 failures, 0 errors).
- **Full V3 Test Suite**: `python -m unittest discover -s scratch -p "test_v3*.py"` — **244/244 PASSED** (0 failures, 0 errors).

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
