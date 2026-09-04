# ReelForge

> Turn Instagram Reels into structured, category-specific personal knowledge and automatically publish clean, formatted notes to Microsoft OneNote.

ReelForge is an automated knowledge extraction system that turns short-form Instagram video content into structured, searchable personal knowledge. When you send an Instagram Reel URL to the ReelForge Telegram bot (or CLI), it downloads the media, transcribes the audio via local Whisper, analyzes visual video frames (on-screen text, code, ingredients, demonstrations), classifies the content into 10 specialized categories, extracts a structured schema, persists an atomic Brain Object with deterministic provenance, and publishes formatted pages into dedicated Microsoft OneNote sections.

---

## What It Does

Short-form videos contain high-value knowledge—coding roadmaps, recipes, workout routines, cinematography techniques, financial breakdowns, and productivity frameworks. However, this knowledge is typically locked in transient video streams.

ReelForge structures this knowledge:

1. **Ingests** an Instagram Reel via Telegram or CLI.
2. **Transcribes** audio offline using local OpenAI Whisper.
3. **Analyzes visual frames** (on-screen text, visible tools, UI code, demonstration steps) via OmniRoute (`VISION_MODEL`).
4. **Classifies** the content domain using OmniRoute (`TEXT_MODEL`).
5. **Extracts domain-specific structured knowledge** combining caption, transcript, and visual observations.
6. **Constructs an Atomic Brain Object** (persisted locally as JSON with provenance metadata).
7. **Publishes** a styled note directly into the appropriate OneNote category section via Microsoft Graph API.
8. **Discovers Cross-Reel Connections** via deterministic semantic linking (`/related`, `/topics`, `/creator`).
9. **Evaluates Quality** via an automated Grounded LLM Judge auditing factuality, completeness, and grounding.

---

## 10 Specialized Knowledge Categories

ReelForge routes each Reel to a dedicated extractor tailored for its specific domain:

| # | Category | Key Extracted Structured Fields |
|---|---|---|
| 1 | **Programming** | Main topic, difficulty, key concepts, resources, tools, code snippets, best practices, mistakes to avoid, action items |
| 2 | **AI** | Models, tools, websites, prompts, concepts, use cases, key points, tips |
| 3 | **Food** | Dishes, ingredient lists with measurements, step-by-step instructions, cookware, cuisine, tips |
| 4 | **Photography** | Camera settings (ISO/shutter/aperture), gear, lighting, techniques, editing tools, locations, tips |
| 5 | **Gym** | Exercises, targeted muscles, equipment, sets & reps, form cues, workout type, nutrition, recovery, tips |
| 6 | **Movies & Edits** | Film/show titles, editing software, transitions, effects, templates, steps, tips |
| 7 | **Travel** | Destinations, attractions, hotels, restaurants, budget tips, best time to visit, transport, tips |
| 8 | **Finance** | Financial concepts, instruments/stocks, metrics/percentages, actionable rules, risks, key takeaways |
| 9 | **Productivity** | Methods/frameworks, tools, habits, action items, key concepts, best practices |
| 10 | **Other** | Key points, recommendations, steps, websites, general takeaways |

---

## Pipeline Architecture

```text
Instagram Reel URL
        │
        ├──────────────► Local Whisper (Offline Audio Transcription)
        │                    │
        │                    ▼
        │                Transcript
        │
        └──────────────► Vision Analyzer (Frame Sampling & Analysis)
                             │
                             ▼
                         OmniRoute Gateway (VISION_MODEL)
                             │
                             ▼
                      Vision Analysis
                             │
Caption + Transcript + Vision Analysis
                             │
                             ▼
                       Categorizer (OmniRoute TEXT_MODEL)
                             │
                             ▼
                   Category-Specific Extractor
                             │
                             ▼
                   Structured Knowledge JSON
                             │
                             ▼
              Atomic Brain Object (JSON + Provenance)
                             │
        ┌────────────────────┴────────────────────┐
        ▼                                         ▼
Microsoft OneNote Publisher            Cross-Reel Knowledge Graph
(Microsoft Graph API)                  (/related, /topics, /creator)
```

---

## Key Features

* **Provider-Independent AI Architecture**: Routes production text, vision, and evaluation LLM calls through an OpenAI-compatible OmniRoute gateway (`http://localhost:20128/v1`), supporting local inference nodes or cloud providers with zero code changes.
* **Multimodal Evidence Extraction**: Fuses speech transcripts, video captions, and visual frame observations (on-screen text, ingredients, settings, code) into domain knowledge extraction.
* **100% Local Speech Transcription**: Audio transcription runs completely offline on device via OpenAI Whisper.
* **Deterministic Knowledge Lifecycle**: Full ingestion control with `/force <url>` (bypass cache), `/reprocess <reel_id>` (re-extract using stored source URL), `/archive <reel_id>`, `/restore <reel_id>`, and `/recat <reel_id> <category>`.
* **Cross-Reel Linking & Discovery**: Instant, zero-LLM deterministic discovery linking related Reels across shared tools, tags, creator, and category (`/related`, `/topics`, `/topic`, `/creator`).
* **Grounded LLM Evaluation System**: Automated 7-dimension quality evaluation framework auditing Brain Objects against source evidence for grounding, factuality, completeness, and multimodal utilization.
* **Graph API Error Handling & Title Sanitization**: Automatically normalizes colons, slashes, and reserved Microsoft Graph characters to prevent Error 20153 rejections.
* **Complete Privacy**: Downloaded videos are cleaned up automatically; metadata is saved locally to JSON before publishing.

---

## Telegram Bot Commands

| Command | Usage | Description |
|---|---|---|
| `/start` | `/start` | Welcome message & bot overview |
| `/help` | `/help` | Complete command reference guide |
| `/get <reel_id>` | `/get DcK7QPXuRBJ` | View structured knowledge card with related Reels |
| `/list [category]` | `/list Food` | List active Brain Objects (optionally filtered by category) |
| `/stats` | `/stats` | View library size, category breakdown, and modality coverage |
| `/related <reel_id>` | `/related DZAqhTjN6-R` | Discover conceptually related Reels with match scoring |
| `/topics` | `/topics` | List top extracted topics across the active library |
| `/topic <name>` | `/topic Python` | Find all Reels covering a specific topic |
| `/creator <username>` | `/creator Christian` | Find all Reels from a specific creator |
| `/reprocess <reel_id>` | `/reprocess DcK7QPXuRBJ` | Re-extract an existing Reel using its stored source URL |
| `/force <url>` | `/force https://...` | Ingest an Instagram Reel bypassing the cache |
| `/archive <reel_id>` | `/archive DcK7QPXuRBJ` | Archive a Reel (hides from search/linking, preserves file) |
| `/restore <reel_id>` | `/restore DcK7QPXuRBJ` | Restore an archived Reel to the active library |
| `/recat <reel_id> <cat>` | `/recat DcK7QPXuRBJ Food` | Update category metadata without re-downloading |

---

## Grounded Evaluation System

ReelForge includes a built-in Grounded Evaluation framework to audit extraction quality:

```bash
# Evaluate a single Brain Object
python -m evaluation.runner --reel DZAqhTjN6-R

# Evaluate the entire active valid library
python -m evaluation.runner --all

# View an aggregate evaluation report
python -m evaluation.runner --report run_20260904_224410
```

### 7 Evaluation Dimensions (0–100 Normalized Score):
1. **Category Accuracy** (0–10): Dominant-purpose classification correctness.
2. **Grounding & Factuality** (0–20): Absence of hallucinations or fabricated URLs/tools.
3. **Completeness** (0–15): Coverage of all key steps, ingredients, settings, and takeaways.
4. **Summary / Insight Quality** (0–15): Clarity, conciseness, and high-density summary.
5. **Structured Extraction Accuracy** (0–20): Category-specific schema population.
6. **Tags / Tools / Resources** (0–10): Grounded entity and tool extraction.
7. **Multimodal Utilization** (0–10 when applicable): Effective synthesis of audio and visual frame observations.

---

## Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/ReelForge.git
cd ReelForge
```

### 2. Create and Activate Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 4. Start OmniRoute Gateway
Ensure OmniRoute is running locally:
```bash
omniroute serve
```

### 5. Install FFmpeg
* **Windows (via winget)**:
  ```powershell
  winget install Gyan.FFmpeg
  ```
* **macOS (via Homebrew)**:
  ```bash
  brew install ffmpeg
  ```
* **Linux (Ubuntu/Debian)**:
  ```bash
  sudo apt update && sudo apt install ffmpeg
  ```

---

## Configuration

Copy the example environment file and configure your credentials:

```bash
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
MICROSOFT_CLIENT_ID=your_microsoft_azure_app_client_id_here
OMNIROUTE_BASE_URL=http://localhost:20128/v1
OMNIROUTE_API_KEY=your_optional_omniroute_key
TEXT_MODEL=gemini/gemini-3.1-flash-lite
EVALUATION_MODEL=gemini/gemini-3.1-flash-lite
VISION_MODEL=gemini/gemini-3.1-flash-lite
VISION_MAX_FRAMES=8
```

---

## Running ReelForge

### Primary Mode: Telegram Bot
Start the Telegram bot listener:
```bash
python bot/telegram_bot.py
```
Open Telegram, find your bot, send `/start`, and paste any Instagram Reel link.

### CLI Mode: Single Reel Ingestion
To process a single Reel directly from the command line:
```bash
python main.py "https://www.instagram.com/reel/EXAMPLE_CODE/"
```

---

## Running Tests

Run the complete regression test suite:
```bash
python -m unittest scratch/test_evaluation_unit.py scratch/test_v26_layer1.py scratch/test_v26_layer1_bot.py scratch/test_v26_layer2.py scratch/test_v26_layer2_bot.py scratch/test_v25_layer3_storage.py scratch/test_v25_layer3_bot.py scratch/test_v25_encoding.py scratch/test_escape_html.py
```

---

## License

MIT License. See `LICENSE` for details.
