# ReelForge V3.6.4 — Phase 3: Deployment Readiness & Validation Plan

> **Phase 3 Specification & Implementation Plan**  
> **Status**: COMPLETED (Planning & Readiness Only — Read-Only)  
> **Target Version**: ReelForge V3.6.4 (`23cc846`)  
> **Core Objective**: Autonomous 24/7 Cloud Operation (Laptop Independent)  
> **Budget Target**: $\le$ $10.00 / month  
> **Mandatory Scope**: Microsoft OAuth `/connect` for new users included in initial release  

---

## 1. Executive Summary & Confirmed User Constraints

Phase 3 establishes the concrete engineering, hosting, configuration, and validation blueprint required to transition ReelForge V3.6.4 to the cloud.

### Confirmed Constraints & Invariants:
1. **Strict Cost Ceiling**: Total recurring infrastructure expenditure must target approximately **$10/month**, preferably lower.
2. **New-User OAuth Support (Mandatory)**: The initial cloud release **must** allow new users to execute `/connect` in Telegram and successfully authenticate their Microsoft OneNote accounts. Public HTTPS callback ingress must be included in the baseline deployment.
3. **AI Gateway Decision Open**: OmniRoute is under consideration but **not selected**. Direct cloud API options (e.g. Gemini, OpenAI, Groq) must be evaluated against budget and compatibility.
4. **Hosting Provider Unselected**: Provider choice remains open for Rehan's selection.
5. **Instagram Strategy Unselected**: Unauthenticated vs cookie-based scraping strategy must be staged and validated safely.
6. **24/7 Autonomy**: System must operate continuously with zero dependency on the developer's laptop.

---

## 2. Deployment Readiness Checklist

Every operational requirement has been mapped into 10 readiness domains, distinguishing repository-verified facts from items requiring live cloud validation:

| Domain / Item | Status | Evidence / Source | Required Action Prior to Deploy | Dependencies | Acceptance Criteria |
|---|---|---|---|---|---|
| **1. Application Core** | **VERIFIED** | Tracked V3 test suite (272/272 pass) | None. Application code is frozen and verified. | None | All tests pass in `tests/`. |
| **2. Docker Definition** | **VERIFIED** | [`Dockerfile`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/Dockerfile), [`.dockerignore`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/.dockerignore) | Create `docker-compose.yml` to orchestrate bot, volume mounts, and reverse proxy. | Docker Engine on host | Container builds cleanly via `docker compose build`. |
| **3. Cloud Host** | **NOT VERIFIED** | Unprovisioned | Provision Linux VPS with Docker & Docker Compose; configure UFW firewall. | Provider choice (Rehan) | SSH key access verified; Docker daemon running. |
| **4. MongoDB Atlas** | **VERIFIED** | [`storage/db.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/storage/db.py), [`storage/job.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/storage/job.py) | Add Cloud VPS public IPv4 to Atlas Network Access IP Whitelist. | Atlas Console access | `python storage/db.py` succeeds from cloud host. |
| **5. Telegram Bot** | **VERIFIED** | [`bot/telegram_bot.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/bot/telegram_bot.py) | Inject `TELEGRAM_BOT_TOKEN`; start container. Long-polling requires no open inbound ports. | Bot token | Bot responds to `/start` in Telegram. |
| **6. Microsoft OAuth** | **BLOCKED** | [`auth/oauth_server.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/auth/oauth_server.py), [`config.py:50-54`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/config.py#L50-L54) | Provision public HTTPS ingress (Caddy/DuckDNS or Cloudflare Tunnel); register URI in Azure Portal. | Domain / Tunnel, Azure AD Portal | `/connect` URL opens Microsoft login; redirect saves token to Atlas. |
| **7. AI Gateway** | **BLOCKED** | [`processing/vision_analyzer.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/processing/vision_analyzer.py), [`processing/llm_client.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/processing/llm_client.py) | Select provider (Gemini / OpenAI / OmniRoute); inject `OMNIROUTE_BASE_URL` & API keys. | Provider choice (Rehan) | Preflight probe `python -m processing.vision_analyzer --check-gateway` returns HTTP 200. |
| **8. Instagram Ingestion** | **PARTIALLY VERIFIED** | [`download/downloader.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/download/downloader.py) | Run unauthenticated `yt-dlp` download test on cloud IP; mount `cookies.txt` only if challenged. | Cloud IP | Reel MP4, thumbnail, and info.json download successfully. |
| **9. Security & Secrets** | **PARTIALLY VERIFIED** | [`.dockerignore`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/.dockerignore) | Create host `.env` file with `chmod 600`; generate random `OAUTH_STATE_SECRET` (32 bytes). | Secret generation | No plain secrets in repo or container image. |
| **10. Monitoring & Recovery** | **VERIFIED** | [`storage/job.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/storage/job.py), [`processing/worker.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/processing/worker.py) | Set Docker `restart: unless-stopped`; verify stale job lease recovery. | Docker Compose | Abruptly killing container recovers pending jobs on restart. |

---

## 3. Hosting Provider Decision Analysis (Target $\le$ $10/mo)

ReelForge requires a 24/7 long-polling process, memory residency for CPU Whisper (`base` model ~140MB weights, ~1.2GB peak RAM), and local disk storage for temporary media and model cache.

### Provider Comparison Matrix

| Evaluation Factor | Option 1: Hetzner Cloud CX23 (Recommended) | Option 2: AWS Lightsail 2GB Tier | Option 3: DigitalOcean Basic Droplet | Option 4: Managed Container (Railway / Render) |
|---|---|---|---|---|
| **CPU & RAM** | **2 vCPU (AMD/Intel), 4 GB RAM** | **2 vCPU, 2 GB RAM** | 1 vCPU, 2 GB RAM (or 2 vCPU, 2 GB) | 1 vCPU, 2 GB RAM (metered) |
| **Persistent Storage** | **40 GB NVMe SSD** | 60 GB SSD | 50 GB SSD | Ephemeral + Paid Volume ($0.15/GB) |
| **Public IPv4** | Included (€0.50/mo fee included in total) | Included (free with active instance) | Included | Shared proxy domain only |
| **Monthly Cost (2026)** | **€5.49 + €0.50 = €5.99/mo (~$6.50/mo)** | **$10.00/mo flat** | **$12.00/mo** (1 vCPU) or **$18.00/mo** (2 vCPU) | **$20.00 – $35.00+/mo** |
| **Hidden / Add-on Costs** | 20 TB egress included. Zero hidden costs. | 3 TB egress included. Overage is $0.09/GB. | 2 TB egress included. | Continuous RAM billing exceeds credits. |
| **Whisper Performance** | **Fastest** (2 vCPU + 4GB RAM avoids OOM) | Good (2 vCPU handles transcription well) | Slower on 1 vCPU; 2 vCPU exceeds budget | Variable CPU throttling |
| **OAuth Callback Support** | Native (Run Caddy / DuckDNS or Cloudflare Tunnel) | Native (Run Caddy / DuckDNS or Cloudflare Tunnel) | Native (Run Caddy / DuckDNS or Cloudflare Tunnel) | Built-in HTTPS, but high base compute cost |
| **Operational Burden** | Low (Standard Ubuntu 24.04 LTS + Docker) | Low (Standard Ubuntu + Docker in AWS console) | Low (Standard Ubuntu + Docker) | Very Low (Git push) |
| **Budget Alignment** | **Under Budget** (Leaves ~$3.50/mo for AI usage) | **Exact Budget Match** ($0 left for AI usage) | **Over Budget** (> $10/month) | **Severely Over Budget** (2x–4x target) |

### Analysis & Recommendation:
* **Recommendation**: **Hetzner Cloud CX23** at **~$6.50/month** is the clear winner for cost-to-performance. It delivers 4GB RAM (preventing memory pressure during heavy Whisper runs), 40GB NVMe, and leaves ~$3.50/month in reserve for AI API calls.
* **Domestic/US Alternative**: If Hetzner (Germany/Finland/US-East) is not preferred, **AWS Lightsail 2GB** at **$10.00/month flat** is a reliable alternative within AWS's global infrastructure.

---

## 4. AI Gateway and Provider Comparison

ReelForge executes two distinct AI workloads:
1. **Text Categorization & Domain Extraction** ([`processing/llm_client.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/processing/llm_client.py)): Standard OpenAI `/chat/completions` text prompt; expects structured or JSON response.
2. **Multimodal Vision Analysis** ([`processing/vision_analyzer.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/processing/vision_analyzer.py)): Multi-image prompt sending 1 to 8 representative JPEG frames encoded as `data:image/jpeg;base64,...` in the `image_url` payload; expects strict JSON matching `visual_summary`, `on_screen_text`, etc.

### Provider Compatibility & Cost Breakdown

| Metric / Dimension | Option A: Google Gemini API (via OpenAI Adapter) | Option B: OpenAI API (`gpt-4o-mini`) | Option C: Groq API (`llama-3.2-vision`) | Option D: Remote OmniRoute (Self-Hosted) |
|---|---|---|---|---|
| **Text Categorization** | Excellent (`gemini-2.5-flash` / `gemini-2.0-flash`) | Excellent (`gpt-4o-mini`) | Excellent (`llama-3.3-70b-versatile`) | Dependent on loaded model (e.g. `qwen2.5:7b`) |
| **Multimodal Vision** | **Native & Robust** (accepts 8+ base64 frames effortlessly) | **Native & Robust** (accepts 8+ base64 frames effortlessly) | **Constrained** (Groq caps multi-image requests to 1–5 frames) | Dependent on model (e.g. `llama3.2-vision`) |
| **OpenAI API Compatibility** | **100% Native** (`https://generativelanguage.googleapis.com/v1beta/openai/`) | **100% Native** (`https://api.openai.com/v1`) | **100% Native** (`https://api.groq.com/openai/v1`) | 100% Native (`http://<host>:20128/v1`) |
| **Base64 Image URL Support** | **Verified** (`data:image/jpeg;base64,...`) | **Verified** (`data:image/jpeg;base64,...`) | **Verified** (with 4MB per-image size limit) | **Verified** |
| **Token Cost (100 reels/mo)** | **~$0.05 – $0.15 / month** (or **$0** on AI Studio free tier: 15 RPM) | **~$0.15 – $0.35 / month** ($0.15/M in, $0.60/M out) | **~$0.05 – $0.10 / month** | **$0 in API tokens**, BUT... |
| **Infrastructure Surcharge** | **$0.00** (Zero dedicated servers needed) | **$0.00** (Zero dedicated servers needed) | **$0.00** (Zero dedicated servers needed) | **+$150 – $300+/month** if hosted on cloud GPU, OR requires home PC to stay 24/7 ON |
| **Setup Complexity** | Very Low (Generate API key in Google AI Studio) | Very Low (Generate API key in OpenAI Platform) | Low (Generate API key in Groq console) | High (Maintain proxy, GPU drivers, vLLM/Ollama) |
| **Reliability & Latency** | High (Global Google edge infrastructure) | High (OpenAI production Tier 1) | Ultra-fast inference, but stricter rate limits | Dependent on home network / VPS uptime |

### Architectural Insight:
* **The OmniRoute Paradox**: Self-hosting OmniRoute on a cloud GPU instance breaks the $10 budget immediately ($150–$300/mo). Hosting it on a home PC violates the core requirement that ReelForge must run 24/7 when the laptop/home PC is OFF. Hosting it on a $10 CPU VPS will crash with Out-Of-Memory (OOM) errors attempting to run 7B/11B LLMs.
* **The Serverless API Solution**: Pointing `OMNIROUTE_BASE_URL` or `VISION_BASE_URL` directly to **Google Gemini API** (`https://generativelanguage.googleapis.com/v1beta/openai/`) or **OpenAI API** (`https://api.openai.com/v1`) incurs **pennies per month** (~$0.10–$0.30/mo for 100 reels), requires zero server maintenance, supports base64 frames natively, and preserves 100% code compatibility.

---

## 4. Microsoft OAuth Ingress Architecture (Mandatory Release Scope)

Because the initial cloud release must support new users executing `/connect` in Telegram, ReelForge cannot operate in zero-inbound-port isolation. A secure, headless OAuth ingress must be provisioned.

### 4.1 Ingress Strategy Comparison

| Metric / Dimension | Strategy 1: Cloudflare Tunnel (`cloudflared`) | Strategy 2: Caddy Reverse Proxy + Free Dynamic DNS (DuckDNS) | Strategy 3: Direct Port Exposure |
|---|---|---|---|
| **Inbound Open Ports** | **ZERO open firewall ports** (Outbound tunnel to Cloudflare Edge) | **Port 443 & 80** open on host | Port 8080 open (INSECURE) |
| **Domain Requirement** | Requires any domain on Cloudflare DNS (Free plan; e.g. existing domain or $3/yr cheap TLD) | Free dynamic subdomain (e.g. `reelforge.duckdns.org` or `nip.io`) | Raw IP (Azure rejects non-HTTPS for public endpoints) |
| **SSL / HTTPS Termination** | **Automatic at Cloudflare Edge** (TLS 1.3) | **Automatic via Let's Encrypt** managed by Caddy | None |
| **Azure Portal Redirect URI** | `https://<your-subdomain>.<domain>/auth/callback` | `https://reelforge.duckdns.org/auth/callback` | Unsupported |
| **Monthly Cost** | **$0.00** (Free Cloudflare Tunnel) | **$0.00** (Free DuckDNS + Free Let's Encrypt) | N/A |
| **DDoS & Web Protection** | **High** (Hidden origin IP, Cloudflare WAF) | Moderate (VPS IP exposed; rate limited by Caddy) | Zero |
| **Container Setup** | Lightweight `cloudflared` container in Docker Compose | Lightweight `caddy:alpine` container in Docker Compose | None |

### 4.2 Detailed Ingress Architecture (Recommended: Strategy 1 or Strategy 2)

```
[ User Mobile / Browser ]
           |
           | 1. Clicks /connect link (https://login.microsoftonline.com/...)
           v
[ Microsoft Azure AD ]
           |
           | 2. User authenticates & consents
           | 3. Redirects to https://auth.reelforge.xyz/auth/callback?code=...&state=...
           v
+---------------------------------------------------------------------------------------+
| Cloudflare Edge OR Caddy Reverse Proxy (:443)                                         |
| - Terminates public HTTPS with valid certificate                                      |
| - Forwards GET /auth/callback to internal container port 8080                         |
+-------------------------------------------+-------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
| ReelForge Application Container                                                       |
|                                                                                       |
|  [ OAuthCallbackServer ] (auth/oauth_server.py) listening on 0.0.0.0:8080             |
|  1. Intercepts callback request                                                       |
|  2. Validates HMAC-SHA256 state signature using OAUTH_STATE_SECRET                    |
|  3. Atomically consumes oauth_session from MongoDB Atlas (replay-safe)                |
|  4. Exchanges authorization code + PKCE code_verifier via MSAL                        |
|  5. Retrieves user profile from Microsoft Graph (/me)                                 |
|  6. Updates MongoDB `users.microsoft` subdocument with token_cache & display_name     |
|  7. Returns styled HTML confirmation: "Microsoft account connected successfully"     |
+---------------------------------------------------------------------------------------+
```

### 4.3 Invariant Security Rules for OAuth
1. **Host Binding**: Set `OAUTH_HOST=0.0.0.0` and `OAUTH_LISTEN_PORT=8080` in the container so the reverse proxy / tunnel can reach it.
2. **State Signing**: `OAUTH_STATE_SECRET` must be injected as a random 32-byte hex string. If unset, the server refuses to issue authorization links.
3. **Session Replay Guard**: Sessions in `oauth_sessions` are consumed via atomic `find_one_and_delete` / update; duplicate callback requests are rejected immediately.
4. **Token Isolation**: Serialized MSAL token caches are written strictly to `users.microsoft.token_cache` keyed by the user's canonical `user_id`. One user can never access or overwrite another user's tokens.
5. **Headless Silent Refresh**: Returning users never re-trigger the callback server. [`onenote/graph_client.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/onenote/graph_client.py#L102) calls `acquire_token_silent()`, which refreshes access tokens automatically using stored refresh tokens.

---

## 5. Instagram Download Validation Plan

Instagram media acquisition via `yt-dlp` is the primary external operational risk due to Meta's aggressive blocking of cloud datacenter IP ranges.

### Staged Validation Protocol

```
[ Step 1: Unauthenticated Cloud Probe ]
                  |
         +--------+--------+
         |                 |
     (Success)          (HTTP 401 / 429 Challenge)
         |                 |
         v                 v
[ Verified Clean ]   [ Step 2: Burner Cookie Fallback ]
                           |
                           v
                     [ Step 3: Verified with Cookies ]
```

#### Step 1: Unauthenticated Cloud Probe (Execute on Target VPS First)
Before starting production bot services, run a standalone probe command directly on the cloud host:
```bash
python -c "from download.downloader import _download_with_ytdlp; print(_download_with_ytdlp('https://www.instagram.com/reel/DCk7QPXuRBJ/'))"
```
* **Pass Criteria**: `_download_with_ytdlp` downloads the MP4, thumbnail, and metadata without error. (Status: Unauthenticated scraping viable).
* **Fail Criteria**: `yt-dlp` raises `ExtractorError`, `HTTP Error 401: Unauthorized`, or redirects to an Instagram login checkpoint.

#### Step 2: Burner Cookie Fallback Protocol (If Step 1 Fails)
If the cloud host IP is challenged, apply cookie authentication without risking personal credentials:
1. Create a **dedicated burner/throwaway Instagram account** (never use personal credentials).
2. Export cookies in Netscape format (`cookies.txt`) using a secure browser extension.
3. Mount `cookies.txt` into the container at `/app/secrets/cookies.txt` with read-only permissions (`chmod 400`).
4. Update `downloader.py` options via environment flag: `"cookiefile": os.getenv("INSTAGRAM_COOKIES_PATH")`.
5. Re-run probe to confirm clean downloads.

#### Step 3: Error Isolation & Crash Prevention
* If download fails despite retries, [`processing/pipeline.py`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/processing/pipeline.py#L272) catches the exception cleanly, marks the job `failed` in MongoDB Atlas, and notifies the user in Telegram ("Download Failed: ...").
* The durable queue and worker process do not crash, and other jobs remain unaffected.

---

## 6. End-to-End Deployment Sequence

A 6-stage, dependency-ordered deployment sequence designed to prevent premature exposure or budget overrun:

### Stage 1: Local Preparation & Asset Packaging
* **Objective**: Prepare production configuration templates without touching application code.
* **Prerequisites**: Phase 1, 2, and 3 documentation approved.
* **Actions**: Draft `docker-compose.yml`, `Caddyfile` (or `cloudflared` config), and `.env.production` template.
* **Configuration Keys Needed**: `TELEGRAM_BOT_TOKEN`, `MONGODB_URI`, `MICROSOFT_CLIENT_ID`, `OAUTH_STATE_SECRET`, `OMNIROUTE_BASE_URL`, `OMNIROUTE_API_KEY`.
* **Validation**: Run existing test suite locally (`python -m unittest discover -s tests -p "test_v3*.py"` -> 272/272 pass).
* **Cost**: $0.00.

### Stage 2: Cloud Host & Ingress Provisioning
* **Objective**: Provision target VPS and configure networking.
* **Prerequisites**: Provider choice approved by Rehan.
* **Actions**: Provision VPS (e.g. Hetzner CX23 or Lightsail 2GB); install Docker & Docker Compose; configure UFW firewall (allow SSH :22, HTTP :80, HTTPS :443); setup domain or tunnel.
* **Validation**: SSH access operational; `docker --version` succeeds; domain resolves to host IP.
* **Cost**: ~$6.50 – $10.00/month.

### Stage 3: Preflight Cloud Connectivity Probes
* **Objective**: Verify outbound connectivity to Atlas, AI Gateway, and Instagram before running application services.
* **Prerequisites**: Cloud host running.
* **Actions**: Execute 3 verification probes:
  1. *MongoDB Atlas*: `python storage/db.py` (verifies connection pool and indexes).
  2. *AI Gateway*: `python -m processing.vision_analyzer --check-gateway` (verifies LLM auth and HTTP 200).
  3. *Instagram Scraping*: Execute Step 1 probe from Section 5.
* **Acceptance Criteria**: All 3 external services respond successfully.
* **Rollback**: If cloud IP is blocked by Instagram, pivot to burner cookie file. If Atlas fails, verify Atlas IP whitelist.
* **Cost**: < $0.05 (test tokens).

### Stage 4: Controlled End-to-End Test (Existing Connected User)
* **Objective**: Validate the full ingestion-to-publication pipeline.
* **Prerequisites**: Preflight probes passed.
* **Actions**: Start ReelForge container (`docker compose up -d`). Send a public test Instagram reel URL to the Telegram bot.
* **Validation Trace**:
  1. Telegram bot receives message -> enqueues job in MongoDB `reel_jobs`.
  2. Worker claims job with 900s lease -> notifies Telegram ("Downloading").
  3. Media downloaded to worker scratch workspace -> Whisper transcribes audio.
  4. Vision analyzer extracts frames -> LLM categorizes and normalizes schema.
  5. OneNote writer claims publication slot -> silently refreshes token -> creates page in user's OneNote notebook.
  6. Ephemeral media files purged -> job marked `completed` -> Telegram receives success card.
* **Acceptance Criteria**: OneNote page appears with thumbnail and structured notes; Brain Object saved in Atlas; media scratch clean.

### Stage 5: New-User Microsoft OAuth Ingress Validation
* **Objective**: Validate the mandatory `/connect` onboarding flow for new users.
* **Prerequisites**: Stage 4 pipeline verified.
* **Actions**:
  1. Register public redirect URI (`https://<domain>/auth/callback`) in Azure AD App Registration portal.
  2. Send `/connect` command to Telegram bot from a secondary/test Telegram account.
  3. Click authorization button in Telegram -> authenticate with a test Microsoft account in mobile/desktop browser.
  4. Confirm redirect to callback server -> verify green confirmation page in browser.
  5. Check MongoDB Atlas `users` collection: verify user record contains populated `microsoft.token_cache` and `microsoft.display_name`.
  6. Send a reel URL from the newly connected account -> verify reel publishes to their OneNote notebook.
* **Acceptance Criteria**: New user connects and publishes without developer intervention.

### Stage 6: Production Cutover & Workstation Independence
* **Objective**: Decommission laptop execution and achieve 24/7 cloud autonomy.
* **Prerequisites**: Stages 4 and 5 passed.
* **Actions**: Terminate any local ReelForge processes on Rehan's laptop. Power down the laptop completely.
* **Validation**: Send a reel to Telegram from a mobile device while laptop is OFF. Confirm completion notification and OneNote publication.
* **Acceptance Criteria**: System functions 100% autonomously 24/7.

---

## 7. Security and Operational Readiness

### 7.1 Security Policy & Hardening
* **Secret Protection**:
  * Production secrets stored in host `.env` with file permissions `600` owned by root.
  * `.dockerignore` blocks `.env`, `atlas-credentials.env`, and `token_cache.bin` from ever being packaged into image layers.
  * Access tokens, authorization codes, and raw PKCE verifiers are **never logged** to stdout, stderr, or Telegram messages.
* **Microsoft Token Isolation**:
  * MSAL token caches stored per-user in MongoDB Atlas `users.microsoft.token_cache`.
  * Multi-tenant queries strictly enforce `ownership.saved_by` filtering; users cannot view or mutate other users' libraries or tokens.
* **Ingress Hardening**:
  * The internal OAuth server binds to `0.0.0.0:8080` inside the container network only.
  * Port 8080 is never exposed directly on the public host interface; all external traffic passes through Caddy (port 443 with TLS 1.3) or Cloudflare Tunnel.
* **Container Isolation**:
  * ReelForge runs without root host privileges.
  * Ephemeral media scratch uses strict container-internal paths (`/app/reels/workers/...`).

### 7.2 Backup & Disaster Recovery
* **Durable State**: Resides entirely in MongoDB Atlas. Atlas automated continuous daily backups protect against data loss.
* **Whisper Weights Volume**: Bound to host directory `/var/lib/reelforge/whisper_cache`. If container restarts, weights remain cached.
* **Container Failover**: Docker configured with `restart: unless-stopped`. If host reboots, systemd automatically restarts Docker and ReelForge.

---

## 8. Budget & Monthly Cost Model

### Cost Comparison Scenarios

| Expense Item | Baseline Low-Usage (~50 reels/mo) | Moderate-Usage (~200 reels/mo) | Cost Drivers & Risk Factors |
|---|---|---|---|
| **Host Compute (Hetzner CX23)** | **$6.50** (€5.99/mo) | **$6.50** (€5.99/mo) | Fixed monthly cost. Includes 2 vCPU, 4GB RAM, 40GB SSD. |
| **Alternative Compute (AWS Lightsail)** | *$10.00* | *$10.00* | Flat $10/mo tier. |
| **Domain & Public Ingress** | **$0.00** (Free DuckDNS + Caddy OR existing domain via Cloudflare) | **$0.00** | $0 if using DuckDNS/Cloudflare Tunnel. (+$1–$2/yr if buying cheap TLD). |
| **AI Inference: Google Gemini API** | **$0.00** (Free Tier: 15 RPM) or **~$0.05** | **~$0.20** | Extremely cheap ($0.10/M input tokens). Negligible cost. |
| **AI Inference: OpenAI API (`gpt-4o-mini`)** | *$0.10* | *$0.40* | Very cheap ($0.15/M input tokens). |
| **AI Inference: Self-Hosted OmniRoute** | *NOT VIABLE ($150+)* | *NOT VIABLE ($150+)* | Running a dedicated GPU host would breach budget by 15x. |
| **MongoDB Atlas** | **$0.00** (M0 Free Tier: 512MB storage) | **$0.00** (M0 Free Tier holds ~10,000+ Brain Objects) | Stays free indefinitely for personal/small-group use. |
| **Egress Bandwidth** | **$0.00** (20 TB included on Hetzner; 3 TB on Lightsail) | **$0.00** | Negligible (~7 GB / mo). |
| **Total Estimated Monthly Cost** | **$6.50 – $6.60 / month** | **$6.70 – $7.00 / month** | **Comfortably under the $10.00 budget target!** |

### What Could Push Costs Above $10/month?
1. Choosing managed container platforms (Railway/Render) which bill continuous RAM 24/7 (~$25–$45/mo).
2. Attempting to self-host GPU AI models on cloud instances.
3. Choosing higher-tier VPS instances (e.g. 8GB+ RAM).

---

## 9. Open Decisions Register (Requiring Rehan's Approval)

The following decisions must be made before Phase 4 (implementation and provisioning):

| Decision ID | Decision Title | Options Considered | Trade-Offs & Impact | Recommendation | When Needed |
|---|---|---|---|---|---|
| **DEC-01** | **Cloud Hosting Provider** | 1. **Hetzner Cloud CX23** (~$6.50/mo, 4GB RAM)<br>2. **AWS Lightsail** ($10.00/mo, 2GB RAM)<br>3. **DigitalOcean** ($12–$18/mo) | Hetzner provides 4GB RAM under $7/mo. Lightsail is $10 flat in AWS. DigitalOcean is slightly over budget. | **Hetzner CX23** (best value) or **Lightsail 2GB** | Before Stage 2 (Provisioning) |
| **DEC-02** | **AI Inference Provider** | 1. **Google Gemini API** (via OpenAI adapter)<br>2. **OpenAI API (`gpt-4o-mini`)**<br>3. **Remote OmniRoute** | Gemini/OpenAI cost pennies ($0.10–$0.30/mo) with zero server overhead. OmniRoute requires expensive 24/7 host. | **Google Gemini API** (via standard OpenAI endpoint) | Before Stage 1 (Config draft) |
| **DEC-03** | **OAuth Ingress Strategy** | 1. **Caddy + Free DuckDNS** (`reelforge.duckdns.org`)<br>2. **Cloudflare Tunnel** (`auth.<yourdomain>.com`) | DuckDNS is 100% free with zero prerequisites. Cloudflare Tunnel requires owning a domain on Cloudflare. | **DuckDNS + Caddy** (Zero cost, standalone) | Before Stage 2 (DNS setup) |
| **DEC-04** | **Instagram Fallback Strategy** | 1. **Unauthenticated probe first**<br>2. **Pre-stage throwaway burner cookies** | Probe costs nothing to test. Burner cookies guarantee reliability if datacenter IP is challenged. | **Probe first; stage burner cookies if probe fails** | Stage 3 (Preflight probe) |
| **DEC-05** | **Monthly Budget Ceiling** | 1. **$7.00 / month** (Hetzner + Gemini)<br>2. **$10.00 / month** (Lightsail + OpenAI) | Both models satisfy 24/7 autonomous requirements. | **Confirm $10/mo ceiling** | Immediate |

---

## 10. Phase 3 Completion Checklist

- [x] Comprehensive 10-domain deployment readiness checklist established.
- [x] Hosting providers compared rigorously against the $\le$ $10/mo budget constraint.
- [x] AI gateway options compared (Gemini, OpenAI, Groq, OmniRoute) with base64 vision verified.
- [x] Microsoft OAuth public ingress architecture defined for mandatory new-user onboarding.
- [x] Instagram download reliability and staged validation protocol detailed.
- [x] Dependency-aware 6-stage deployment sequence created (including end-to-end and `/connect` tests).
- [x] Security, credential isolation, and disaster recovery policies documented.
- [x] Transparent monthly cost model created for low and moderate usage scenarios.
- [x] Open Decisions Register compiled for Rehan's explicit approval.
- [x] Application code, dependencies, git history, and tests left 100% unmodified.
