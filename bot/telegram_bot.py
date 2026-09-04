import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, BRAINS_DIR, CATEGORIES
from onenote.sanitizer import sanitize_page_title
from processing.pipeline import process_reel
from storage.brain_object import (
    archive_brain_object,
    find_related_brain_objects,
    get_all_topics,
    get_brain_categories,
    get_brain_objects_by_creator,
    get_brain_objects_by_topic,
    get_cached_brain_object,
    get_knowledge_stats,
    get_recent_brain_objects,
    is_valid_instagram_url,
    load_brain_object,
    recategorize_brain_object,
    restore_brain_object,
    scan_valid_brain_objects,
    search_brain_objects,
)


def _list_items(items):
    if not items:
        return "None identified."
    return "\n".join(f"- {item}" for item in items)


def _first_available(knowledge, keys):
    for key in keys:
        value = knowledge.get(key)
        if value:
            return value
    return []


def _truncate(text: str, max_len: int = 130) -> str:
    if not text:
        return ""
    clean = " ".join(str(text).split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


def _format_summary(result):
    brain = result["brain"] or {}
    knowledge = brain.get("knowledge") or {}
    content = brain.get("content") or {}
    source = brain.get("source") or {}
    caption = content.get("caption") or ""
    title = knowledge.get("title") or (
        caption.splitlines()[0] if caption else source.get("shortcode", "Untitled")
    )
    category = result.get("category") or knowledge.get("category") or "Unknown"
    summary = knowledge.get("summary") or "No summary available."
    key_takeaways = _first_available(
        knowledge,
        ("tips", "use_cases", "concepts", "techniques", "steps", "workflows")
    )
    resources = []

    for key in (
        "websites",
        "tools",
        "apps",
        "editing_apps",
        "gear",
        "equipment",
        "models",
        "calculators",
    ):
        resources.extend(knowledge.get(key) or [])

    action_items = knowledge.get("action_items") or knowledge.get("tips") or []

    return (
        "InstaBrain Summary\n\n"
        f"Title: {title}\n\n"
        f"Summary:\n{summary}\n\n"
        f"Category:\n{category}\n\n"
        f"Key Takeaways:\n{_list_items(key_takeaways)}\n\n"
        f"Resources:\n{_list_items(resources)}\n\n"
        f"Action Items:\n{_list_items(action_items)}"
    )


def _format_reel_card(brain: dict, index: int | None = None) -> str:
    k = brain.get("knowledge") or {}
    c = brain.get("content") or {}
    cr = brain.get("creator") or {}
    src = brain.get("source") or {}
    reel_id = brain.get("id") or src.get("shortcode") or "Unknown"
    caption = c.get("caption") or ""
    title = k.get("title") or (
        caption.splitlines()[0] if caption else src.get("shortcode", "Untitled")
    )
    category = k.get("category") or "Unknown"
    summary = _truncate(k.get("summary") or caption or "No summary available.")

    username = cr.get("username")
    creator_str = f"@{username}" if username else "Unknown creator"
    prefix = f"{index}. " if index is not None else ""

    return (
        f"{prefix}🏷️ [{category}] {title}\n"
        f"   📝 {summary}\n"
        f"   👤 {creator_str} | 🆔 `{reel_id}`\n"
        f"   👉 Retrieve: /get {reel_id}"
    )


def _format_related_card(match: dict, index: int = 1) -> str:
    brain = match.get("brain") or {}
    reasons = match.get("reasons") or []
    k = brain.get("knowledge") or {}
    c = brain.get("content") or {}
    cr = brain.get("creator") or {}
    src = brain.get("source") or {}
    reel_id = brain.get("id") or src.get("shortcode") or "Unknown"
    caption = c.get("caption") or ""
    title = k.get("title") or (
        caption.splitlines()[0] if caption else src.get("shortcode", "Untitled")
    )
    category = k.get("category") or "Unknown"
    username = cr.get("username")
    creator_str = f"@{username}" if username else "Unknown creator"
    reasons_str = " • ".join(reasons) if reasons else "Related topic"

    return (
        f"{index}. 🏷️ [{category}] {title}\n"
        f"   🔗 Matched on: {reasons_str}\n"
        f"   👤 {creator_str} | 🆔 `{reel_id}`\n"
        f"   👉 Retrieve: /get {reel_id}"
    )


def _format_detailed_card(brain: dict) -> str:
    k = brain.get("knowledge") or {}
    c = brain.get("content") or {}
    cr = brain.get("creator") or {}
    src = brain.get("source") or {}
    ts = brain.get("timestamps") or {}
    prov = brain.get("provenance") or {}
    caption = c.get("caption") or ""
    reel_id = brain.get("id") or src.get("shortcode") or "Unknown"
    title = k.get("title") or (
        caption.splitlines()[0] if caption else src.get("shortcode", "Untitled")
    )
    category = k.get("category") or "Unknown"
    summary = k.get("summary") or caption or "No summary available."

    user = cr.get("username")
    full = cr.get("full_name")
    if user and full and user != full:
        creator_str = f"@{user} ({full})"
    elif user:
        creator_str = f"@{user}"
    else:
        creator_str = "Unknown"

    processed_at = ts.get("processed_at") or "Unknown"
    source_url = src.get("url") or f"https://www.instagram.com/reel/{reel_id}/"
    page_title = sanitize_page_title(title)

    modality = prov.get("modality")
    if not modality:
        has_audio = bool(c.get("transcript"))
        has_vision = bool(c.get("vision_analysis"))
        if has_audio and has_vision:
            modality = "multimodal"
        elif has_audio:
            modality = "audio_caption"
        elif has_vision:
            modality = "vision_caption"
        else:
            modality = "caption_only"

    key_takeaways = _first_available(
        k,
        (
            "key_takeaways",
            "key_concepts",
            "tips",
            "use_cases",
            "concepts",
            "techniques",
            "steps",
            "workflows",
            "action_items",
        ),
    )
    takeaways_str = _list_items(key_takeaways)

    return (
        "🧠 Reel Knowledge Card\n\n"
        f"Title: {title}\n"
        f"Category: {category}\n"
        f"Modality: {modality}\n"
        f"Creator: {creator_str}\n"
        f"Processed: {processed_at}\n\n"
        f"Summary:\n{summary}\n\n"
        f"Key Takeaways:\n{takeaways_str}\n\n"
        "📓 OneNote Location:\n"
        "Notebook: InstaBrain\n"
        f"Section: {category}\n"
        f"Page: {page_title}\n\n"
        f"🔗 Source Reel: {source_url}\n"
        f"👉 Discover Related: /related {reel_id}"
    )


# ---------------------------------------------------------------------------
# Discovery & Management Commands
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Welcome to ReelForge (InstaBrain)!\n\n"
        "Send me any Instagram Reel URL to extract structured knowledge and save it to OneNote.\n\n"
        "💡 Discovery & Management Commands:\n"
        "• /recent — View recent reels\n"
        "• /search <query> — Search by keywords\n"
        "• /category — Browse by category\n"
        "• /topics — Browse all knowledge topics\n"
        "• /topic <name> — Search reels by topic tag\n"
        "• /creator <user> — Search reels by creator\n"
        "• /related <id> — Discover related reels\n"
        "• /get <reel_id> — View full details & JSON\n"
        "• /stats — Knowledge base statistics\n"
        "• /reprocess <id> — Re-run full pipeline\n"
        "• /force <url> — Ingest with cache bypass\n"
        "• /recat <id> <cat> — Change category\n"
        "• /archive <id> — Move to archive\n"
        "• /restore <id> — Restore from archive\n"
        "• /help — Full command guide"
    )
    await update.message.reply_text(welcome_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 ReelForge Knowledge Discovery & Management\n\n"
        "🔍 Discovery Commands:\n"
        "• /recent [limit] — View latest processed reels (default 5, max 10)\n"
        "• /search <query> — Search reels by keyword, tag, or creator\n"
        "• /category [name] — Filter reels by category or list all categories\n"
        "• /topics [limit] — List all discovered topics/tags across the library\n"
        "• /topic <name> — Find reels matching an exact topic/tag\n"
        "• /creator <username> — Find all reels by a specific creator\n"
        "• /related <reel_id> — Find related reels sharing tags, tools, or creator\n"
        "• /get <reel_id> — View full knowledge card & download JSON\n"
        "• /stats — View library statistics and category breakdown\n\n"
        "⚡ Ingestion & Lifecycle Commands:\n"
        "• /force <url> — Force-ingest a Reel URL (bypasses cache)\n"
        "• /reprocess <reel_id> — Re-extract an existing Reel using its source URL\n"
        "• /recat <reel_id> <category> — Change category and update schema locally\n"
        "• /archive <reel_id> — Move Reel to archive (hides from discovery)\n"
        "• /restore <reel_id> — Restore an archived Reel to active library\n"
        "• /help — Show this guide\n\n"
        "📥 Add New Reels:\n"
        "Paste any Instagram Reel link directly into this chat to process and save it to OneNote."
    )
    await update.message.reply_text(help_text)


async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    limit = 5
    if context.args:
        try:
            limit = int(context.args[0])
        except (ValueError, TypeError):
            limit = 5
    limit = max(1, min(limit, 10))

    brains = get_recent_brain_objects(limit=limit)
    if not brains:
        await update.message.reply_text("📚 No processed reels found in the library yet.")
        return

    cards = [_format_reel_card(b, index=i + 1) for i, b in enumerate(brains)]
    text = (
        f"📚 Recent Reels ({len(brains)} shown):\n\n"
        + "\n\n".join(cards)
        + "\n\n💡 Use /get <reel_id> to view full details."
    )
    await update.message.reply_text(text)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a search query.\n\n"
            "Example: /search python\n"
            "Example: /search pizza\n\n"
            "💡 Use /help to see all commands."
        )
        return

    query = " ".join(context.args).strip()
    brains = search_brain_objects(query=query, limit=10)
    if not brains:
        await update.message.reply_text(
            f'🔍 No reels found matching "{query}".\n\n'
            "💡 Try searching with different keywords, or use /recent or /category."
        )
        return

    cards = [_format_reel_card(b, index=i + 1) for i, b in enumerate(brains)]
    text = (
        f'🔍 Search Results for "{query}" ({len(brains)} found):\n\n'
        + "\n\n".join(cards)
        + "\n\n💡 Use /get <reel_id> to view full details."
    )
    await update.message.reply_text(text)


async def category_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        counts = get_brain_categories()
        lines = [f"• {cat}: {count} reels" for cat, count in counts.items()]
        text = (
            "📂 ReelForge Categories:\n\n"
            + "\n".join(lines)
            + "\n\n💡 To view reels in a category: /category <name>\n"
            "Example: /category Food"
        )
        await update.message.reply_text(text)
        return

    target_name = " ".join(context.args).strip()
    matched_category = None
    for cat in CATEGORIES:
        if cat.lower() == target_name.lower():
            matched_category = cat
            break

    if not matched_category:
        valid_cats = ", ".join(CATEGORIES)
        await update.message.reply_text(
            f'⚠️ Unknown category: "{target_name}"\n\n'
            f"Available categories:\n{valid_cats}\n\n"
            "💡 Type /category to view counts per category."
        )
        return

    brains = search_brain_objects(query="", category=matched_category, limit=10)
    if not brains:
        await update.message.reply_text(
            f"📂 No reels saved under category '{matched_category}' yet."
        )
        return

    cards = [_format_reel_card(b, index=i + 1) for i, b in enumerate(brains)]
    text = (
        f"📂 {matched_category} Reels ({len(brains)} found):\n\n"
        + "\n\n".join(cards)
        + "\n\n💡 Use /get <reel_id> to view full details."
    )
    await update.message.reply_text(text)


async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID.\n\n"
            "Example: /get DcWdh3bhowK\n\n"
            "💡 Use /recent or /search to find Reel IDs."
        )
        return

    target_id = context.args[0].strip()
    brain = get_cached_brain_object(target_id)
    if not brain:
        matched = [
            b for b in scan_valid_brain_objects()
            if b.get("id") == target_id or (b.get("source") or {}).get("shortcode") == target_id
        ]
        if matched:
            brain = matched[0]

    if not brain:
        await update.message.reply_text(
            f'❌ Reel "{target_id}" not found or not fully processed.\n\n'
            "💡 Check the Reel ID with /recent or /search."
        )
        return

    detailed_text = _format_detailed_card(brain)
    await update.message.reply_text(detailed_text)

    reel_id = brain.get("id") or target_id
    brain_file = Path(BRAINS_DIR) / f"{reel_id}.json"
    if brain_file.exists():
        with open(brain_file, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=brain_file.name
            )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_knowledge_stats()
    total = stats["total_reels"]
    counts = stats["category_counts"]
    unique_tags = stats["unique_tags_count"]
    latest_ts = stats["latest_processed_at"] or "None"
    latest_id = stats["latest_reel_id"]
    latest_title = stats["latest_title"]

    latest_info = f"{latest_ts}"
    if latest_id:
        latest_info += f" ({latest_id})"
        if latest_title:
            latest_info += f" — {_truncate(latest_title, 40)}"

    cat_lines = [f"  • {cat}: {count}" for cat, count in counts.items()]

    text = (
        "📊 ReelForge Knowledge Base Stats\n\n"
        f"Total Processed Reels: {total}\n"
        f"Unique Knowledge Tags: {unique_tags}\n"
        f"Latest Processed: {latest_info}\n\n"
        "Category Breakdown:\n"
        + "\n".join(cat_lines)
    )
    await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# Ingestion Flow (Writes & Ingestion Lock)
# ---------------------------------------------------------------------------

_PROCESS_LOCK = asyncio.Lock()


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.strip()

    if not is_valid_instagram_url(message):
        await update.message.reply_text(
            "Please send a valid Instagram Reel link, or type /help for discovery commands."
        )
        return

    await update.message.reply_text(
        "Reel received. Added to processing queue..."
    )

    loop = asyncio.get_running_loop()

    def progress(message_text):
        future = asyncio.run_coroutine_threadsafe(
            update.message.reply_text(message_text),
            loop
        )
        future.result()

    async with _PROCESS_LOCK:
        result = await asyncio.to_thread(process_reel, message, progress)

    if not result["success"]:
        await update.message.reply_text(
            f"❌ Reel processing failed: {result.get('error', 'Unknown error')}"
        )
        return

    is_cached = result.get("cached", False)
    onenote_success = result.get("onenote_success", False)
    onenote_error = result.get("onenote_error")
    category = result.get("category") or "Unknown"

    if is_cached:
        status_line = f"⚡ Already Processed (Cached Brain Object)\n✅ OneNote Section: {category}\n\n"
    elif onenote_success:
        status_line = f"✅ OneNote Page Created in Section: {category}\n\n"
    else:
        status_line = f"⚠️ Saved to Brain Object, but OneNote publishing failed: {onenote_error}\n\n"

    summary_text = status_line + _format_summary(result)
    await update.message.reply_text(summary_text)

    brain_path = result.get("brain_path")

    if brain_path is not None and Path(brain_path).exists():
        with open(brain_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=Path(brain_path).name
            )


async def force_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide an Instagram Reel URL to force-process.\n\n"
            "Example: /force https://www.instagram.com/reel/C14V0wutZzd/\n\n"
            "💡 This intentionally bypasses cache and re-extracts the Reel."
        )
        return

    url = context.args[0].strip()
    if not is_valid_instagram_url(url):
        await update.message.reply_text(
            "❌ Invalid URL. Please provide a valid Instagram Reel link.\n\n"
            "Example: /force https://www.instagram.com/reel/C14V0wutZzd/"
        )
        return

    await update.message.reply_text(
        "⚡ Force ingestion requested. Bypassing cache..."
    )

    loop = asyncio.get_running_loop()

    def progress(message_text):
        future = asyncio.run_coroutine_threadsafe(
            update.message.reply_text(message_text),
            loop
        )
        future.result()

    async with _PROCESS_LOCK:
        result = await asyncio.to_thread(process_reel, url, progress, True)

    if not result["success"]:
        await update.message.reply_text(
            f"❌ Force processing failed: {result.get('error', 'Unknown error')}"
        )
        return

    onenote_success = result.get("onenote_success", False)
    onenote_error = result.get("onenote_error")
    category = result.get("category") or "Unknown"

    if onenote_success:
        status_line = f"⚡ Force-Processed (Fresh Extraction)\n✅ OneNote Page Created in Section: {category}\n\n"
    else:
        status_line = f"⚡ Force-Processed (Fresh Extraction)\n⚠️ Saved to Brain Object, but OneNote publishing failed: {onenote_error}\n\n"

    summary_text = status_line + _format_summary(result)
    await update.message.reply_text(summary_text)

    brain_path = result.get("brain_path")
    if brain_path is not None and Path(brain_path).exists():
        with open(brain_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=Path(brain_path).name
            )


async def reprocess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID to reprocess.\n\n"
            "Example: /reprocess DcK7QPXuRBJ\n\n"
            "💡 Use /recent or /search to find Reel IDs."
        )
        return

    reel_id = context.args[0].strip()

    try:
        brain = load_brain_object(reel_id)
    except FileNotFoundError:
        await update.message.reply_text(
            f"❌ Reel '{reel_id}' not found in active library.\n\n"
            "💡 Check the Reel ID with /recent or /search."
        )
        return
    except Exception as e:
        await update.message.reply_text(
            f"❌ Error loading Reel '{reel_id}': {e}"
        )
        return

    source_info = brain.get("source") or {}
    source_url = source_info.get("url")
    if not is_valid_instagram_url(source_url):
        await update.message.reply_text(
            f"❌ Cannot reprocess Reel '{reel_id}': Original source Instagram URL is missing or invalid in Brain Object.\n\n"
            "💡 Tip: Use /force <url> with the direct Instagram link instead."
        )
        return

    await update.message.reply_text(
        f"🔄 Reprocessing Reel `{reel_id}`...\n"
        f"🔗 Source: {source_url}\n"
        "Re-running Whisper, Vision, and LLM extraction..."
    )

    loop = asyncio.get_running_loop()

    def progress(message_text):
        future = asyncio.run_coroutine_threadsafe(
            update.message.reply_text(message_text),
            loop
        )
        future.result()

    async with _PROCESS_LOCK:
        result = await asyncio.to_thread(process_reel, source_url, progress, True)

    if not result["success"]:
        await update.message.reply_text(
            f"❌ Reprocessing failed: {result.get('error', 'Unknown error')}"
        )
        return

    onenote_success = result.get("onenote_success", False)
    onenote_error = result.get("onenote_error")
    category = result.get("category") or "Unknown"

    if onenote_success:
        status_line = f"🔄 Successfully Reprocessed `{reel_id}`\n✅ OneNote Page Created in Section: {category}\n\n"
    else:
        status_line = f"🔄 Successfully Reprocessed `{reel_id}`\n⚠️ Saved to Brain Object, but OneNote publishing failed: {onenote_error}\n\n"

    summary_text = status_line + _format_summary(result)
    await update.message.reply_text(summary_text)

    brain_path = result.get("brain_path")
    if brain_path is not None and Path(brain_path).exists():
        with open(brain_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=Path(brain_path).name
            )


async def archive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID to archive.\n\n"
            "Example: /archive DcK7QPXuRBJ\n\n"
            "💡 Use /recent or /search to find Reel IDs."
        )
        return

    reel_id = context.args[0].strip()
    async with _PROCESS_LOCK:
        success, msg = archive_brain_object(reel_id)

    if success:
        await update.message.reply_text(
            f"📦 Reel `{reel_id}` archived successfully.\n\n"
            "It will no longer appear in /recent, /search, /category, or /stats.\n"
            f"💡 To restore it later: /restore {reel_id}"
        )
    else:
        await update.message.reply_text(f"❌ {msg}")


async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID to restore.\n\n"
            "Example: /restore DcK7QPXuRBJ"
        )
        return

    reel_id = context.args[0].strip()
    async with _PROCESS_LOCK:
        success, msg = restore_brain_object(reel_id)

    if success:
        await update.message.reply_text(
            f"♻️ Reel `{reel_id}` restored successfully.\n\n"
            "It is now active and will appear in discovery commands and cache checks.\n"
            f"👉 View details: /get {reel_id}"
        )
    else:
        await update.message.reply_text(f"❌ {msg}")


async def recat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        valid_cats = ", ".join(CATEGORIES)
        await update.message.reply_text(
            "⚠️ Usage: /recat <reel_id> <category>\n\n"
            "Example: /recat DcK7QPXuRBJ Food\n\n"
            f"Available categories:\n{valid_cats}"
        )
        return

    reel_id = context.args[0].strip()
    new_category = " ".join(context.args[1:]).strip()

    async with _PROCESS_LOCK:
        success, msg, updated_brain = recategorize_brain_object(reel_id, new_category)

    if success and updated_brain:
        cat = updated_brain.get("knowledge", {}).get("category")
        await update.message.reply_text(
            f"🏷️ Category updated to '{cat}' for Reel `{reel_id}`.\n\n"
            f"💡 Note: Knowledge metadata and schema have been updated locally. Use /reprocess {reel_id} if you want full multimodal knowledge re-extracted under the {cat} category."
        )
    else:
        await update.message.reply_text(f"❌ {msg}")


async def related_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID.\n\n"
            "Example: /related DcK7QPXuRBJ\n\n"
            "💡 Use /recent or /search to find Reel IDs."
        )
        return

    target_id = context.args[0].strip()
    brain = get_cached_brain_object(target_id)
    if not brain:
        matched = [
            b for b in scan_valid_brain_objects()
            if b.get("id") == target_id or (b.get("source") or {}).get("shortcode") == target_id
        ]
        if matched:
            brain = matched[0]

    if not brain:
        await update.message.reply_text(
            f"❌ Reel '{target_id}' not found in active library.\n\n"
            "💡 Check the Reel ID with /recent or /search."
        )
        return

    reel_id = brain.get("id") or target_id
    related_matches = find_related_brain_objects(reel_id, limit=5)

    if not related_matches:
        await update.message.reply_text(
            f"🔍 No related Reels found for '{reel_id}'.\n\n"
            "💡 Use /topics to explore related themes."
        )
        return

    k = brain.get("knowledge") or {}
    title = k.get("title") or reel_id
    cards = [_format_related_card(m, index=i + 1) for i, m in enumerate(related_matches)]
    text = (
        f"🔗 Related Reels for '{title}' ({len(related_matches)} found):\n\n"
        + "\n\n".join(cards)
        + "\n\n💡 Use /get <reel_id> to view full details."
    )
    await update.message.reply_text(text)


async def topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    limit = 15
    if context.args:
        try:
            limit = int(context.args[0])
        except (ValueError, TypeError):
            limit = 15
    clamped_limit = max(1, min(limit, 50))

    topics = get_all_topics()
    if not topics:
        await update.message.reply_text("🏷️ No knowledge topics found in the library yet.")
        return

    total_unique = len(topics)
    shown_topics = topics[:clamped_limit]
    lines = [f"• #{name} — {count} reel{'s' if count > 1 else ''}" for name, count in shown_topics]

    text = (
        f"🏷️ Knowledge Topics ({len(shown_topics)} of {total_unique} shown):\n\n"
        + "\n".join(lines)
        + "\n\n💡 To view reels for a topic: /topic <name>\n"
        "Example: /topic productivity"
    )
    await update.message.reply_text(text)


async def topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a topic or tag.\n\n"
            "Example: /topic productivity\n"
            "Example: /topic python\n\n"
            "💡 Use /topics to see all discovered topics."
        )
        return

    topic_name = " ".join(context.args).strip()
    clean_name = topic_name.lstrip("#")
    brains = get_brain_objects_by_topic(topic_name, limit=10)

    if not brains:
        await update.message.reply_text(
            f"🔍 No Reels found for topic '#{clean_name}'.\n\n"
            "💡 Type /topics to browse all available topics."
        )
        return

    cards = [_format_reel_card(b, index=i + 1) for i, b in enumerate(brains)]
    text = (
        f"🏷️ Reels tagged with #{clean_name} ({len(brains)} found):\n\n"
        + "\n\n".join(cards)
        + "\n\n💡 Use /get <reel_id> to view full details."
    )
    await update.message.reply_text(text)


async def creator_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a creator username.\n\n"
            "Example: /creator Noor\n\n"
            "💡 You can omit the leading '@'."
        )
        return

    creator_name = " ".join(context.args).strip()
    clean_name = creator_name.lstrip("@")
    brains = get_brain_objects_by_creator(creator_name, limit=10)

    if not brains:
        await update.message.reply_text(
            f"👤 No Reels found for creator '@{clean_name}'.\n\n"
            "💡 Check the creator with /recent or /search."
        )
        return

    cards = [_format_reel_card(b, index=i + 1) for i, b in enumerate(brains)]
    text = (
        f"👤 Reels by @{clean_name} ({len(brains)} found):\n\n"
        + "\n\n".join(cards)
        + "\n\n💡 Use /get <reel_id> to view full details."
    )
    await update.message.reply_text(text)


def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register discovery & lifecycle commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("recent", recent_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("category", category_command))
    app.add_handler(CommandHandler("topics", topics_command))
    app.add_handler(CommandHandler("topic", topic_command))
    app.add_handler(CommandHandler("creator", creator_command))
    app.add_handler(CommandHandler("related", related_command))
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("force", force_command))
    app.add_handler(CommandHandler("reprocess", reprocess_command))
    app.add_handler(CommandHandler("archive", archive_command))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(CommandHandler("recat", recat_command))

    # Register URL ingestion handler (for non-command text)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive,
        )
    )

    print("InstaBrain Bot Running...")

    app.run_polling()


if __name__ == "__main__":
    run_bot()
