# ReelForge Project State — Single Source of Truth

> **Current as of**: 2026-11-01
> **Branch**: `main` (latest observed committed HEAD `23cc846`; local history is 4 commits ahead of `origin/main` before this task)
> **Current Version**: V3.7.4 Android/Termux MongoDB DNS compatibility (working tree implementation)
> **Current Layer**: Termux DNS resolver setup before MongoDB SRV URI parsing
> **Layer Status**: IMPLEMENTED & VERIFIED — focused checks 8/8; full V3 suite 297/297

---

## 1. Executive Snapshot

"If I know absolutely nothing about ReelForge, where are we right now?"

ReelForge is an automated Instagram reel knowledge extraction and personal intelligence engine. It downloads reels, transcribes audio through FFmpeg and the local whisper.cpp CLI using an explicitly provisioned multilingual Base model, performs visual analysis and text extraction through the fixed OpenRouter `openrouter/free` route, categorizes domain knowledge, stores structured Brain Objects in MongoDB Atlas (authoritative) with local JSON backup, and publishes rich formatted notes into user-specific Microsoft OneNote notebooks via Microsoft Graph.

The system is actively progressing through the **Cloud Runtime Deployment** milestone:
- V3.1 through V3.3 were previously committed (`68a5084`, `7241185`, `87739d9`, `d8bebfe`).
- V3.4 (Cloud Portability), V3.5 (Durable Jobs & Worker Reliability), V3.6.1 (Storage Audit), and V3.6.2 (Worker Workspace Isolation) were committed in `d500583`.
- V3.6 test suite was migrated to tracked `tests/` in commit `7b40d35` (244 tests).
- V3.6.3 implemented managed OAuth callback server lifecycle and cloud binding (`4064789`, 14 tests, total 258).
- V3.6.4 implements remote vision connectivity overrides (`VISION_BASE_URL`, `VISION_API_KEY`), URL normalization, preflight connectivity probing (`check_vision_gateway`), and test suite expansion (`tests/test_v36_vision_remote.py`, 14 tests).
- All 272 V3 unit and integration tests are tracked in `tests/` and pass (100% pass rate).
- V3.6.5 implements shared-job requester tracking and per-user OneNote publication fan-out, with publication-only retries (`tests/test_v36_publication_fanout.py`, 12 tests); full V3 suite: 284/284 passing on 2026-11-01.
- V3.7.3-A replaces Python `openai-whisper` with a subprocess adapter for `whisper-cli`, explicitly configured `ggml-base.bin`, FFmpeg 16 kHz mono PCM preparation, per-call temporary directories, and mocked regression coverage. Docker packaging remains for V3.7.3-B.
- V3.7.3-B adds a pinned CPU whisper.cpp multi-stage Docker build, SHA-1-verified multilingual Base model, external model mount target, and worker-only startup validation; Docker is not installed in this environment. Its recorded 300-test result is the historical V3.7.3-B checkpoint.
- V3.7.1 + V3.7.2 replaces configurable OmniRoute/provider/model routes with a single fixed OpenRouter endpoint/model for text, evaluation, and vision. Local whisper.cpp transcription and FFmpeg frame extraction remain unchanged. Focused checks: 60/60; full V3 suite: 285/285.
- V3.7.4 configures dnspython from `$PREFIX/etc/resolv.conf` only on the Termux Python prefix, immediately before PyMongo client construction, so Atlas SRV/TXT resolution can use Termux nameservers. DNS setup is covered by mocked tests; no live database lookup was performed.

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
| **V3.6.3** | Managed OAuth Lifecycle & Cloud Configuration | Verified | Committed (`4064789`) | `auth/oauth_server.py`, `bot/telegram_bot.py`, `config.py`; `tests/test_v36_oauth_lifecycle.py` (14/14 pass) |
| **V3.6.4** | Remote AI Vision Connectivity & Cloud Readiness | Verified | Implemented (Working Tree) | `config.py`, `processing/vision_analyzer.py`, `WORKER.env.example`; `tests/test_v36_vision_remote.py` (14/14 pass) |
| **V3.6.5** | Multi-user Publication Fan-out | Verified | Implemented (Working Tree) | `storage/job.py`, `processing/publication_fanout.py`, `processing/worker.py`, `onenote/writer.py`, bot retry command; `tests/test_v36_publication_fanout.py` (12/12 pass) |
| **V3.7.3-A** | whisper.cpp Transcription Adapter | Verified (Uncommitted) | Working Tree | `processing/transcriber.py`, `config.py`, `tests/test_v34_portability.py` (13 tests; complete V3 suite 285/285 pass) |
| **V3.7.3-B** | whisper.cpp Docker & Runtime Packaging | Verified in tests (Uncommitted); Docker build unavailable | Working Tree | `Dockerfile`, `processing/transcriber.py`, `processing/worker.py`, `tests/test_v373_runtime_packaging.py` (15 tests; full V3 suite 300/300) |
| **V3.7.4** | Android/Termux MongoDB DNS Compatibility | Verified in mocked tests | Working Tree | `storage/db.py`; `tests/test_v34_termux_dns.py` (8/8; full V3 suite 297/297) |

---

## 3. Working Tree & Git Status

Current Git Status on branch `main`:
- **Latest committed HEAD observed before this task**: `23cc846` (four local commits ahead of `origin/main`).
- **Pre-existing working-tree work preserved**: uncommitted V3.6.5 changes and untracked V3.6.5 version record, publication fan-out code/test, and `docs/` directory.
- **V3.7.3-A/B** add the transcription adapter, pinned CPU packaging, model provisioning, startup validation, tests, and documentation. No commit or history mutation was performed.

---

## 4. Test Verification Summary

Prior V3 suites were verified on 2026-11-01. Current task verification:
- **Focused suites**: V3.7.3-B startup/runtime packaging plus V3.7.3-A adapter/config contract — 37/37 passed.
- **V3.7.3-B historical full-suite checkpoint**: `python -m unittest discover -s tests -p "test_v3*.py"` — 300/300 passed (0 failures, 0 errors; 31.429s). This was the count at that release checkpoint, before replacement of legacy gateway-contract test cases.
- **Docker image**: not built; Docker CLI unavailable in this environment.
- **V3.6 Layer 3 Remote AI Vision Connectivity**: `tests/test_v36_vision_remote.py` — **14/14 PASSED** (0 failures, 0 errors in 0.005s).
- **V3.6 Layer 3 Managed OAuth Lifecycle**: `tests/test_v36_oauth_lifecycle.py` — **14/14 PASSED** (0 failures, 0 errors in 3.62s).
- **V3.6 Layer 2 Workspace Isolation**: `tests/test_v36_layer2_workspace.py` — **59/59 PASSED** (0 failures, 0 errors in 0.75s).
- **V3.6 Layer 1 Storage Lifecycle Audit**: `tests/test_v36_layer1_storage_audit.py` — **19/19 PASSED** (0 failures, 0 errors in 0.68s).
- **V3.6.5 Publication Fan-out**: `tests/test_v36_publication_fanout.py` — **12/12 PASSED**.
- **Post OpenRouter migration full V3 suite**: `python -m unittest discover -s tests -p "test_v3*.py"` — **285/285 PASSED** (0 failures, 0 errors; 31.326s), before this integration cleanup.
- **Current cleanup focused suite**: 79/79 passed.
- **Current full V3 suite**: `python -m unittest discover -s tests -p "test_v3*.py"` — **289/289 passed** (0 failures, 0 errors; 36.420s).
- **V3.7.4 Termux DNS focused tests before prefix-fallback correction**: `python -m unittest tests.test_v34_termux_dns` — **7/7 passed**.
- **Full V3 suite before prefix-fallback correction**: `python -m unittest discover -s tests -p "test_v3*.py"` — **296/296 passed** (0 failures, 0 errors; 36.378s).
- **After prefix-fallback correction**: current results are recorded in `.reelforge/ACTIVE_TASK.md` after the requested test runs.

> **Test Tracking Status**:
> The active V3 regression suite is discovered by `python -m unittest discover -s tests -p "test_v3*.py"`. Its count changed from the historical 300 at the V3.7.3-B checkpoint to 285 after replacing legacy configurable-gateway contract coverage during the OpenRouter migration. Later integration and Termux DNS coverage brings the current result to 296. Exploratory and manual verification scripts remain in `scratch/`.

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
