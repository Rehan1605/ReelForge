# ReelForge V3.6.4 — Cloud Migration Master Audit

> **Master Audit Specification & Roadmap**  
> **Target Version**: ReelForge V3.6.4  
> **Status**: All 3 Phases Complete (Deployment Readiness & Validation Plan Established)  
> **Authoritative Persistence**: MongoDB Atlas  
> **Goal**: Independent 24/7 cloud runtime on headless infrastructure (laptop OFF)  
> **Target Budget**: $\le$ $10.00 / month  

---

## 1. Executive Summary & Objective

ReelForge is a Telegram-driven Instagram Reel knowledge extraction and personal intelligence engine. It captures Instagram reels, downloads video/audio media, transcribes spoken content using OpenAI Whisper, extracts structured visual observations via multimodal vision LLMs, classifies domain topics, stores rich Brain Objects in MongoDB Atlas, and publishes structured notes to Microsoft OneNote notebooks via Microsoft Graph.

The primary objective of the Cloud Migration project is to transition ReelForge from a developer-laptop-tethered system to an autonomous, 24/7 cloud deployment. In this target state, ReelForge will:
1. Receive Instagram reel links and Telegram commands via Telegram Bot long-polling continuously.
2. Ingest, download, and process media on headless cloud compute without a graphical display.
3. Transcribe audio and query multimodal AI endpoints without relying on local workstation services (`http://localhost:20128/v1` or local Ollama).
4. Persist all state, user profiles, and ownership structures directly in MongoDB Atlas.
5. Publish formatted notes into users' Microsoft OneNote accounts using existing per-user OAuth tokens stored in MongoDB Atlas, and handle fresh user `/connect` authorization flows via a cloud-accessible ingress.
6. Recover reliably from container restarts, preemption, or network interruptions without data loss or duplicate publications.

---

## 2. Three-Phase Cloud Migration Roadmap

```
+-----------------------------------------------------------------------------------+
|                           Cloud Migration Audit Roadmap                           |
+-----------------------------------------------------------------------------------+
|  [PHASE 1: CURRENT-STATE AUDIT] (COMPLETED)                                       |
|  - Comprehensive code & dependency inspection                                    |
|  - Inventory of entry points, persistence, and external services                 |
|  - Identification of laptop-bound paths, localhost endpoints, and auth blocks   |
|  - Baseline test verification across all active regression suites (272/272 pass) |
+---------------------------------------------------------+-------------------------+
                                                          |
                                                          v
+-----------------------------------------------------------------------------------+
|  [PHASE 2: CLOUD ARCHITECTURE & GAP ANALYSIS] (COMPLETED)                         |
|  - Target cloud architecture & communication topology defined                     |
|  - Unified Single-Container vs Decoupled Multi-Service trade-off analysis        |
|  - 2026 hosting provider comparison (Hetzner, Lightsail, DigitalOcean, Railway)  |
|  - Systematic gap catalog with severities, resolutions, and validation methods    |
|  - Instagram datacenter IP risk & mitigation analysis (yt-dlp)                    |
|  - Sizing guidelines (2 vCPU, 2-4GB RAM, persistent Whisper volume)               |
+---------------------------------------------------------+-------------------------+
                                                          |
                                                          v
+-----------------------------------------------------------------------------------+
|  [PHASE 3: DEPLOYMENT READINESS & VALIDATION PLAN] (COMPLETED)                    |
|  - Comprehensive 10-domain deployment readiness checklist                         |
|  - Detailed provider analysis targeting <= $10/mo (Hetzner CX23 vs Lightsail 2GB)|
|  - AI Gateway provider comparison (Gemini API vs OpenAI vs Groq vs OmniRoute)    |
|  - Mandatory Microsoft OAuth public ingress plan (DuckDNS+Caddy / Cloudflare)    |
|  - Staged Instagram download validation protocol (unauthenticated -> cookies)    |
|  - 6-stage end-to-end deployment sequence & transparent monthly cost model        |
|  - Open Decisions Register compiled for Rehan's explicit approval                 |
+-----------------------------------------------------------------------------------+
```

---

## 3. High-Level Findings Across All Phases

### Phase 1: Current-State Audit (COMPLETED)
* Detailed component inventory, external service dependencies, and persistence models mapped.
* Verified that MongoDB Atlas is authoritative for all durable state.
* Full test suite regression verified: 272/272 unit tests passing.
* Detailed findings documented in [`PHASE1_CURRENT_STATE_AUDIT.md`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/docs/cloud-migration/PHASE1_CURRENT_STATE_AUDIT.md).

### Phase 2: Cloud Architecture & Gap Analysis (COMPLETED)
* Target architecture: Outbound-polling bot with embedded worker and in-process OAuth callback server.
* Unified Single-Container (`REELFORGE_EMBEDDED_WORKER=1`) recommended for initial release (~1.5GB RAM footprint).
* Hosting evaluation confirmed a budget Linux VPS is significantly more cost-effective than usage-metered container platforms.
* Detailed findings documented in [`PHASE2_CLOUD_ARCHITECTURE_GAP_ANALYSIS.md`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/docs/cloud-migration/PHASE2_CLOUD_ARCHITECTURE_GAP_ANALYSIS.md).

### Phase 3: Deployment Readiness & Validation Plan (COMPLETED)
* **Budget Viability**: Verified achievable at **~$6.50 – $10.00 / month total** using Hetzner Cloud CX23 (~$6.50/mo) or AWS Lightsail 2GB ($10.00/mo) paired with serverless cloud AI endpoints (Google Gemini API / OpenAI `gpt-4o-mini`), leaving MongoDB Atlas on the free tier ($0) and public ingress via free DuckDNS + Caddy or Cloudflare Tunnel ($0).
* **Mandatory Microsoft OAuth Ingress**: Public HTTPS reverse proxy architecture designed using DuckDNS + Caddy (or Cloudflare Tunnel) to forward external redirects to internal port 8080. Zero open inbound ports required if Cloudflare Tunnel is selected; single port 443 required if Caddy is selected.
* **AI Provider Analysis**: Confirmed that self-hosting OmniRoute on a cloud GPU instance breaks the $10 budget ($150–$300/mo), whereas direct serverless APIs (Gemini 2.5 Flash via OpenAI adapter, OpenAI `gpt-4o-mini`) cost **<$0.20/month** for 100 reels, support base64 frames natively, and require zero infrastructure maintenance.
* **Detailed Report**: Documented in [`PHASE3_DEPLOYMENT_READINESS_PLAN.md`](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/docs/cloud-migration/PHASE3_DEPLOYMENT_READINESS_PLAN.md).

---

## 4. Phase Reports Directory

* [Phase 1 Current-State Audit Report](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/docs/cloud-migration/PHASE1_CURRENT_STATE_AUDIT.md) — Technical inventory, dependency matrix, verified evidence items, and blocker analysis.
* [Phase 2 Cloud Architecture & Gap Analysis Report](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/docs/cloud-migration/PHASE2_CLOUD_ARCHITECTURE_GAP_ANALYSIS.md) — Target architecture, process separation, 2026 hosting comparison, gap resolution matrix, Instagram reliability, capacity planning, and staged migration roadmap.
* [Phase 3 Deployment Readiness & Validation Plan](file:///c:/Users/Rehan%27s%20Lenovo/OneDrive/Desktop/InstaBrain/docs/cloud-migration/PHASE3_DEPLOYMENT_READINESS_PLAN.md) — 10-domain readiness checklist, budget model ($\le$ $10/mo), AI gateway comparison, mandatory OAuth ingress design, Instagram validation plan, and 6-stage deployment sequence.

---

## 5. Critical & High-Priority Gaps Summary

| Gap ID | Description | Severity | Resolution Summary |
|---|---|---|---|
| **G-01** | `OMNIROUTE_BASE_URL` localhost default (`http://localhost:20128/v1`) | **CRITICAL** | Set to external Gemini/OpenAI endpoint in production environment. |
| **G-02** | `OAUTH_REDIRECT_URI` localhost default (`http://localhost:8080/auth/callback`) | **HIGH** | Set to `https://<domain>/auth/callback` in environment and Azure AD Portal. |
| **G-03** | `OAUTH_HOST` loopback binding (`127.0.0.1`) | **HIGH** | Set `OAUTH_HOST=0.0.0.0` in container environment for reverse proxy ingress. |
| **G-06** | `BRAINS_DIR` MongoDB bypass heuristic | **HIGH** *(Operational Hazard)* | Keep `BRAINS_DIR` at default or path ending in `/brains` to prevent silent Atlas bypass. |
| **G-09** | Secret injection via process environment | **HIGH** | Supply all required production secrets through container environment variables. |

---

## 6. Open Decisions Register (Requiring Rehan's Approval)

Before proceeding to physical provisioning and deployment, Rehan's direction is requested on the following strategic decisions:

| Decision ID | Decision Title | Options Considered | Recommendation | Status |
|---|---|---|---|---|
| **DEC-01** | **Cloud Hosting Provider** | 1. **Hetzner Cloud CX23** (~$6.50/mo, 2 vCPU, 4GB RAM)<br>2. **AWS Lightsail 2GB** ($10.00/mo flat)<br>3. **DigitalOcean Basic** ($12–$18/mo) | **Hetzner Cloud CX23** (best value) or **AWS Lightsail 2GB** | Awaiting Approval |
| **DEC-02** | **AI Inference Provider** | 1. **Google Gemini API** (via OpenAI adapter, ~$0.05–$0.20/mo)<br>2. **OpenAI API (`gpt-4o-mini`)** (~$0.15–$0.35/mo)<br>3. **Remote OmniRoute** (breaks budget if cloud GPU) | **Google Gemini API** (`gemini-2.5-flash` via OpenAI endpoint) | Awaiting Approval |
| **DEC-03** | **OAuth Public Ingress** | 1. **DuckDNS + Caddy** (`reelforge.duckdns.org`, $0)<br>2. **Cloudflare Tunnel** (`auth.<yourdomain>.com`, $0) | **DuckDNS + Caddy** (Zero cost, standalone) | Awaiting Approval |
| **DEC-04** | **Instagram Fallback** | 1. **Unauthenticated probe first**<br>2. **Pre-stage throwaway burner cookies** | **Probe cloud IP first; mount burner cookies if challenged** | Awaiting Approval |
| **DEC-05** | **Monthly Budget Ceiling** | 1. **$7.00 / month** (Hetzner + Gemini)<br>2. **$10.00 / month** (Lightsail + OpenAI) | **Confirm target ceiling ($\le$ $10.00/mo)** | Awaiting Approval |

---

## 7. Scope & Compliance Verification

* **Read-Only Compliance**: Verified. No application code, Dockerfiles, dependencies, tests, or Git history have been modified.
* **Working Tree**: Clean working tree with untracked files strictly confined to `docs/cloud-migration/`.
* **Tests**: 272/272 active V3 unit and regression tests passing.
