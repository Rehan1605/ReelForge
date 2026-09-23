# ReelForge Architectural Decisions

> **PURPOSE OF THIS DOCUMENT**
> This file records key architectural decisions, rationale, constraints, and conditions for revisiting them.
> It prevents coding agents from repeatedly redesigning existing systems, guessing architectural intent, or introducing services that were deliberately avoided (e.g. introducing AWS S3 when media is ephemeral scratch).

---

## Decision Index

1. [ADR-001: MongoDB Atlas as Authoritative Persistent Store](#adr-001-mongodb-atlas-as-authoritative-persistent-store)
2. [ADR-002: Ephemeral Media Scratch & No Object Storage](#adr-002-ephemeral-media-scratch--no-object-storage)
3. [ADR-003: Local JSON Backup & Offline Fallback](#adr-003-local-json-backup--offline-fallback)
4. [ADR-004: Worker Ephemeral Workspace Isolation](#adr-004-worker-ephemeral-workspace-isolation)
5. [ADR-005: Idempotent OneNote Publication via Slot Claims](#adr-005-idempotent-onenote-publication-via-slot-claims)
6. [ADR-006: Provider-Agnostic AI Gateway (OmniRoute)](#adr-006-provider-agnostic-ai-gateway-omniroute)
7. [ADR-007: Laptop-Side Loopback OAuth / Server-Side Token Consumption](#adr-007-laptop-side-loopback-oauth--server-side-token-consumption)
8. [ADR-008: Persistent Whisper Model Cache Decoupled from Scratch](#adr-008-persistent-whisper-model-cache-decoupled-from-scratch)
9. [ADR-009: Disciplined Git Checkpoints (Commit ≠ Push)](#adr-009-disciplined-git-checkpoints-commit--push)

---

### ADR-001: MongoDB Atlas as Authoritative Persistent Store

- **Status**: Accepted & Implemented
- **Date**: 2026-09-10 (V3.1)
- **Context**: ReelForge began as a single-user local file-based prototype storing JSON Brain Objects in `brains/*.json`. As multi-user support, cloud worker execution, and durable queueing were introduced, a centralized, concurrent, authoritative datastore was required.
- **Decision**: MongoDB Atlas is the single authoritative datastore for Brain Objects (`brains`), User accounts (`users`), OAuth flow sessions (`oauth_sessions`), and durable jobs (`reel_jobs`).
- **Rationale**: Document-oriented schema fits the nested, polymorphic Brain Object and extraction metadata; rich indexing, partial unique indexes for job deduplication, and atomic operations (`find_one_and_update`) enable robust distributed coordination without external redis/locking daemons.
- **Do Not Change Unless**: A massive scale migration away from document databases is formally scheduled.

---

### ADR-002: Ephemeral Media Scratch & No Object Storage

- **Status**: Accepted & Implemented
- **Date**: 2026-09-14 (V3.4/V3.6)
- **Context**: Instagram reels (MP4 files, thumbnails, and JSON metadata) must be downloaded for audio transcription and visual frame analysis. There was discussion about uploading videos to cloud object storage (e.g. AWS S3, Google Cloud Storage, or MinIO).
- **Decision**: **No object storage is used.** Reel video files and thumbnails are strictly ephemeral local scratch files. By default (`KEEP_VIDEOS=False`), all downloaded reel media is deleted immediately upon pipeline completion via `_cleanup_reel_media()`.
- **Rationale**: ReelForge extracts durable knowledge and publishes it to OneNote and MongoDB. It is not a video streaming host. Storing videos in object storage incurs unnecessary cloud storage costs, egress fees, complex lifecycle policies, and copyright liability.
- **Revisit When**: A future feature explicitly requires re-serving original reel video files directly to users through a mobile app or web player.

---

### ADR-003: Local JSON Backup & Offline Fallback

- **Status**: Accepted & Implemented
- **Date**: 2026-09-10 (V3.1)
- **Context**: Development often occurs locally on laptops with flaky internet connections or during offline unit testing where MongoDB Atlas is unreachable.
- **Decision**: When a Brain Object is saved, it is written to MongoDB first, and also written to `brains/<reel_id>.json` using an atomic write pattern (`tempfile` + `os.replace`). When reading, MongoDB is the primary source; if MongoDB is unavailable or unconfigured, the system safely falls back to local JSON.
- **Rationale**: Guarantees local developers and offline test suites can run without Atlas connectivity, while ensuring Atlas remains the authoritative master when connected.
- **Do Not Change Unless**: Local backup writing is found to bottleneck high-throughput containerized workers (in which case it can be toggled by environment variable).

---

### ADR-004: Worker Ephemeral Workspace Isolation

- **Status**: Accepted & Implemented
- **Date**: 2026-09-15 (V3.6 Layer 2)
- **Context**: In multi-process worker deployments, multiple workers running on the same host or sharing a mounted volume would conflict if downloading to a flat `reels/` directory (e.g. overwriting `temp.mp4` or deleting each other's files during cleanup).
- **Decision**: Standalone workers (`python -m processing.worker`) automatically activate an isolated directory at startup: `WORKSPACE_DIR/workers/w-<pid>-<random_hex>/`. All pipeline components resolve media paths dynamically through `processing.worker_workspace.effective_workspace()`. Embedded mode (`REELFORGE_EMBEDDED_WORKER=1` / `main.py`) does not activate isolation and retains the flat `WORKSPACE_DIR` for zero-friction inspection.
- **Rationale**: Eliminates race conditions and media collisions across concurrent worker processes with zero operator configuration.
- **Do Not Change Unless**: Worker architecture shifts to fully containerized one-process-per-container setups with dedicated container filesystems.

---

### ADR-005: Idempotent OneNote Publication via Slot Claims

- **Status**: Accepted & Implemented
- **Date**: 2026-09-14 (V3.5 Layer 4)
- **Context**: If a durable job crashes or times out while publishing to Microsoft OneNote, retrying the job could create duplicate OneNote pages in the user's notebook.
- **Decision**: OneNote publishing is made idempotent per `(reel_id, user_id)` through publication slot claiming in `storage.publication`. The first worker claims the slot (`state: in_progress`); once published, the resulting `page_id` and URL are recorded (`state: complete`). Subsequent retry attempts or duplicate jobs detect the completed publication record and reuse it without calling Microsoft Graph API.
- **Rationale**: Prevents notebook pollution, protects Microsoft Graph rate limits, and provides idempotency guarantees during network or process failures.
- **Do Not Change Unless**: Microsoft Graph API introduces native idempotency keys for OneNote page creation.

---

### ADR-006: Provider-Agnostic AI Gateway (OmniRoute)

- **Status**: Accepted & Implemented
- **Date**: 2026-09-03 (V2.3)
- **Context**: The pipeline relies on text LLMs (for categorization, structured extraction, summary) and vision models (for frame analysis). Coupling code to vendor-specific SDKs (OpenAI, Anthropic, Google, Ollama) created vendor lock-in.
- **Decision**: All LLM and multimodal vision calls route through an OpenAI-compatible gateway (`OMNIROUTE_BASE_URL`). The default is local OmniRoute (`http://localhost:20128/v1`), overridable by environment variables (`OMNIROUTE_BASE_URL` / `OMNIROUTE_API_KEY`).
- **Rationale**: Unified API format (`/chat/completions`) allows zero-code model swapping across local Ollama instances, OpenAI, Gemini, or Claude.
- **Do Not Change Unless**: A required frontier model capability is fundamentally unsupported by OpenAI-compatible endpoint schemas.

---

### ADR-007: Laptop-Side Loopback OAuth / Server-Side Token Consumption

- **Status**: Accepted & Implemented
- **Date**: 2026-09-13 (V3.3)
- **Context**: Users authenticate their Microsoft OneNote account via Telegram using `/connect`. A cloud worker running in a container or VM cannot easily open a browser or listen on localhost for OAuth callbacks.
- **Decision**: The interactive OAuth authorization-code flow with PKCE is initiated via `/connect` and uses a local callback server (`auth/oauth_server.py`) with signed state (`auth/state.py`). The resulting MSAL token cache is stored encrypted in MongoDB (`users.microsoft.token_cache`). Cloud workers only *consume* tokens from MongoDB via silent refresh and never listen on OAuth callback ports.
- **Rationale**: Decouples user-facing interactive login from background job execution. Workers remain lightweight background consumers.
- **Revisit When**: A centralized public web gateway with a public domain and SSL is deployed to handle OAuth redirects globally.

---

### ADR-008: Persistent Whisper Model Cache Decoupled from Scratch

- **Status**: Accepted & Implemented
- **Date**: 2026-09-14 (V3.4/V3.6)
- **Context**: Whisper models (e.g. `base`, `medium`) download large neural network weights. If stored in ephemeral media scratch, every worker restart or cleanup would re-download weights from HuggingFace.
- **Decision**: Whisper weight downloads are directed to `WHISPER_CACHE_DIR` (defaulting to system user cache `~/.cache/whisper` or an explicit mount). Model loading is wrapped in thread-safe process-lifetime caching in `processing/transcriber.py`.
- **Rationale**: Eliminates redundant multi-gigabyte downloads on worker startup and ensures startup is offline-capable once pre-cached.
- **Do Not Change Unless**: Moving to a dedicated external speech-to-text API service.

---

### ADR-009: Disciplined Git Checkpoints (Commit ≠ Push)

- **Status**: Accepted
- **Date**: 2026-09-23
- **Context**: Agents previously accumulated massive uncommitted working trees across multiple layers, risking catastrophic context loss or corrupted states upon unexpected termination.
- **Decision**: 
  1. Agents never execute `git commit` or `git push` without explicit human authorization.
  2. Local commits are recovery checkpoints created layer-by-layer after verification tests pass and human review is conducted.
  3. Pushing to remote repositories is a separate, deliberate operational step.
- **Rationale**: Small logical local commits provide safe rollback points and prevent agents from losing days of progress, while maintaining human oversight over code merges.
