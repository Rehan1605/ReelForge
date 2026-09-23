# ReelForge V3.4/V3.5/V3.6 — Worker Runtime & Operations Contract

> A minimal specification of what a fresh ReelForge worker environment requires,
> based on the portability work completed in V3.4 Layers 1–4, the durable
> job queue + independent worker process completed in V3.5 Layers 1–2, and the
> worker workspace isolation completed in V3.6 Layer 2. It formalizes the
> runtime contract for a Linux/cloud worker without choosing a hosting provider
> or deploying anything.

Companion file: `WORKER.env.example` (safe-to-commit config example with **no**
real credentials).

---

## 1. Architecture Snapshot (the contract's invariants)

| Concern | Contract |
|---|---|
| Brain Objects & user state | **MongoDB Atlas is the authoritative persistent store.** |
| Per-user Microsoft token caches | Stored **in MongoDB** (`users.microsoft.token_cache`). |
| Reel media (mp4 / thumbnail / info.json) | **Ephemeral, worker-local** scratch; each standalone worker automatically gets its own isolated sub-directory (`WORKSPACE_DIR/workers/<worker-id>/`). Deleted after processing (`KEEP_VIDEOS=False`), safe to lose on restart. Operators do **not** need to configure separate workspace paths per worker — isolation is automatic (V3.6 Layer 2). |
| Brain JSON (`brains/*.json`) | **Backup / fallback only.** Unmodified by the worker except as write-behind backup + offline/test fallback. Mongo routing keys off the dir name (see §2). |
| `token_cache.bin` | **Legacy V2 only** (used solely when `GraphClient(user_id=None)`). **Never a worker requirement.** |
| OAuth `/connect` | **Laptop-side loopback flow** for now. A worker only *consumes* per-user token caches from Mongo; it never serves the OAuth callback. |
| OmniRoute | **Externally reachable/configurable dependency** (`OMNIROUTE_BASE_URL`). The worker contract does **not** assume localhost. |

---

## 2. Environment Files & Loading Behavior

`config.py` reads every variable from the process environment, then
`setdefault`s values from two repo files if present:

1. `<repo-root>/.env`
2. `<repo-root>/atlas-credentials.env`

In the container the Docker image **excludes** `.env` and
`atlas-credentials.env` (`.dockerignore`), so a worker must inject every
required variable via **real environment variables / a secrets manager** —
never by copying the laptop's files. Values already present in the environment
win over the dotfiles, and `WORKSPACE_DIR`/`BRAINS_DIR`/`PROMPTS_DIR` support
absolute, env-overridable paths (V3.4 Layer 1).

**Routing nuance (Layer 4):** `storage.brain_object` treats MongoDB as primary
only while `BRAINS_DIR` is the default repo path (or a path ending in
`/brains`). A worker must therefore keep `BRAINS_DIR` at `<ROOT>/brains` (or a
`…/brains` dir) so Mongo stays the source of truth instead of silently falling
back to ephemeral local files.

---

## 3. Required Environment Variables

| Variable | Secret | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Telegram Bot API token (`bot/telegram_bot.py`). |
| `MICROSOFT_CLIENT_ID` | yes | Azure public-client app ID (OneNote Graph publishing). |
| `MONGODB_URI` | yes | Atlas connection string. Required for authoritative persistence. |
| `OMNIROUTE_BASE_URL` | no | AI gateway base URL (`…/v1`). Must be reachable from the worker. |

`MONGODB_URI` is technically optional (default `""` → local-file-only mode), but
in the worker contract it is **effectively required**: without it the system
reverts to ephemeral/local-only storage and loses the authoritative-store
guarantee.

## 4. Optional Environment Variables

| Variable | Default (config.py) | Notes |
|---|---|---|
| `OMNIROUTE_API_KEY` (secret) | `""` | Bearer for gateway; empty = unauthenticated. Falls back to `OPENAI_API_KEY`. |
| `MONGODB_DB_NAME` | `reelforge` | Database name. |
| `TEXT_MODEL` | `qwen2.5:7b-instruct` | Text/categorization/extraction model. |
| `EVALUATION_MODEL` | `TEXT_MODEL` | Evaluation judge model. |
| `VISION_MODEL` | `llama3.2-vision:latest` | Vision model. |
| `VISION_MAX_FRAMES` | `8` | Frames sampled for vision (1–8+; clamped ≥1). |
| `WHISPER_MODEL` | `base` | Whisper transcription model. |
| `WHISPER_CACHE_DIR` | none (`~/.cache/whisper`) | Persistent volume for the model weight cache. |
| `REELFORGE_EMBEDDED_WORKER` | `1` (on) | V3.5 L2. `0`/`false`/`no` disables the in-process worker inside `bot/telegram_bot.py`, so a separate `python -m processing.worker` process does the claiming. Keep `1` for single-process local development. |
| `FFMPEG_PATH` | Windows build dir on `nt`, `""` elsewhere | Empty → resolve `ffmpeg`/`ffprobe` from system PATH. |
| `WORKSPACE_DIR` | `<ROOT>/reels` | Media scratch. All standalone workers share this root; isolation sub-dirs are created automatically (see §9). |
| `BRAINS_DIR` | `<ROOT>/brains` | Brain JSON backup/fallback. Keep `…/brains` for Mongo-primary routing. |
| `PROMPTS_DIR` | `<ROOT>/prompts` | Prompt templates; only set if bundled elsewhere. |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | — | Compatibility aliases used as fallbacks for the OmniRoute vars. |
| `MICROSOFT_TENANT_ID`, `MICROSOFT_AUTHORITY`, `OAUTH_REDIRECT_URI`, `OAUTH_HOST`, `OAUTH_LISTEN_PORT`, `OAUTH_BASE_URL`, `OAUTH_STATE_SECRET` (secret) | see `config.py` | Only needed on the device running `/connect` (laptop-side). Not required for a worker. |

**Not required / legacy on a worker:** `token_cache.bin`, `sessions/`,
`MONGODB_USERNAME`, `MONGODB_PASSWORD`, and the repo dotfiles themselves.

---

## 5. External Service Dependencies (connectivity)

| Service | Direction | Purpose |
|---|---|---|
| MongoDB Atlas cluster (`mongodb+srv://…` via `MONGODB_URI`) | outbound | Authoritative brains/users/oauth_sessions store. |
| OmniRoute gateway (`OMNIROUTE_BASE_URL`) | outbound | All text/vision/evaluation LLM calls (`POST {base}/chat/completions`). |
| `api.telegram.org` | outbound | Telegram polling (443). |
| Instagram + media CDNs | outbound | `yt-dlp` download. |
| Microsoft `login.microsoftonline.com` + `graph.microsoft.com` | outbound | Per-user token refresh + OneNote page creation. |
| Whisper model endpoint (HuggingFace mirror) | outbound, first run only | Model download into `WHISPER_CACHE_DIR`. Pre-cache the weights on a persistent volume to make restart offline-instant. |

## 6. Required Ports

- **No inbound ports.** The Telegram bot uses outbound polling; `/connect`'s
  loopback callback server (`OAUTH_HOST:OAUTH_LISTEN_PORT`, default
  `127.0.0.1:8080`) is **laptop-side only** and must not run on a cloud worker.
- All other dependencies are outbound HTTPS (see §5).

---

## 7. Persistent vs Ephemeral Data

**Persistent (must survive worker restart — and does, because it is remote):**
- MongoDB Atlas: `brains` (Brain Objects), `users` (incl. `microsoft.token_cache`),
  `oauth_sessions` (laptop-side sessions, TTL-expiring).
- Whisper model weights on `WHISPER_CACHE_DIR` (optional but recommended).

**Ephemeral (safe to lose on restart):**
- `WORKSPACE_DIR/workers/<worker-id>/` media (mp4, thumbnail, `*.info.json`) —
  cleaned by the pipeline at job completion (`_cleanup_reel_media`).
- Vision frame temp dirs (`tempfile.mkdtemp`, system temp) — cleaned in `finally`.
- System-temp audio used by Whisper/FFmpeg.
- Worker-local `brains/` JSON **if** not configured per §2 — replaced from Mongo on demand.
- Orphaned `WORKSPACE_DIR/workers/` sub-directories from crashed workers — safe
  to remove manually or on next fresh container start.

**Never required to persist:** `token_cache.bin`, `sessions/`, `evaluation/results/`.

**Media paths inside the Brain Object are worker-local (V3.6 Layer 1 audit + Layer 2):**
- `brain.media.video_path` and `brain.media.thumbnail_path` are runtime-local
  file paths captured at download time. With V3.6 Layer 2 they point into the
  worker's isolated sub-directory (`WORKSPACE_DIR/workers/<worker-id>/`).
  They are valid only on the worker that downloaded the reel and only until
  that worker's media is cleaned up. After cleanup they become dangling references.
- They describe where a worker put the media — **never** durable media URLs,
  **never** proof the media still exists, and never something a fresh worker
  should treat as readable.
- Durable state is the `brains` document in MongoDB plus the published OneNote
  page; neither depends on these paths.
- If a future feature ever re-serves media, it must introduce a durable media
  URL scheme (e.g., object storage) and stop relying on these worker-local paths.

---

## 8. Startup Command

```bash
# Single-process local development (default; bot runs its own worker task)
REELFORGE_EMBEDDED_WORKER=1 python bot/telegram_bot.py

# Independent processes (V3.5 Layer 2) — bot without a worker, plus one or
# more standalone workers. MongoDB `reel_jobs` is the only communication
# channel; workers claim jobs atomically, so N workers are safe concurrently.
REELFORGE_EMBEDDED_WORKER=0 python bot/telegram_bot.py
python -m processing.worker                # standalone worker #1
python -m processing.worker                # standalone worker #2 (optional)

# Single-reel CLI (non-interactive; evaluation/CI)
python main.py "https://www.instagram.com/reel/EXAMPLE_CODE/"

# Evaluation
python -m evaluation.runner --reel <reel_id> | --all | --report <run>
```

On Windows/PowerShell use `$env:REELFORGE_EMBEDDED_WORKER="0"` before the
command. The standalone worker uses its own `telegram.Bot` client (from
`TELEGRAM_BOT_TOKEN`, env only — never job docs) purely to send progress/result
messages; it never runs an Application/polling loop.

Docker image (`Dockerfile`): `CMD ["python", "bot/telegram_bot.py"]`, CPU-only
torch, `ffmpeg` + `libgomp1` installed via apt. A fresh worker: clone/import
image → inject env (never the laptop's `.env`) → ensure `ffmpeg` on PATH →
start. First `process_reel` downloads the Whisper model into `WHISPER_CACHE_DIR`.

## 9. V3.6 Layer 2 — Worker Workspace Isolation

### Design

Each **standalone worker** (`python -m processing.worker`) automatically
activates a private, process-lifetime media workspace before entering the job
loop. No operator configuration is required.

Directory layout:

```
WORKSPACE_DIR/
    workers/
        w-<pid>-<random>/    ← this worker's exclusive media scratch
            <reel_id>.mp4
            <reel_id>.jpg
            <reel_id>.info.json
        w-<pid2>-<random>/   ← another concurrent worker's scratch
            …
    (flat root files — embedded / CLI mode only)
```

### Worker Identity

Each worker ID is generated once at activation:

```
w-<os.getpid()>-<secrets.token_hex(4)>
```

- PID anchors the ID to the process; the 4-byte random hex suffix ensures
  uniqueness even if two workers start with the same PID (restart after crash).
- The ID is filesystem-safe (alphanumeric + dashes only) on all platforms.
- The ID is never exposed to Telegram users and is never stored in Brain Objects
  or MongoDB.

### Activation rules

| Mode | Activation | Workspace |
|---|---|---|
| Standalone worker (`python -m processing.worker`) | Automatic, before the job loop | `WORKSPACE_DIR/workers/<worker-id>/` |
| Embedded worker (default local dev, `REELFORGE_EMBEDDED_WORKER=1`) | Not activated | Flat `WORKSPACE_DIR` |
| CLI / `main.py` | Not activated | Flat `WORKSPACE_DIR` |

`activate_worker_workspace()` is idempotent and thread-safe (double-checked
lock). Repeated calls within one process always return the same path.

### Isolation boundary

| Artifact | Location | Isolated? |
|---|---|---|
| Reel mp4 / thumbnail / info.json | `WORKSPACE_DIR/workers/<worker-id>/` | ✅ Worker-local |
| Brain Objects (JSON backup) | `BRAINS_DIR` (shared) | ✅ Shared, unaffected |
| Brain Objects (MongoDB) | Atlas `brains` collection | ✅ Shared, unaffected |
| Whisper model weights | `WHISPER_CACHE_DIR` (separate) | ✅ Shared, unaffected |
| Vision frame temp dirs | `tempfile.mkdtemp()` (system temp) | ✅ Process-local by OS |
| OneNote publication records | `brain.publications.*` in MongoDB | ✅ Shared, unaffected |

### Cleanup

`_cleanup_reel_media(reel_id)` globs only inside `effective_workspace()` —
the current worker's sub-directory. It uses `<reel_id>.*` (exact match before
the dot) so:
- It cannot delete another worker's files.
- It cannot delete a file whose name starts with the reel ID (e.g., `1234.mp4`
  is not deleted when cleaning up reel `123`).
- `KEEP_VIDEOS=True` disables all cleanup as before.

### Hard-crash orphan files

A SIGKILL or container preemption that interrupts the `finally` clause leaves
orphan media in `WORKSPACE_DIR/workers/<worker-id>/`. This is intentional and
acceptable: the files are ephemeral, carry no durable state, and may be removed
manually or on the next container start. No cleanup daemon is provided.

### Embedded / local development compatibility

Existing local workflows — `python bot/telegram_bot.py`, `python main.py`,
`python -m evaluation.evaluate` — are completely unaffected. They never call
`activate_worker_workspace()`, so `effective_workspace()` returns the flat
`WORKSPACE_DIR` exactly as before.

### Operator checklist additions (V3.6)

- `WORKSPACE_DIR` must be writable. Worker sub-directories are created
  automatically; no per-worker directory pre-creation is needed.
- A single shared `WORKSPACE_DIR` value is safe across all parallel worker
  processes — automatic isolation prevents any collision.
- Periodically sweep `WORKSPACE_DIR/workers/` for orphaned sub-directories
  from crashed workers (or let the OS ephemeral disk handle it on restart).

---

## 10. Shutdown / Restart Expectations

- Stop the process (SIGINT/SIGTERM/SIGKILL) at any point. The pipeline's media
  cleanup (`_cleanup_reel_media`) only runs on graceful completion paths; a hard
  kill may leave media in the worker's isolated workspace sub-directory — that
  is fine, it is ephemeral and may be wiped manually or on the next container
  start.
- A hard kill (SIGKILL, power loss, container preemption) can bypass `finally`
  cleanup entirely, so orphaned ephemeral artifacts may remain:
    - Reel media in `WORKSPACE_DIR/workers/<worker-id>/` (mp4, thumbnail, `*.info.json`).
    - Vision frame temp dirs (`reelforge_vision_*`, `reelforge_frames_*`) under
      the system temp directory.
  These are safe orphaned artifacts carrying no durable Reel state. Worker
  sub-directories for dead workers (absent after a container restart) may be
  deleted manually. A cleanup daemon is not provided — this is intentional.
- A crash never invalidates MongoDB Brain Objects and never leaves
  partially-written Brain JSON (both use the safe persistence mechanisms
  below), and it never invalidates an already-recorded OneNote publication.
- Future deployments can therefore run workers on ephemeral disks without
  treating these files as persistent state.
- Brain Object writes are atomic (temp-file + `os.replace`) and Mongo-upserted,
  so an interrupted job leaves no half-written state.
- On restart, `get_cached_brain_object` short-circuits already-processed reels
  from Mongo; `KEEP_VIDEOS=False` means every (re)run re-downloads fresh media.
- Recommended: give each worker a **unique `WORKSPACE_DIR`** so parallel workers
  never share media scratch.
  **V3.6 Layer 2 note:** standalone workers (`python -m processing.worker`)
  automatically receive an isolated sub-directory (`WORKSPACE_DIR/workers/<worker-id>/`)
  and do not require a manually unique `WORKSPACE_DIR`. Operators can share one
  `WORKSPACE_DIR` value across all worker processes — the isolation is handled
  at runtime with no operator configuration required.

## 11. Health / Readiness Considerations

There is no HTTP health endpoint (polling bot). Suggested pre-flight assertions
for a fresh worker:

1. **Config loads** — all required env vars present (else `config.py` raises at import).
2. **Mongo reachable** — `python storage/db.py` (pings Atlas, lists collections; masks URI).
3. **OmniRoute reachable** — a small request to `OMNIROUTE_BASE_URL` (OpenAI-compatible; verify `POST …/v1/chat/completions` or the models listing responds before enablement).
4. **Whisper model cached** — `WHISPER_CACHE_DIR` non-empty / on a persistent volume.
5. **Media scratch writable** — `WORKSPACE_DIR` exists and is writable. Worker sub-directories are created automatically; no per-worker directory pre-creation is needed.
6. **FFmpeg present** — `ffmpeg -version` / `ffprobe -version` resolve from PATH.
7. **Telegram reachable** — bot's `run_polling()` successfully connects.

---

## 12. Can a Fresh Linux Worker Start From This Contract?

**Yes (theoretically), with the checklist above.** All laptop-specific assumptions
have been removed or made optional in Layers 1–4:
- `WORKSPACE_DIR`/`BRAINS_DIR`/`PROMPTS_DIR` are absolute, env-overridable, root-anchored.
- `FFMPEG_PATH` defaults to `""` (system PATH) on non-Windows.
- `token_cache.bin`, `sessions/`, `MONGODB_USERNAME`/`MONGODB_PASSWORD` are not required.
- The only `localhost` default left is `OMNIROUTE_BASE_URL`, which the contract
  says must be overridden to a reachable gateway URL.

Remaining operational caveats (not blockers): keep `BRAINS_DIR` as `…/brains`
(Mongo routing heuristic, §2), and supply `OAUTH_STATE_SECRET` + the OAuth
callback machine only where `/connect` is served (laptop).

---

## 13. Done vs Not In Scope

**Completed in V3.5:** durable `reel_jobs` MongoDB queue (partial-unique
`(claim_key, active)` dedupe guard, atomic claims, lease-based crash recovery,
`queued → processing → completed | failed`), standalone worker
(`python -m processing.worker`) with its own Telegram notifier, and a
`REELFORGE_EMBEDDED_WORKER` flag for single-process local development.

**Completed in V3.6 Layer 2 (worker workspace isolation):** each standalone
worker process automatically receives a unique, isolated ephemeral media
directory at `WORKSPACE_DIR/workers/w-<pid>-<random>/`. All pipeline components
(downloader, transcriber, vision analyzer, cleanup) resolve the correct directory
via `processing.worker_workspace.effective_workspace()`. `BRAINS_DIR` and
`WHISPER_CACHE_DIR` are deliberately outside the isolated workspace and remain
shared. Embedded / local-dev mode is unaffected — it continues to use the flat
`WORKSPACE_DIR` directly. Thread-safe activation uses double-checked locking so
concurrent startup within a single process is safe. Operators do not need to
configure per-worker paths.

**Not in scope (future layers):** media re-serving to users, provider selection,
and deployment/orchestration (supervisor/systemd, container scheduling, worker
scaling policy).