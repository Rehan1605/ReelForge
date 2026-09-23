# ReelForge Changelog

> All notable changes to ReelForge are documented in this file and detailed in continuous version logs under [`.reelforge/versions/`](VERSION_INDEX.md).

---

## [V3.6.2] - 2026-09-15
### Added
- Worker Workspace Isolation (`processing/worker_workspace.py`): Ephemeral private directory `WORKSPACE_DIR/workers/w-<pid>-<hex>/` per standalone worker process.
- Dynamic workspace resolution via `effective_workspace()` across downloader, transcriber, vision analyzer, and cleanup.
- Dedicated unit test suite `scratch/test_v36_layer2_workspace.py` (59 tests, 100% passing).
### Changed
- Exact reel ID cleanup matching (`<reel_id>.*`) to prevent numeric prefix collisions (e.g. `123` deleting `1234.mp4`).
### Commit
- `d500583`

---

## [V3.6.1] - 2026-09-14
### Added
- Storage Lifecycle Audit & Operations Contract (`WORKER_OPERATIONS.md` and `WORKER.env.example`).
- Comprehensive storage verification test suite `scratch/test_v36_layer1_storage_audit.py` (19 tests, 100% passing).
### Commit
- `d500583`

---

## [V3.5] - 2026-09-14
### Added
- Durable MongoDB `reel_jobs` queue with partial-unique index on `(claim_key, active)`.
- Atomic lease-based job claims and lease heartbeat renewer (`storage/job.py`).
- Standalone worker daemon (`processing/worker.py`) with Telegram job notifier (`bot/notifier.py`).
- Idempotent OneNote publication slot claiming (`storage/publication.py`) preventing duplicate pages on retry.
- Embedded worker toggle (`REELFORGE_EMBEDDED_WORKER=1`).
### Commit
- `d500583`

---

## [V3.4] - 2026-09-14
### Added
- Cloud portability & runtime independence: root-anchored paths in `config.py` via `ROOT_DIR`.
- Platform-agnostic FFmpeg resolution (system PATH fallback on Linux).
- Configurable external AI gateway routing (`OMNIROUTE_BASE_URL` and `OMNIROUTE_API_KEY`).
- Docker container definition (`Dockerfile`, `.dockerignore`).
### Commit
- `d500583`

---

## [V3.3] - 2026-09-13 to 2026-09-14
### Added
- Per-user Microsoft OAuth 2.0 authorization code flow with PKCE (`auth/oauth_server.py`, `auth/state.py`).
- Encrypted per-user MSAL token cache in MongoDB (`users.microsoft.token_cache`).
- Per-user `GraphClient` and `OneNoteWriter` with automated notebook/section creation.
- Telegram commands: `/connect`, `/disconnect`, `/mestatus`.
- Fix for public-client MSAL flow state persistence (`d8bebfe`).
### Commit
- `87739d9`, `d8bebfe`

---

## [V3.2.1] - 2026-09-13
### Fixed
- MongoDB ownership index hotfix: dropped invalid parallel-array compound index (`idx_user_library_active`), created separate indexes `idx_ownership_archived_by` and `idx_ownership_saved_processed_at` with self-healing startup repair.
### Commit
- Included in `87739d9`

---

## [V3.2] - 2026-09-11
### Added
- Multi-user Brain Object ownership model: `ownership.saved_by`, `ownership.archived_by`.
- Multi-user library filtering (`get_user_brain_objects`, `search_user_brain_objects`).
- Automatic user registration upon Telegram interaction.
### Commit
- `7241185`

---

## [V3.1] - 2026-09-10
### Added
- MongoDB Atlas cloud storage integration (`storage/db.py`, `storage/brain_object.py`).
- Brain Object schema with write-behind atomic local JSON backup.
- User management collection (`storage/user.py`).
- V2 to V3 data migration script (`storage/migrate_v2_to_v3.py`).
### Commit
- `68a5084`

---

## [V2.7] - 2026-09-04
### Added
- Grounded evaluation & quality system (`evaluation/evaluate.py`, LLM judge).
- Dataset migration and categorization quality verification.
### Commit & Tag
- `af43757`, Tag `v2.7.0`

---

## [V2.6] - 2026-09-04
### Added
- Cross-reel knowledge linking (`reels_related`, topic discovery).
- Knowledge lifecycle control: `/archive`, `/unarchive`, duplicate prevention.
### Commit & Tag
- `aa56157`, `77e1640`, Tags `v2.6.0`, `v2.6.1`

---

## [V2.5.1] - 2026-09-04
### Added
- Telegram knowledge discovery: `/library`, `/search <query>`, `/topic <name>`.
### Commit & Tag
- `85d6445`, Tag `v2.5.1`

---

## [V2.5] - 2026-09-04
### Added
- Rich OneNote knowledge note styling (dark theme, colored callouts, code formatting, thumbnails).
- Duplicate reel processing detection.
### Commit & Tag
- `4fda5a1`, Tag `v2.5.0`

---

## [V2.4] - 2026-09-04
### Added
- Pipeline reliability, atomic file operations, state safety, fallback categorizer.
### Commit & Tag
- `268afc9`, Tag `v2.4.0`

---

## [V2.3] - 2026-09-03
### Added
- Unified OmniRoute gateway for LLM calls (`qwen2.5:7b-instruct`).
- Multimodal visual frame extraction and vision analysis (`llama3.2-vision`).
### Commit & Tag
- `f5906f9`, Tag `v2.3.0`

---

## [V1.0] - 2026-07-28 to 2026-08-23
### Added
- Initial Telegram bot and yt-dlp reel downloader.
- Whisper audio transcription.
- Microsoft OneNote publishing via Microsoft Graph API.
- Local JSON storage in `brains/`.
### Commit
- Commits `7cc85d1` through `220e540`
