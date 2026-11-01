# ReelForge V3.6.4 — Phase 2: Cloud Architecture & Gap Analysis

> **Phase 2 Architecture Specification & Gap Analysis**  
> **Status**: COMPLETED (Planning Only — Read-Only)  
> **Target System**: ReelForge V3.6.4 (`23cc846`)  
> **Core Objective**: 24/7 Autonomous Cloud Operation (Laptop Independent)  
> **Authoritative Persistence**: MongoDB Atlas  

---

## 1. Executive Architecture Summary

ReelForge V3.6.4 is structurally well-positioned for cloud migration. Previous engineering layers (V3.4 through V3.6.4) systematically introduced headless abstractions:
* MongoDB Atlas as the single authoritative store for users, brain objects, token caches, and durable jobs.
* Ephemeral media workspace isolation per worker process (`WORKSPACE_DIR/workers/w-<pid>-<random>/`).
* Lease-based job recovery and idempotent OneNote publication slots.
* Non-interactive MSAL silent token refresh for users already connected via Microsoft OAuth.
* Decoupled architecture supporting both embedded (single-container) and standalone worker execution.

The objective of Phase 2 is to define the target cloud architecture, evaluate process separation trade-offs, compare realistic hosting providers, systematically analyze every configuration and dependency gap, plan for Instagram download reliability, size compute resources, and outline a risk-managed, staged migration path.

---

## 2. Target Cloud Architecture

The target architecture enables ReelForge to run 24/7 on a headless cloud host without requiring any developer intervention, open workstation sessions, or GUI displays.

```
                              +---------------------------------------+
                              |         Telegram Bot API              |
                              |         (api.telegram.org)            |
                              +-------------------+-------------------+
                                                  ^
                                                  | Outbound HTTPS Polling
                                                  v
+--------------------------------------------------------------------------------------------------+
|  ReelForge Cloud Runtime Host (Docker / Linux VPS)                                               |
|                                                                                                  |
|  +--------------------------------------------------------------------------------------------+  |
|  |  Ingress Layer (Optional / Conditional for /connect)                                       |  |
|  |  - Caddy / Nginx Reverse Proxy OR Cloudflare Tunnel (HTTPS :443)                           |  |
|  |  - Forwards /auth/callback to internal port 8080                                           |  |
|  +----------------------------------------------+---------------------------------------------+  |
|                                                 | (Loopback/Container Network)                   |
|                                                 v                                                |
|  +--------------------------------------------------------------------------------------------+  |
|  |  ReelForge Application Service(s)                                                          |  |
|  |                                                                                            |  |
|  |  [ Telegram Bot ]  <----------------------------+                                          |  |
|  |  - bot/telegram_bot.py                          |                                          |  |
|  |  - Ingestion, commands, status                  |                                          |  |
|  |  - Optional embedded OAuth server (:8080)       |                                          |  |
|  |                                                 v                                          |  |
|  |  [ Durable Processing Worker ] <=======> [ MongoDB Atlas (Cloud) ]                         |  |
|  |  - processing/worker.py                  - brains (Brain Objects)                          |  |
|  |  - Atomic claim & lease renewal          - users (MSAL Token Caches)                       |  |
|  |  - Ephemeral media workspace             - reel_jobs (Durable Queue)                       |  |
|  |                                          - oauth_sessions (TTL State)                      |  |
|  |         |                                                                                  |  |
|  |         v                                                                                  |  |
|  |  [ Processing Pipeline ]                                                                   |  |
|  |  - yt-dlp  -------------> Outbound HTTPS --> Instagram / Meta CDNs                         |  |
|  |  - Whisper (CPU) -------> Local PyTorch (Cached weights in /cache/whisper)                 |  |
|  |  - Vision Analyzer -----> Outbound HTTPS --> Remote OmniRoute / Vision Gateway (OpenAI-API) |  |
|  |  - Categorizer/Extract -> Outbound HTTPS --> Remote OmniRoute / Text LLM Gateway            |  |
|  |  - OneNote Writer ------> Outbound HTTPS --> Microsoft Graph API (token refreshed silently)|  |
|  +--------------------------------------------------------------------------------------------+  |
+--------------------------------------------------------------------------------------------------+
```

### Component Communication & Invariant Rules:
1. **Telegram Interface**: Connects to `api.telegram.org` exclusively via outbound long-polling (`app.run_polling()`). No open inbound firewall ports or public IP are needed for bot messaging.
2. **Authoritative Coordination**: The bot and workers communicate exclusively through MongoDB Atlas collections (`reel_jobs` queue, `brains` store).
3. **AI Inference**: Dispatched via standard outbound HTTP/HTTPS calls using the OpenAI-compatible `/chat/completions` schema to an external or hosted gateway (`OMNIROUTE_BASE_URL` or `VISION_BASE_URL`).
4. **Media Ephemeral Scratch**: Media is downloaded into isolated scratch directories and deleted immediately upon job completion (`KEEP_VIDEOS=False`).
5. **OneNote Publishing**: Outbound HTTPS calls to Microsoft Graph (`graph.microsoft.com`). Tokens are refreshed headlessly using stored refresh tokens in MongoDB Atlas without browser interaction.
6. **Microsoft OAuth `/connect` Ingress**: If enabled, external browser redirects hit a public HTTPS endpoint, which proxies to the internal OAuth callback server on port 8080.

---

## 3. Process and Service Separation Analysis

ReelForge V3.6 contains built-in toggles (`REELFORGE_EMBEDDED_WORKER` and `REELFORGE_START_OAUTH_SERVER`) designed to support multiple deployment topologies.

### Option A: Unified Single-Container Architecture (Recommended for Initial Release)
* **Configuration**: `REELFORGE_EMBEDDED_WORKER=1`, `REELFORGE_START_OAUTH_SERVER=1` (or `0` if new-user onboarding is deferred).
* **Topology**: One container running `python bot/telegram_bot.py`. The bot initializes:
  1. Telegram long-polling loop in the main asyncio thread.
  2. The durable job worker as an in-process background `asyncio` task (`worker_loop()`) executing pipeline jobs in worker threads.
  3. The OAuth callback server in a background daemon thread.
* **Trade-offs**:
  * **Pros**:
    * Lowest operational complexity: Exactly one container, one service definition, one log stream.
    * Memory efficiency: PyTorch and Python runtime are loaded only once. Total memory footprint is ~1.2GB–1.8GB, fitting comfortably on a budget 2GB–4GB RAM VPS.
    * Zero inter-process network configuration.
  * **Cons**:
    * If a catastrophic crash or unhandled C-extension fault occurs in PyTorch/Whisper, the bot process also restarts (mitigated by Docker `restart: unless-stopped` and MongoDB lease recovery).
    * Heavy CPU transcription jobs execute on the same instance, requiring sufficient vCPU allocation so Telegram polling does not experience lag.

### Option B: Decoupled Multi-Service Architecture (Scale-Out Architecture)
* **Configuration**: `REELFORGE_EMBEDDED_WORKER=0`.
* **Topology**: Orchestrated via `docker-compose.yml`:
  * Service 1: `bot` (`python bot/telegram_bot.py`, `REELFORGE_START_OAUTH_SERVER=1`) — handles commands, submissions, and OAuth callbacks.
  * Service 2: `worker-1` (`python -m processing.worker`) — claims jobs from MongoDB Atlas, executes pipeline, notifies Telegram via Bot API client.
  * Service 3 (optional): `worker-2` — second parallel worker for high volume.
* **Trade-offs**:
  * **Pros**:
    * Strict failure isolation: Worker crashes do not interrupt Telegram commands or OAuth callbacks.
    * Independent horizontal scaling: Workers can be scaled dynamically (`docker compose up --scale worker=2`).
  * **Cons**:
    * Duplicated RAM overhead: Each worker process loads PyTorch and Whisper model weights into memory (~1GB per worker). Two workers + bot requires at least 3GB–4GB of dedicated RAM.
    * Multiple logs and container lifecycles to supervise.

### Recommendation
**Deploy Option A (Unified Single-Container) first.** ReelForge is currently a personal/small-group system. Option A minimizes hosting costs, simplifies monitoring, and leverages V3.6.3's managed lifecycle. Because the durable job queue in MongoDB Atlas already exists, upgrading to Option B later requires only setting `REELFORGE_EMBEDDED_WORKER=0` and running a second container, with zero code modifications.

---

## 4. Cloud Hosting Options & Cost Comparison

To identify the most practical, maintainable, and cost-conscious infrastructure, three deployment approaches are compared:

| Evaluation Metric | Option 1: Budget Linux VPS (Hetzner / DigitalOcean / Lightsail) + Docker | Option 2: Managed Container Platform (Railway / Render / Fly.io) | Option 3: Serverless / Cloud Run / AWS Fargate |
|---|---|---|---|
| **Representative Provider & Tier** | **Hetzner Cloud CX23** (2 vCPU, 4GB RAM, 40GB NVMe) OR **DigitalOcean Basic** (2 vCPU, 2GB/4GB RAM) | **Railway Pro** or **Render Individual** (Resource metered) | **AWS ECS Fargate** or **GCP Cloud Run** (Always-on container) |
| **Estimated Monthly Cost (2026)** | **$6.00 – $15.00 / month**<br>*(Hetzner CX23 is ~€5.49/mo + €0.50 IPv4; DO 2GB is $18/mo, 4GB is $24/mo; Lightsail 2GB is $10/mo)* | **$20.00 – $45.00+ / month**<br>*(Billed continuously: ~$10/GB RAM + $20/vCPU/mo. 1.5GB RAM 24/7 worker costs $15–$25+)* | **$25.00 – $50.00+ / month**<br>*(0.5 vCPU + 2GB RAM 24/7 Fargate is ~$22-$30/mo + Egress/Logs)* |
| **CPU/RAM Suitability for Whisper** | **High** (Dedicated 2 vCPU + 4GB RAM allows fast CPU Whisper transcription without OOM) | **Moderate** (Can scale, but CPU/RAM limits incur steep continuous billing) | **Moderate** (Cold starts, high cost for continuous vCPU allocation) |
| **Persistent Volume / Cache** | **Native local NVMe disk** (Bind-mount host dir for `/cache/whisper`; zero extra cost) | **Add-on volume** (Typically $0.15–$0.25/GB/mo; ephemeral by default) | **EFS mount** (Complex setup, additional AWS storage latency/cost) |
| **Background Worker Model** | **Native** (Runs Docker containers 24/7 via systemd / Docker restart policy) | **Treated as web service** (Billed 24/7 for compute even when queue is idle) | **Poor fit** (Serverless prefers HTTP request-driven triggers over long-polling) |
| **HTTPS & Ingress Setup** | **Simple** (Run Caddy container or Cloudflare Tunnel with zero open ports) | **Built-in** (Automatic HTTPS domain provided by platform) | **Complex** (Requires ALB/API Gateway, certificates, VPC setup) |
| **Operational Maintenance** | Moderate (Basic Linux security, Docker updates, unattended-upgrades) | Very Low (Git push deployments) | High (Cloud IAM, VPC routing, cloudwatch) |
| **Vendor Lock-In** | **None** (Standard Docker Compose runs on any Linux host) | Low-to-Moderate (Proprietary platform config) | High (AWS/GCP specific IAM/manifests) |

### Hosting Verdict: Option 1 (Budget Linux VPS)
* A budget Linux VPS (e.g. **Hetzner CX23** at ~€6/month or **AWS Lightsail 2GB** at $10/month) is the most cost-effective and architecturally suited option.
* ReelForge's background long-polling and continuous memory residency (PyTorch/Whisper) make metered container platforms 2x–4x more expensive with no functional benefit.

---

## 5. Laptop Dependency & Configuration Gap Analysis

Every gap identified in Phase 1 has been validated against active repository code:

| # | Gap & Current Behavior | Cloud Impact | Severity | Proposed Resolution | Code Change Required? | Validation Method |
|---|---|---|---|---|---|---|
| **G-01** | `OMNIROUTE_BASE_URL` defaults to `http://localhost:20128/v1` in [`config.py:83`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/config.py#L83). | System fails to connect to LLM in cloud if unset. | **CRITICAL** | Set `OMNIROUTE_BASE_URL` in production environment to external/reachable gateway URL. | **No** (Config only). | Preflight probe `curl {OMNIROUTE_BASE_URL}/models` or pipeline test. |
| **G-02** | `OAUTH_REDIRECT_URI` defaults to `http://localhost:8080/auth/callback` in [`config.py:50`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/config.py#L50). | User `/connect` link directs mobile/browser to client's own device localhost, breaking auth. | **HIGH** *(Critical if new users onboard in cloud)* | Set `OAUTH_REDIRECT_URI=https://<domain>/auth/callback` in cloud env; register URI in Azure Portal. | **No** (Config only). | Inspect generated URL in `/connect` and test browser callback. |
| **G-03** | `OAUTH_HOST` defaults to `127.0.0.1` in [`config.py:54`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/config.py#L54). | In Docker, binding to loopback prevents external reverse proxy from forwarding traffic. | **HIGH** *(if OAuth enabled)* | Set `OAUTH_HOST=0.0.0.0` in cloud container environment. | **No** (Config only). | Container port probe from host network. |
| **G-04** | Dedicated Vision Gateway (`VISION_BASE_URL`, `VISION_API_KEY`). | Defaults to empty in [`config.py:88-89`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/config.py#L88-L89), falling back to `OMNIROUTE_*`. | **MEDIUM** | If vision model is hosted on a separate endpoint (e.g. Gemini API), provide `VISION_BASE_URL` and `VISION_API_KEY`. | **No** (Already built in V3.6.4). | Run `python -m processing.vision_analyzer --check-gateway`. |
| **G-05** | `FFMPEG_PATH` WinGet fallback in [`config.py:70-75`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/config.py#L70-L75). | Windows hardcoded path ignored on Linux; defaults to `""` (system PATH). | **LOW** | System `ffmpeg` installed via `apt-get` in [`Dockerfile:17`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/Dockerfile#L17). Verified working. | **No**. | Verify `ffmpeg -version` inside container. |
| **G-06** | `BRAINS_DIR` MongoDB bypass heuristic in [`storage/brain_object.py:83-93`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/storage/brain_object.py#L83-L93). | Bypasses MongoDB if `BRAINS_DIR` doesn't end in `/brains`. | **HIGH** *(Operational Hazard)* | Ensure cloud deployment leaves `BRAINS_DIR` at default or an explicit path ending in `/brains`. | **No** (Operational constraint). | Unit test `test_v34_portability.py`. |
| **G-07** | Whisper Model Download on Restart. | Defaults to `~/.cache/whisper`. Fresh containers redownload 140MB on boot. | **MEDIUM** | Set `WHISPER_CACHE_DIR=/cache/whisper` and mount a persistent Docker volume. | **No** (Deployment/compose config). | Check `/cache/whisper` persistence across container recreation. |
| **G-08** | Headless CLI MSAL Interactive Fallback in [`onenote/graph_client.py:118`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/onenote/graph_client.py#L118). | Standalone CLI `main.py` opens local browser window if `user_id` is None and cache is empty. | **MEDIUM** | Bot and Worker pass `user_id` (headless silent refresh). Do not run headless unauthenticated `main.py` without user context. | **No** (Operational constraint). | Bot/worker test suites pass without GUI. |
| **G-09** | Secret files excluded by `.dockerignore` (`.env`, `atlas-credentials.env`). | Container fails to start if environment variables are not injected at runtime. | **HIGH** | Supply all required secrets (`TELEGRAM_BOT_TOKEN`, `MONGODB_URI`, `MICROSOFT_CLIENT_ID`, etc.) via environment file or secret manager. | **No** (Standard practice). | Container start sanity check. |
| **G-10** | Legacy `OLLAMA_MODEL = "llama3.2-vision"` in [`config.py:120`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/config.py#L120). | Dead code; not imported or referenced anywhere. | **LOW** | Informational; no operational impact. | **No** (Clean up in future maintenance). | Grep search confirmed zero imports. |

---

## 6. Instagram Download Reliability & Datacenter IP Analysis

Media acquisition via `yt-dlp` represents the highest external operational risk for cloud deployment.

### 6.1 The Datacenter IP Risk
* **Current Behavior**: [`download/downloader.py:27-38`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/download/downloader.py#L27-L38) invokes `yt-dlp` unauthenticated with standard user-agent strings.
* **Problem**: Meta/Instagram heavily monitors Autonomous System Numbers (ASNs) belonging to cloud hosting providers (AWS, GCP, DigitalOcean, Hetzner, OVH). Requests from datacenter IPs frequently encounter:
  1. `HTTP 401 Unauthorized` / "Login required".
  2. `HTTP 429 Too Many Requests` (aggressive IP rate limiting).
  3. Interactive bot challenge checkpoints.
* While residential IP connections (such as the developer's laptop) rarely trigger these blocks, datacenter IPs have a high baseline failure rate for unauthenticated Instagram scraping.

### 6.2 Mitigation Options & Trade-Offs

| Strategy | Technical Mechanism | Operational Viability | Security & Policy Considerations |
|---|---|---|---|
| **Strategy 1: Unauthenticated Baseline (Probe First)** | Deploy `yt-dlp` on the target VPS with default settings. Run a test download probe. | Viable on certain smaller VPS providers or European regions where IP pools are less heavily flagged. | Zero credential risk; standard public access. |
| **Strategy 2: Mounted Cookie File (`--cookies`)** | Export an Instagram session cookie from a dedicated throwaway account to `cookies.txt`, mount it read-only in the container. | High reliability against 401 login challenges. | **Security Risk**: If the cookie is compromised, the associated account is compromised. Must use a dedicated throwaway burner account, NEVER a personal account. |
| **Strategy 3: Residential Egress Proxy** | Configure `yt-dlp` proxy options (`--proxy`) to route scraping traffic through a residential proxy or home WireGuard VPN tunnel. | Maximum reliability against datacenter blocks. | Adds network latency, proxy subscription costs ($2–$5/GB), and third-party dependency. |

### 6.3 Pre-Deployment Validation Test
Before migrating production traffic to any cloud host, execute a preflight validation test on the target machine:
```bash
# Run inside the cloud host / container before final deployment:
python -c "from download.downloader import _download_with_ytdlp; print(_download_with_ytdlp('https://www.instagram.com/reel/EXAMPLE/'))"
```
If Instagram returns HTTP 401 or bot challenge, immediately pivot to Strategy 2 (mounted burner cookies).

---

## 7. Resource & Capacity Planning

### 7.1 Component Resource Footprint

| Resource | Idle Baseline | Peak Processing (Single Reel) | Sizing Basis / Measurement |
|---|---|---|---|
| **CPU** | < 2% of 1 vCPU | 100% of 1–2 vCPUs (during Whisper transcription & FFmpeg frame decode) | Audio transcription with CPU PyTorch is compute-bound for ~15–30 seconds per reel. |
| **RAM** | ~200 MB (Python + Telegram bot polling) | **1.2 GB – 1.8 GB** | PyTorch runtime (~500MB) + Whisper `base` model weights (~140MB RAM) + FFmpeg image buffers + Python runtime. |
| **Disk (Storage)** | ~3.5 GB (OS + Docker image) | + 200 MB scratch per active reel | Reels average 10–50MB. Isolated worker workspace cleans up automatically (`KEEP_VIDEOS=False`). |
| **Whisper Cache** | 0 MB (empty) | ~140 MB (`base` model weights) | Stored on persistent volume (`WHISPER_CACHE_DIR`). |
| **Network Egress/Ingress** | Minimal (Polling) | ~25 MB Inbound (Video), ~10 MB Outbound (Frames to Vision AI, OneNote HTML) | Total per reel: ~35 MB. Processing 100 reels/month requires < 5 GB bandwidth. |

### 7.2 Capacity Guidelines
* **Minimum Host Specification**: **1 vCPU, 2 GB RAM, 20 GB SSD**. (Sufficient for unified single-container with sequential reel processing).
* **Recommended Host Specification**: **2 vCPU, 4 GB RAM, 40 GB SSD**. (Leaves comfortable headroom for OS caching, prevents OOM-killer during complex video processing, keeps Telegram bot responsive during transcription).
* **Worker Concurrency Limit**: **1 concurrent worker**. Because ReelForge's job queue in MongoDB Atlas atomically coordinates jobs, running a single worker thread prevents multi-core CPU starvation and eliminates RAM thrashing on budget instances.

---

## 8. Security & Operational Hardening

### 8.1 Secret Management
* **Never commit secrets**: Verified enforced by [`.dockerignore`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/.dockerignore) (ignores all `.env`, `atlas-credentials.env`, `token_cache.bin`).
* **Cloud Injection**: Inject secrets via container environment variables or a host-level `.env` file with restrictive Linux permissions (`chmod 600 .env`).
* **Required Cloud Secrets**:
  * `TELEGRAM_BOT_TOKEN`
  * `MONGODB_URI`
  * `MICROSOFT_CLIENT_ID`
  * `OAUTH_STATE_SECRET` (mandatory 32+ byte hex for OAuth state signing)
  * `OMNIROUTE_API_KEY` or `VISION_API_KEY` (if remote gateway requires auth)

### 8.2 Microsoft OAuth Token Security
* Token caches are stored per-user in MongoDB Atlas under `users.microsoft.token_cache`.
* Tokens are transmitted over encrypted TLS (`mongodb+srv://`) and never logged to stdout or Telegram chats.
* State tokens are HMAC-SHA256 signed using `OAUTH_STATE_SECRET` to prevent CSRF attacks.

### 8.3 Public Ingress & Network Exposure
* **Telegram Bot**: Operates strictly via outbound HTTPS polling. Requires **NO inbound open ports** or firewall ingress rules.
* **OAuth Callback Server**:
  * If enabled, binds to internal port `8080`.
  * **Security Requirement**: NEVER expose raw HTTP port 8080 directly to the public internet. Use a reverse proxy (Caddy / Nginx) terminating TLS on port 443 with a valid certificate, or a Cloudflare Tunnel pointing to `localhost:8080`.
  * If fresh user onboarding is deferred, port 8080 can remain disabled (`REELFORGE_START_OAUTH_SERVER=0`), resulting in **ZERO open inbound ports on the cloud host**.

### 8.4 MongoDB Atlas Network Access
* In the MongoDB Atlas Console, update the **Network Access IP Access List**:
  * Recommended: Add the static public IPv4 of the cloud VPS.
  * Avoid `0.0.0.0/0` unless combined with strict database user passwords and SCRAM-SHA-256 authentication.

### 8.5 Lifecycle & Crash Resilience
* Configure Docker container with `restart: unless-stopped`.
* On restart, uncompleted jobs in `reel_jobs` are automatically recovered via `recover_stale_jobs()` once their 900-second lease expires.
* OneNote publication slots in [`storage/publication.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/storage/publication.py) prevent duplicate OneNote page creation across container restarts.

---

## 9. Staged Migration Strategy

A risk-managed, 6-stage migration path is recommended:

```
[ Stage 1: Preparation & Configuration ]
       |
       v
[ Stage 2: Host & Network Provisioning ]
       |
       v
[ Stage 3: Preflight Cloud Validation (Outbound Connectivity & Probes) ]
       |
       v
[ Stage 4: Pilot Test Deployment (Bot + Worker with Existing User Token) ]
       |
       v
[ Stage 5: OAuth Ingress & Onboarding Validation (Optional/Conditional) ]
       |
       v
[ Stage 6: Production Cutover & Laptop Decommissioning ]
```

### Stage 1: Preparation & Configuration
* **Prerequisites**: Phase 1 & 2 audit reports approved.
* **Deliverables**: Production `docker-compose.yml` draft, environment variable mapping sheet.
* **Acceptance Criteria**: All configurations verified without modifying application source code.

### Stage 2: Host & Network Provisioning
* **Prerequisites**: Target cloud provider selected by Rehan.
* **Deliverables**: Linux VPS provisioned with Docker, Docker Compose, and firewall rules configured.
* **Acceptance Criteria**: VPS accessible via SSH key; Docker daemon operational.

### Stage 3: Preflight Cloud Validation
* **Prerequisites**: Cloud host running.
* **Deliverables**: Connectivity validation scripts executed on cloud host:
  1. MongoDB Atlas ping (`python storage/db.py`).
  2. AI Vision Gateway probe (`python -m processing.vision_analyzer --check-gateway`).
  3. Instagram `yt-dlp` download test on cloud IP.
* **Acceptance Criteria**: All three outbound network dependencies verified functional.

### Stage 4: Pilot Test Deployment
* **Prerequisites**: Preflight tests passed.
* **Deliverables**: ReelForge container started with `REELFORGE_EMBEDDED_WORKER=1`.
* **Acceptance Criteria**:
  * Bot responds to `/start` and `/recent` from Telegram.
  * Submitting a reel processes completely: download -> Whisper -> Vision -> Brain Object saved in Atlas -> OneNote page published to Rehan's account using existing token.

### Stage 5: OAuth Ingress Validation (If New-User Onboarding Enabled)
* **Prerequisites**: Public domain / Cloudflare Tunnel configured pointing to port 8080.
* **Deliverables**: Azure AD App Registration updated with `https://<domain>/auth/callback`.
* **Acceptance Criteria**: User executing `/connect` successfully links account and receives confirmation in Telegram.

### Stage 6: Production Cutover & Laptop Decommissioning
* **Prerequisites**: Pilot test verified end-to-end.
* **Deliverables**: Laptop ReelForge process stopped permanently. Laptop turned off.
* **Acceptance Criteria**: System processes reels sent from Telegram mobile app with laptop powered down.

---

## 10. Decisions Requiring Rehan's Approval

The following architectural decisions are explicitly held open for Rehan's input:

1. **Hosting Provider Selection**:
   * *Option 1A*: **Hetzner Cloud CX23** (~€6/month, 2 vCPU, 4GB RAM) — Most cost-effective, high performance.
   * *Option 1B*: **AWS Lightsail 2GB** ($10/month) — Predictable flat billing within AWS ecosystem.
   * *Option 1C*: **DigitalOcean Basic Droplet** ($12–$18/month) — User-friendly UI, standard developer choice.
2. **Monthly Infrastructure Budget**:
   * Confirm target expenditure ceiling (recommended: $6–$15/month).
3. **AI Inference Provider**:
   * Confirm what remote endpoint will serve `OMNIROUTE_BASE_URL` and `VISION_BASE_URL` (e.g. self-hosted OmniRoute, Google Gemini API, Groq, or OpenAI).
4. **Scope of Microsoft OAuth Ingress (Phase 3 Requirement)**:
   * **Scope A (Recommended for Quick Cutover)**: Support existing connected users only (zero open inbound ports, no domain or reverse proxy needed; existing tokens in Atlas refresh silently).
   * **Scope B (Full Onboarding)**: Support `/connect` for new users in cloud (requires public domain, reverse proxy / Cloudflare Tunnel, and Azure App Registration redirect URI update).
5. **Instagram Access Strategy**:
   * Proceed with unauthenticated `yt-dlp` probe on target host, or prepare a burner Instagram session cookie file (`cookies.txt`) as fallback.

---

## 11. Phase 2 Completion Checklist

- [x] Target cloud architecture designed and mapped.
- [x] Process and service separation trade-offs analyzed (Option A unified vs Option B decoupled).
- [x] Cloud hosting platforms compared with realistic 2026 pricing and resource suitability.
- [x] All 10 laptop configuration gaps analyzed with severity, proposed resolution, and validation method.
- [x] Instagram download reliability and datacenter IP mitigation strategies detailed.
- [x] Resource and capacity planning established (CPU, RAM, disk, persistent cache, concurrency).
- [x] Security, credential isolation, and operational hardening guidelines documented.
* [x] 6-stage migration roadmap formulated.
- [x] Key decisions requiring Rehan's approval identified.
- [x] Application code, dependencies, git history, and tests left 100% unmodified.
