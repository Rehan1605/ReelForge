# ReelForge System Architecture

> **Architectural Specification & Component Blueprint**
> **Current Version**: V3.6
> **Authoritative Store**: MongoDB Atlas
> **Runtime Target**: Local Development & Containerized Cloud Workers

---

## 1. System Overview

ReelForge is a multimodal knowledge extraction and automated personal knowledge management (PKM) platform. It transforms unstructured short-form video content (Instagram reels) into deeply structured, queryable, categorized "Brain Objects", and publishes rich, stylized knowledge notes to Microsoft OneNote notebooks scoped to individual users.

```
                           +---------------------------+
                           |  Telegram User Interface  |
                           |    (bot/telegram_bot.py)  |
                           +-------------+-------------+
                                         |
                       [Submits URL]     |     [/connect OAuth]
                                         v                     v
+--------------------+       +-----------+---------+       +---+------------------+
| Microsoft Graph    |       |  MongoDB Atlas      |       | OAuth Callback Svr   |
| (OneNote Pages)    |<---+  |  (Authoritative)    |<------+ (auth/oauth_server.py|
+--------------------+    |  |  - brains           |       +----------------------+
                          |  |  - users            |
                          |  |  - reel_jobs        |
                          |  |  - oauth_sessions   |
                          |  +-----------+---------+
                          |              ^
                          |  [Atomic Claim & Lease]
                          |              |
+-------------------------+--------------+---------------------------------------+
| Processing Worker (processing/worker.py or Embedded Worker in bot)             |
|                                                                                |
|  +--------------------------------------------------------------------------+  |
|  | Private Ephemeral Workspace: reels/workers/w-<pid>-<hex>/                |  |
|  | (processing/worker_workspace.py)                                         |  |
|  +--------------------------------------------------------------------------+  |
|         |                        |                         |                   |
|         v                        v                         v                   |
|  +--------------+         +--------------+          +------------------+       |
|  | Downloader   |         | Transcriber  |          | Vision Analyzer  |       |
|  | (yt-dlp)     |         | (Whisper)    |          | (OmniRoute LLM)  |       |
|  +--------------+         +--------------+          +------------------+       |
|         |                        |                         |                   |
|         +------------------------+-------------------------+                   |
|                                  |                                             |
|                                  v                                             |
|                   +-------------------------------+                            |
|                   | Domain Knowledge Extractors   |                            |
|                   | (Programming, AI, Food, etc.) |                            |
|                   +---------------+---------------+                            |
|                                   |                                            |
|                                   v                                            |
|                   +-------------------------------+                            |
|                   | OneNote Writer (Idempotent)   |                            |
|                   +-------------------------------+                            |
+--------------------------------------------------------------------------------+
```

---

## 2. Core Architectural Subsystems

### 2.1 Ingestion & User Interface (`bot/`)
- **`bot/telegram_bot.py`**:
  - Handles incoming Telegram messages, URL detection, commands (`/start`, `/library`, `/search`, `/topic`, `/connect`, `/disconnect`, `/mestatus`, `/queue`).
  - Supports both **embedded execution** (single-process mode via `REELFORGE_EMBEDDED_WORKER=1`) and **decoupled execution** (submits jobs to MongoDB `reel_jobs` queue for standalone workers).
  - Preserves user ownership boundaries: users only view and search their own saved reel objects.
- **`bot/notifier.py`**:
  - Asynchronous notifier used by background workers to dispatch live progress updates ("Downloading", "Transcribing", "Analyzing Vision", "Publishing") and final result cards back to the requesting Telegram chat.

### 2.2 Processing & Multimodal Pipeline (`processing/`, `download/`)
- **`download/downloader.py`**:
  - Utilizes `yt-dlp` to download video MP4 files, extract metadata (`*.info.json`), and download thumbnails into `effective_workspace()`.
- **`processing/transcriber.py`**:
  - Transcribes audio using OpenAI Whisper.
  - Weights are cached on a persistent volume via `WHISPER_CACHE_DIR`.
  - Process-lifetime model caching ensures models are loaded into RAM once per worker process.
- **`processing/vision_analyzer.py`**:
  - Extracts key representative video frames using OpenCV / FFmpeg.
  - Sends base64-encoded frames alongside transcript and caption to the OmniRoute vision gateway (`VISION_MODEL`).
  - Generates visual observations, on-screen text (OCR), code snippets, and UI details.
- **`processing/categorizer.py` & Domain Extractors**:
  - Classifies content into predefined domains: *Programming*, *AI*, *Finance*, *Food*, *Gym*, *Productivity*, *Travel*, *Photography*, *Movies/Edits*, *Other*.
  - Dispatches to specialized domain extractors (`processing/<domain>_extractor.py`) that normalize schema into structured key-value knowledge.
- **`processing/pipeline.py` (`process_reel`)**:
  - The central orchestrator uniting download, transcription, vision, categorization, extraction, Brain Object creation, and OneNote publication.
  - Enforces automatic post-processing media cleanup (`_cleanup_reel_media()`) when `KEEP_VIDEOS=False`.

### 2.3 Worker Runtime & Ephemeral Workspace Isolation (`processing/worker_workspace.py`)
- Standalone worker processes (`python -m processing.worker`) automatically initialize a private directory under `WORKSPACE_DIR/workers/w-<pid>-<random_hex>/`.
- All media path lookups invoke `effective_workspace()`:
  - In standalone mode: points to the private worker sub-directory.
  - In embedded mode / CLI: points directly to `WORKSPACE_DIR`.
- Guarantees zero cross-process file collisions and safe cleanup in multi-worker environments.

### 2.4 Durable Asynchronous Job System (`storage/job.py`, `processing/worker.py`)
- **Queue Mechanics**:
  - Jobs are stored in the MongoDB `reel_jobs` collection.
  - A partial-unique compound index `(claim_key, active)` prevents duplicate concurrent jobs for the same reel URL while allowing historic re-runs.
- **Atomic Claiming & Leases**:
  - Workers atomically claim the next available job via `find_one_and_update` with a 900-second lease (`lease.expires_at`).
  - A background `_LeaseRenewer` heartbeat thread continuously extends the lease during slow pipeline execution (e.g. large model loads or video processing).
  - If a worker crashes, stale jobs whose leases have expired are automatically recovered and reassigned.

### 2.5 Authoritative Storage & Multi-Tenancy (`storage/`)
- **`storage/db.py`**:
  - Connection pooling for MongoDB Atlas with auto-reconnect and index enforcement (`ensure_brain_indexes()`, `ensure_job_indexes()`).
- **`storage/brain_object.py`**:
  - Brain Object document schema: `id`, `shortcode`, `url`, `media`, `transcription`, `vision_analysis`, `knowledge`, `ownership`, `publications`, `metadata`, `provenance`.
  - Multi-user ownership tracking: tracks primary saver and associated users (`ownership.saved_by`, `ownership.archived_by`).
  - Atomic write-behind local JSON backup into `brains/<reel_id>.json`.
- **`storage/user.py`**:
  - User records in MongoDB `users` collection storing user preferences and Microsoft token caches.
- **`storage/publication.py`**:
  - Idempotent OneNote publication slot claiming: avoids duplicate OneNote pages across job retries or concurrent worker runs.

### 2.6 Microsoft Graph Integration & Authentication (`auth/`, `onenote/`)
- **`auth/oauth_server.py` & `auth/state.py`**:
  - Public-client OAuth 2.0 authorization code flow with PKCE (`code_verifier`, `code_challenge`).
  - HMAC-signed state tokens ensure anti-CSRF protection and correlate callback codes to Telegram user IDs.
  - MSAL token cache stored directly in MongoDB `users.microsoft.token_cache`.
- **`onenote/graph_client.py` & `onenote/writer.py`**:
  - Interacts with Microsoft Graph REST API (`/me/onenote/notebooks`, `/sections`, `/pages`).
  - Creates notebook `InstaBrain` with category-based sections.
  - Formats rich HTML notes with metadata headers, styled markdown blocks, code formatting, and embedded thumbnails.

---

## 3. Directory Layout

```
ReelForge/
│
├── .reelforge/                  # Canonical Project Brain (living agent engineering record)
│   ├── PROJECT_STATE.md         # Current single source of truth
│   ├── ACTIVE_TASK.md           # Recovery checkpoint for current task
│   ├── ARCHITECTURE.md          # This architectural blueprint
│   ├── DECISIONS.md             # Persistent ADR decisions & constraints
│   ├── AGENT_PROTOCOL.md        # Agent operating protocol & rules
│   ├── CHANGELOG.md             # High-level version changelog
│   ├── VERSION_INDEX.md         # Index of historical version records
│   └── versions/                # Continuous per-version history files
│       ├── V1.0.md
│       ├── V2.3.md
│       ...
│       └── V3.6.2.md
│
├── AGENTS.md                    # Root entrypoint instruction pointing agents to .reelforge/
├── config.py                    # Root configuration & environment variable resolution
├── main.py                      # CLI entrypoint for local execution
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Cloud worker container definition
├── WORKER_OPERATIONS.md         # Production worker runtime specification
│
├── auth/                        # Microsoft OAuth PKCE & callback handling
│   ├── oauth_server.py
│   └── state.py
│
├── bot/                         # Telegram Bot interface & notification subsystem
│   ├── telegram_bot.py
│   └── notifier.py
│
├── download/                    # Media downloader
│   └── downloader.py
│
├── onenote/                     # Microsoft OneNote publishing subsystem
│   ├── graph_client.py
│   ├── writer.py
│   ├── formatter.py
│   └── sanitizer.py
│
├── processing/                  # Media extraction & worker pipeline
│   ├── pipeline.py              # Orchestrator
│   ├── worker.py                # Standalone durable job worker
│   ├── worker_workspace.py      # Ephemeral workspace isolation generator
│   ├── transcriber.py           # Whisper audio transcription
│   ├── vision_analyzer.py       # Multimodal visual extraction
│   ├── categorizer.py           # Domain categorizer
│   ├── dispatcher.py            # Extractor dispatch logic
│   └── *_extractor.py           # Domain-specific knowledge extractors
│
├── prompts/                     # LLM prompt templates (path-independent via PROMPTS_DIR)
│   ├── categorizer.txt
│   ├── vision_analyzer.txt
│   └── *_extractor.txt
│
├── storage/                     # Data access & persistence layer
│   ├── db.py                    # MongoDB Atlas connection & indexing
│   ├── brain_object.py          # Brain Object operations & write-behind backup
│   ├── user.py                  # User profiles & credentials
│   ├── job.py                   # Durable job queue operations
│   ├── publication.py           # OneNote publication idempotency slots
│   └── oauth_session.py         # In-flight OAuth sessions
│
├── brains/                      # Local JSON backup of Brain Objects (*.json)
├── reels/                       # Ephemeral media scratch (WORKSPACE_DIR)
│   └── workers/                 # Isolated worker sub-directories (w-<pid>-<hex>/)
└── scratch/                     # Test suites, audits, and evaluation scripts
```
