# ReelForge

> Turn Instagram Reels into structured, category-specific knowledge and automatically publish clean notes to Microsoft OneNote.

ReelForge is an automated pipeline that extracts actionable intelligence from short-form video content. When you send an Instagram Reel URL to the ReelForge Telegram bot, it downloads the video, transcribes the audio, classifies the content into one of 10 distinct domains, extracts structured metadata, and organizes it into dedicated sections in Microsoft OneNote.

---

## What It Does

Short-form videos are packed with high-value knowledge—coding roadmaps, recipes, workout routines, cinematography tips, financial breakdowns, and productivity frameworks. However, this knowledge is difficult to search, reference, or organize.

ReelForge bridges this gap:

1. **Ingests** an Instagram Reel via Telegram or CLI.
2. **Extracts** audio and generates accurate local transcriptions with Whisper.
3. **Classifies** the content using local LLM inference (**Qwen 2.5 7B**).
4. **Extracts domain-specific schemas** (e.g. ingredients & steps for food; exercises & muscle groups for gym; tools & code for programming).
5. **Constructs a Structured Brain Object** (persisted locally as JSON).
6. **Publishes** a formatted, styled page directly into the appropriate OneNote notebook section via Microsoft Graph API.

---

## 10 Specialized Knowledge Categories

ReelForge routes each reel to a custom extractor tailored for its specific domain:

| # | Category | Key Extracted Fields |
|---|---|---|
| 1 | **Programming** | Language/Stack, key concepts, code snippets, tools, action items, best practices |
| 2 | **AI** | Models, tools, prompts, use cases, concepts, key takeaways |
| 3 | **Food** | Dishes, ingredient lists, step-by-step instructions, cookware, cuisine, tips |
| 4 | **Photography** | Camera settings (ISO/shutter/aperture), gear, lighting, techniques, editing tools |
| 5 | **Gym** | Exercises, targeted muscles, equipment, sets & reps, form cues, workout type |
| 6 | **Movies & Edits** | Film/show titles, editing software, transitions, effects, templates |
| 7 | **Travel** | Destinations, hotels, transport, attractions, best time to visit, budget tips |
| 8 | **Finance** | Financial concepts, stocks/funds, metrics/percentages, actionable rules, risks |
| 9 | **Productivity** | Methods/frameworks, software tools, workflows, daily habits, action items |
| 10 | **Other** | General takeaways, recommendations, references, structured notes |

---

## Pipeline Architecture

```text
Instagram Reel URL
        │
        ▼
   [Telegram Bot]
        │
        ▼
  [Downloader] ──────▶ Audio extraction (yt-dlp)
        │
        ▼
  [Transcriber] ─────▶ Local Whisper transcription
        │
        ▼
  [Categorizer] ─────▶ Qwen 2.5 7B (10-category classification)
        │
        ▼
   [Dispatcher] ─────▶ Domain-specific prompt extractor
        │
        ▼
  [Brain Object] ────▶ Validated JSON metadata schema
        │
        ▼
 [Title Sanitizer] ──▶ Microsoft Graph character sanitization
        │
        ▼
[OneNote Publisher] ─▶ Microsoft Graph API (Category Section)
```

---

## Key Features

* **Zero-Cloud LLM Cost**: Powered entirely by local inference via Ollama (`qwen2.5:7b-instruct`).
* **High-Fidelity Categorization**: Context-aware prompt design enforcing the dominant-purpose rule with explicit negative boundary conditions.
* **Domain-Specific Schema Extraction**: Extracts structured fields rather than generic text summaries.
* **Graph API Error Handling & Title Sanitization**: Automatically normalizes colons, slashes, and reserved Microsoft Graph characters to prevent Error 20153 rejections.
* **Robust Pipeline Status**: Explicit success/error propagation with immediate status feedback in Telegram.
* **Complete Privacy**: Downloaded videos are cleaned up automatically; metadata is saved locally to JSON before publishing.

---

## Prerequisites & Requirements

### System Requirements
* **Operating System**: Windows 10/11, macOS, or Linux
* **Python**: Version 3.10 or higher
* **Ollama**: Installed and running locally ([ollama.ai](https://ollama.ai))
* **FFmpeg**: Required for audio extraction and transcription

### External Accounts & Tokens
1. **Telegram Bot Token**: Created via [@BotFather](https://t.me/botfather).
2. **Microsoft Azure App (Client ID)**: Registered in Microsoft Entra ID with `Notes.Create` and `Notes.ReadWrite` permissions for Microsoft Graph OneNote access.

---

## Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/ReelForge.git
cd ReelForge
```

### 2. Create and Activate a Virtual Environment
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

### 4. Install & Prepare Local Models
Ensure Ollama is running, then pull the production models:
```bash
# Pull production categorization and extraction model
ollama pull qwen2.5:7b-instruct
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

Copy the example environment file and add your credentials:

```bash
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
MICROSOFT_CLIENT_ID=your_microsoft_azure_app_client_id_here
```

### Optional Environment Variables:
* `FFMPEG_PATH`: Explicit path to FFmpeg `bin/` directory (if not present in system PATH).

---

## Microsoft OneNote First-Run Authentication

ReelForge uses MSAL (Microsoft Authentication Library) with device-code / interactive OAuth flow.

1. On the very first run, ReelForge will prompt you in the console:
   ```text
   To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code XXXXXXXX to authenticate.
   ```
2. Open the URL, enter the code, and sign in with your Microsoft account.
3. Once authenticated, tokens are cached locally in `token_cache.bin` (which is excluded from Git).
4. ReelForge will automatically create an **"InstaBrain"** notebook in OneNote and generate section tabs for each category.

---

## Running ReelForge

### Primary Mode: Telegram Bot
Start the Telegram bot listener:
```bash
python bot/telegram_bot.py
```
Open Telegram, find your bot, and send `/start`. Then paste any Instagram Reel link.

### CLI Mode: Single Reel Ingestion
To process a single reel directly from the command line:
```bash
python main.py "https://www.instagram.com/reel/EXAMPLE_CODE/"
```

---

## Project Structure

```text
ReelForge/
├── bot/
│   ├── __init__.py
│   └── telegram_bot.py           # Telegram bot handler & message loop
├── download/
│   ├── __init__.py
│   └── downloader.py             # yt-dlp video downloader & metadata extractor
├── processing/
│   ├── __init__.py
│   ├── pipeline.py               # End-to-end orchestration pipeline
│   ├── categorizer.py            # Qwen 2.5 7B categorization engine
│   ├── dispatcher.py             # Route category to specific extractor
│   ├── transcriber.py            # Whisper audio transcription
│   ├── ai_extractor.py           # AI category extractor
│   ├── finance_extractor.py      # Finance category extractor
│   ├── food_extractor.py         # Food category extractor
│   ├── gym_extractor.py          # Gym category extractor
│   ├── movies_edits_extractor.py # Movies & Edits extractor
│   ├── other_extractor.py        # General fallback extractor
│   ├── photography_extractor.py  # Photography extractor
│   ├── productivity_extractor.py # Productivity extractor
│   ├── programming_extractor.py  # Programming extractor
│   └── travel_extractor.py       # Travel extractor
├── prompts/                      # 11 production system prompts
│   ├── categorizer.txt
│   └── *_extractor.txt
├── storage/
│   ├── __init__.py
│   └── brain_object.py           # Brain Object JSON schema builder
├── onenote/
│   ├── __init__.py
│   ├── graph_client.py           # MS Graph API client & token cache
│   ├── formatter.py              # HTML template formatter for OneNote
│   ├── sanitizer.py              # Microsoft Graph title sanitizer (Error 20153 fix)
│   └── writer.py                 # Notebook/section resolver & page publisher
├── evaluation/                   # Standalone LLM evaluation benchmark suite
│   ├── evaluate.py
│   ├── judge_prompt.txt
│   ├── RUBRIC.md
│   └── test_cases/
├── examples/                     # Sanitized example Brain Object JSONs
│   ├── programming_example.json
│   └── food_example.json
├── config.py                     # Global configuration & environment loader
├── main.py                       # CLI entry point
├── requirements.txt              # Production Python dependencies
├── .env.example                  # Environment variable template
└── .gitignore                    # Secrets, caches & runtime media exclusions
```

---

## Evaluation Benchmark Suite

The `evaluation/` directory contains an experimental, evidence-first evaluation engine. It audits generated Brain Objects against source reel evidence across hallucination resistance, recall, and category fidelity.

> **Note**: The evaluation suite is an independent supporting tool and is not required for normal production execution.

---

## Limitations & Accuracy

* **Classification Accuracy**: In a controlled 28-reel representative regression benchmark spanning all 10 categories, `qwen2.5:7b-instruct` achieved 100% (28/28) accuracy. While highly accurate, edge-case reels with minimal speech and ambiguous captions may occasionally default to `Other`.
* **Private Reels**: Reels from private accounts or age-restricted content cannot be retrieved without authenticated Instagram sessions.
* **Audio Quality**: Background music or heavily distorted audio can impact transcription precision.

---

## License

MIT License. See `LICENSE` for details.
