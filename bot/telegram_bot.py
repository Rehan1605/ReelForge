import asyncio
import datetime
import io
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN,
    BRAINS_DIR,
    CATEGORIES,
    REELFORGE_EMBEDDED_WORKER,
    REELFORGE_START_OAUTH_SERVER,
)
from auth.oauth_server import start_oauth_for_user, start_oauth_server, stop_oauth_server
from auth.device_flow import (
    DeviceFlowDuplicateAccountError,
    DeviceFlowError,
    complete_device_flow_connection,
    initiate_device_flow_for_user,
    poll_device_flow_step,
)

logger = logging.getLogger(__name__)
from onenote.sanitizer import sanitize_page_title
from processing.pipeline import process_reel
from processing.worker import validate_worker_runtime, worker_loop
from bot.notifier import (
    JobNotifier as _JobNotifier,
    notifier_kind_for_job,
    result_summary_text as _result_summary_text,
    onenote_issue_message as _onenote_issue_message,
    _first_available,
    _list_items,
)
from storage.brain_object import (
    archive_brain_object,
    associate_user_with_brain,
    extract_reel_id_from_url,
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
from storage.job import (
    JOB_TYPE_PUBLICATION,
    get_job_for_user,
    list_jobs_for_user,
    submit_job,
)
from storage.publication import get_publication
from storage.user import disconnect_user_microsoft, get_user_microsoft

# Callback action identifiers (never carry a user_id; the user is always
# derived from the authenticated Telegram update).
DISCONNECT_START = "disconnect:start"
DISCONNECT_CONFIRM = "disconnect:confirm"
DISCONNECT_CANCEL = "disconnect:cancel"
STATUS_SHOW = "status:show"
DEVICE_CANCEL_PREFIX = "device_cancel:"


def _microsoft_status_text(user_id) -> str:
    """Build a safe Microsoft connection status message for a resolved user."""
    ms = get_user_microsoft(user_id)
    if ms is None or not ms.get("connected"):
        return (
            "Microsoft account: Not connected\n\n"
            "Use /connect to connect your Microsoft account."
        )

    account = ms.get("display_name") or ms.get("email") or "Microsoft account"
    notebook = ms.get("notebook_name") or "InstaBrain"
    return (
        "Microsoft account: Connected\n"
        f"Account: {account}\n"
        f"OneNote notebook: {notebook}"
    )


def _truncate(text: str, max_len: int = 130) -> str:
    if not text:
        return ""
    clean = " ".join(str(text).split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


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
# Multi-User Identity Resolution Helper
# ---------------------------------------------------------------------------

def _resolve_user(update: Update) -> tuple[dict | None, bool]:
    """
    Extract the authenticated Telegram user from update.effective_user and
    resolve or create their ReelForge account.
    Never accepts or trusts telegram_id from user input.
    """
    effective_user = getattr(update, "effective_user", None)
    if not update or not effective_user:
        return None, False
    telegram_id = getattr(effective_user, "id", None)
    if not isinstance(telegram_id, (int, str)):
        return None, False
    try:
        from storage.user import get_or_create_user
        return get_or_create_user(effective_user)
    except Exception as e:
        print(f"Notice: User resolution failed: {e}")
        return None, False


# ---------------------------------------------------------------------------
# Discovery & Management Commands
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, is_new = _resolve_user(update)
    effective_user = getattr(update, "effective_user", None)

    first_name = ""
    if user and isinstance(user, dict):
        profile = user.get("profile") or {}
        telegram_info = user.get("telegram") or {}
        first_name = profile.get("display_name") or telegram_info.get("first_name") or ""
    elif effective_user and getattr(effective_user, "first_name", None):
        first_name = effective_user.first_name

    first_name = str(first_name).strip()
    greeting_name = f", {first_name}" if first_name else ""

    if is_new or user is None:
        header = f"👋 Welcome to ReelForge{greeting_name}!"
        if is_new:
            header += "\n✨ Your account has been registered."
    else:
        header = f"👋 Welcome back to ReelForge{greeting_name}!"

    welcome_text = (
        f"{header}\n\n"
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
        "• /restore <id> — Restore from archive\n\n"
        "🔗 Microsoft OneNote:\n"
        "• /connect — Connect your Microsoft account\n"
        "• /mestatus — Check your OneNote connection\n"
        "• /disconnect — Disconnect Microsoft\n"
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
        "• /job <job_id> — Check a processing job's status\n"
        "• /jobs — List your recent jobs\n"
        "• /recat <reel_id> <category> — Change category and update schema locally\n"
        "• /archive <reel_id> — Move Reel to archive (hides from discovery)\n"
        "• /restore <reel_id> — Restore an archived Reel to active library\n"
        "• /help — Show this guide\n\n"
        "📥 Add New Reels:\n"
        "Paste any Instagram Reel link directly into this chat to process and save it to OneNote.\n\n"
        "🔗 Microsoft OneNote:\n"
        "To save reels to your OneNote:\n"
        "1. Connect Microsoft with /connect\n"
        "2. Send me a reel\n"
        "3. I'll process and organize it.\n\n"
        "• /connect — Connect your Microsoft account\n"
        "• /mestatus — Check connection status\n"
        "• /disconnect — Disconnect Microsoft account\n"
    )
    await update.message.reply_text(help_text)


async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    limit = 5
    if context.args:
        try:
            limit = int(context.args[0])
        except (ValueError, TypeError):
            limit = 5
    limit = max(1, min(limit, 10))

    brains = get_recent_brain_objects(limit=limit, user_id=user_id)
    if not brains:
        await update.message.reply_text("📚 No saved reels found in your library yet. Send me a Reel URL to get started!")
        return

    cards = [_format_reel_card(b, index=i + 1) for i, b in enumerate(brains)]
    text = (
        f"📚 Recent Reels ({len(brains)} shown):\n\n"
        + "\n\n".join(cards)
        + "\n\n💡 Use /get <reel_id> to view full details."
    )
    await update.message.reply_text(text)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a search query.\n\n"
            "Example: /search python\n"
            "Example: /search pizza\n\n"
            "💡 Use /help to see all commands."
        )
        return

    query = " ".join(context.args).strip()
    brains = search_brain_objects(query=query, limit=10, user_id=user_id)
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
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not context.args:
        counts = get_brain_categories(user_id=user_id)
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

    brains = search_brain_objects(query="", category=matched_category, limit=10, user_id=user_id)
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
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID.\n\n"
            "Example: /get DcWdh3bhowK\n\n"
            "💡 Use /recent or /search to find Reel IDs."
        )
        return

    target_id = context.args[0].strip()
    brain = get_cached_brain_object(target_id, user_id=user_id)
    if not brain:
        matched = [
            b for b in scan_valid_brain_objects(user_id=user_id)
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
    serialized = json.dumps(brain, indent=4).encode("utf-8")
    await update.message.reply_document(
        document=io.BytesIO(serialized),
        filename=f"{reel_id}.json",
    )


def stop_active_device_flows(app) -> None:
    """Cancel all active device flow polling tasks and clear tracking."""
    try:
        bot_data = getattr(app, "bot_data", {}) if app else {}
        active_flows = bot_data.get("active_device_flows", {}) if isinstance(bot_data, dict) else {}
        for uid, entry in list(active_flows.items()):
            task, flow = entry
            if isinstance(flow, dict):
                flow["expires_at"] = 0
            if hasattr(task, "cancel") and not task.done():
                task.cancel()
        active_flows.clear()
        print("Active Microsoft device flows: stopped.")
    except Exception as e:
        logger.warning("Error stopping active device flows: %s", e)


async def _poll_device_flow_task(
    bot,
    client,
    cache,
    flow: dict,
    user_id: str,
    chat_id,
    status_message_id,
    bot_data: dict | None,
) -> None:
    """Background task that polls Microsoft for device code completion.

    Executes non-blocking single-step polling in a worker thread and sleeps
    for the flow-specified interval. Upon completion, persists the connection,
    updates the prompt message, and notifies the user.
    """
    try:
        while time.time() < flow.get("expires_at", 0):
            interval = flow.get("interval", 5)
            await asyncio.sleep(interval)

            if time.time() >= flow.get("expires_at", 0):
                break

            result = await asyncio.to_thread(poll_device_flow_step, client, flow)

            if not isinstance(result, dict):
                continue

            if "access_token" in result:
                try:
                    updated = await asyncio.to_thread(
                        complete_device_flow_connection,
                        user_id,
                        cache,
                        result,
                    )
                    account_name = (
                        updated.get("display_name")
                        or updated.get("email")
                        or "Microsoft account"
                    )
                    try:
                        if status_message_id is not None:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=status_message_id,
                                text=f"Connected to Microsoft ({account_name}).",
                            )
                    except Exception:
                        pass

                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "Microsoft account connected successfully!\n\n"
                            f"Account: {account_name}\n\n"
                            "OneNote publishing is now enabled for your reels."
                        ),
                    )
                except DeviceFlowDuplicateAccountError:
                    try:
                        if status_message_id is not None:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=status_message_id,
                                text="⚠️ This Microsoft account is already connected to another ReelForge user.",
                            )
                    except Exception:
                        pass
                    await bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ This Microsoft account is already connected to another ReelForge user.",
                    )
                except Exception as exc:
                    logger.warning("Failed to complete device flow: %s", exc)
                    try:
                        if status_message_id is not None:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=status_message_id,
                                text="⚠️ Microsoft connection failed. Please try /connect again.",
                            )
                    except Exception:
                        pass
                    await bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ Microsoft connection failed. Please try /connect again.",
                    )
                return

            error = result.get("error")
            if error in ("authorization_pending", "slow_down"):
                continue
            elif error == "expired_token":
                try:
                    if status_message_id is not None:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_message_id,
                            text="⏱️ The Microsoft login code has expired. Use /connect to start a new connection.",
                        )
                except Exception:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="⏱️ The Microsoft login code has expired. Use /connect to start a new connection.",
                    )
                return
            elif error == "access_denied":
                try:
                    if status_message_id is not None:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_message_id,
                            text="❌ Microsoft connection was cancelled or denied.",
                        )
                except Exception:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="❌ Microsoft connection was cancelled or denied.",
                    )
                return
            else:
                logger.warning("Device flow polling error: %s", error)
                try:
                    if status_message_id is not None:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_message_id,
                            text="⚠️ Microsoft connection failed. Please try /connect again.",
                        )
                except Exception:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ Microsoft connection failed. Please try /connect again.",
                    )
                return

        # Expired by timestamp
        try:
            if status_message_id is not None:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text="⏱️ The Microsoft login code has expired. Use /connect to start a new connection.",
                )
        except Exception:
            await bot.send_message(
                chat_id=chat_id,
                text="⏱️ The Microsoft login code has expired. Use /connect to start a new connection.",
            )
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("Unexpected exception in device flow polling task: %s", exc)
    finally:
        if bot_data is not None and "active_device_flows" in bot_data:
            flows = bot_data["active_device_flows"]
            current = flows.get(user_id)
            if current and current[0] is asyncio.current_task():
                flows.pop(user_id, None)


async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    if user is None:
        await update.message.reply_text(
            "⚠️ Could not resolve your account. Please try /start first."
        )
        return

    # For security, Microsoft device login must not be conducted in group chats
    chat = getattr(update, "effective_chat", None)
    if chat and getattr(chat, "type", "") not in ("private", ""):
        await update.message.reply_text(
            "🔒 For security, please connect your Microsoft account in a private chat with the bot."
        )
        return

    user_id = user.get("user_id")

    ms = get_user_microsoft(user_id)
    if ms is not None and ms.get("connected"):
        await update.message.reply_text(
            "Your Microsoft account is already connected.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Connection Status", callback_data=STATUS_SHOW),
                        InlineKeyboardButton("Disconnect", callback_data=DISCONNECT_START),
                    ]
                ]
            ),
        )
        return

    # Cancel previous pending flow for this user if one exists
    bot_data = getattr(context.application, "bot_data", None) if context.application else None
    if bot_data is not None:
        active_flows = bot_data.setdefault("active_device_flows", {})
        prev_entry = active_flows.get(user_id)
        if prev_entry:
            prev_task, prev_flow = prev_entry
            if isinstance(prev_flow, dict):
                prev_flow["expires_at"] = 0
            if hasattr(prev_task, "cancel") and not prev_task.done():
                prev_task.cancel()

    try:
        flow, token_cache, client = await asyncio.to_thread(
            initiate_device_flow_for_user,
            user_id,
        )
    except DeviceFlowError as exc:
        logger.warning("Device flow initiation failed for %s: %s", user_id, exc)
        await update.message.reply_text(
            "⚠️ Microsoft connection is not available right now. "
            "Please try again later."
        )
        return
    except Exception as exc:
        logger.warning("Unexpected error during device flow initiation: %s", exc)
        await update.message.reply_text(
            "⚠️ Microsoft connection is not available right now. "
            "Please try again later."
        )
        return

    user_code = flow.get("user_code", "")
    verification_uri = flow.get("verification_uri") or "https://microsoft.com/devicelogin"
    verification_url = flow.get("verification_uri_complete") or verification_uri
    expires_in = flow.get("expires_in", 900)
    expires_minutes = max(1, expires_in // 60)

    msg_text = (
        "Connect your Microsoft account to enable OneNote saving.\n\n"
        f"1. Open: {verification_uri}\n"
        f"2. Enter code: {user_code}\n\n"
        f"This code expires in {expires_minutes} minutes.\n"
        "ReelForge will automatically detect when you approve."
    )

    sent_msg = await update.message.reply_text(
        msg_text,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Open Microsoft Login", url=verification_url)],
                [InlineKeyboardButton("Cancel", callback_data=f"{DEVICE_CANCEL_PREFIX}{user_id}")],
            ]
        ),
    )

    chat_id = chat.id if chat else (update.message.chat_id if update.message else None)
    status_msg_id = getattr(sent_msg, "message_id", None)

    polling_task = asyncio.create_task(
        _poll_device_flow_task(
            bot=context.bot,
            client=client,
            cache=token_cache,
            flow=flow,
            user_id=user_id,
            chat_id=chat_id,
            status_message_id=status_msg_id,
            bot_data=bot_data,
        )
    )

    if bot_data is not None:
        active_flows[user_id] = (polling_task, flow)


async def mestatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    if user is None:
        await update.message.reply_text(
            "⚠️ Could not resolve your account. Please try /start first."
        )
        return

    await update.message.reply_text(_microsoft_status_text(user.get("user_id")))


async def disconnect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    if user is None:
        await update.message.reply_text(
            "⚠️ Could not resolve your account. Please try /start first."
        )
        return

    user_id = user.get("user_id")
    ms = get_user_microsoft(user_id)
    if ms is None or not ms.get("connected"):
        await update.message.reply_text(
            "Microsoft account is not connected.\n\n"
            "Use /connect to connect your Microsoft account."
        )
        return

    await update.message.reply_text(
        "Disconnect your Microsoft account?\n\n"
        "This will stop ReelForge from saving reels to your OneNote account.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Disconnect", callback_data=DISCONNECT_CONFIRM),
                    InlineKeyboardButton("Cancel", callback_data=DISCONNECT_CANCEL),
                ]
            ]
        ),
    )


async def connection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Microsoft connection inline buttons.

    The acting user is always derived from the authenticated Telegram update;
    callback_data never carries (and never trusts) a user_id.
    """
    query = getattr(update, "callback_query", None)
    if query is None:
        return

    await query.answer()

    user, _ = _resolve_user(update)
    if user is None:
        await query.edit_message_text(
            "⚠️ Could not resolve your account. Please try /start first."
        )
        return

    user_id = user.get("user_id")
    data = getattr(query, "data", "") or ""

    if data.startswith(DEVICE_CANCEL_PREFIX):
        target_user_id = data[len(DEVICE_CANCEL_PREFIX):]
        if target_user_id and target_user_id != user_id:
            await query.answer("⚠️ You cannot cancel another user's login.", show_alert=True)
            return

        bot_data = getattr(context.application, "bot_data", {}) if context.application else {}
        active_flows = bot_data.get("active_device_flows", {}) if isinstance(bot_data, dict) else {}
        entry = active_flows.pop(user_id, None)
        if entry:
            task, flow = entry
            if isinstance(flow, dict):
                flow["expires_at"] = 0
            if hasattr(task, "cancel") and not task.done():
                task.cancel()

        await query.edit_message_text(
            "❌ Microsoft login cancelled.\n\n"
            "Use /connect whenever you want to connect your account."
        )
        return

    if data == STATUS_SHOW:
        await query.edit_message_text(_microsoft_status_text(user_id))
        return

    if data == DISCONNECT_START:
        await query.edit_message_text(
            "Disconnect your Microsoft account?\n\n"
            "This will stop ReelForge from saving reels to your OneNote account.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Disconnect", callback_data=DISCONNECT_CONFIRM),
                        InlineKeyboardButton("Cancel", callback_data=DISCONNECT_CANCEL),
                    ]
                ]
            ),
        )
        return

    if data == DISCONNECT_CONFIRM:
        ms = get_user_microsoft(user_id)
        if ms is None or not ms.get("connected"):
            await query.edit_message_text(
                "Your Microsoft account is not connected.\n\n"
                "Use /connect to connect your Microsoft account."
            )
            return

        disconnect_user_microsoft(user_id)
        await query.edit_message_text(
            "Microsoft account disconnected.\n\n"
            "Use /connect whenever you want to reconnect."
        )
        return

    if data == DISCONNECT_CANCEL:
        await query.edit_message_text(
            "Cancelled. Your Microsoft connection remains untouched."
        )
        return


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    stats = get_knowledge_stats(user_id=user_id)
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
# Job Status / Lifecycle Observability (V3.5 Layer 3)
# ---------------------------------------------------------------------------

_JOB_STATUS_ICONS = {
    "queued": "⏳",
    "processing": "⚙️",
    "completed": "✅",
    "failed": "❌",
}


def _format_job_ts(value) -> str:
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    return _truncate(str(value), 40)


def _format_job_status(job: dict) -> str:
    """Concise, user-safe job status card (never raw Mongo, tokens, or other
    users' data)."""
    ts = job.get("timestamps") or {}
    status = job.get("status") or "unknown"
    icon = _JOB_STATUS_ICONS.get(status, "•")

    lines = ["📋 Job Status", ""]
    lines.append(f"Job: `{job.get('_id', '?')}`")
    reel_id = job.get("reel_id")
    if reel_id:
        lines.append(f"Reel: `{reel_id}`")
    lines.append(f"Status: {icon} {status}")
    lines.append(f"Type: {job.get('job_type') or 'reel'}")
    attempts = job.get("attempts") or 0
    max_attempts = job.get("max_attempts") or 3
    lines.append(f"Attempts: {attempts} / {max_attempts}")
    if job.get("recovered"):
        lines.append("Recovered after worker interruption")

    if ts.get("created_at"):
        lines.append(f"Created: {_format_job_ts(ts['created_at'])}")
    if ts.get("started_at"):
        lines.append(f"Started: {_format_job_ts(ts['started_at'])}")
    if ts.get("finished_at"):
        lines.append(f"Finished: {_format_job_ts(ts['finished_at'])}")

    if status == "processing":
        lease = job.get("lease") or {}
        if lease.get("expires_at"):
            lines.append(f"Lease expires: {_format_job_ts(lease['expires_at'])}")

    if status == "completed":
        result = job.get("result") or {}
        bits = []
        if result.get("category"):
            bits.append(f"Category: {_truncate(str(result['category']), 40)}")
        onenote = result.get("onenote_success")
        if onenote is not None:
            if onenote:
                bits.append("OneNote: published")
            else:
                bits.append("OneNote: not published")
        if bits:
            lines.append("Result: " + " | ".join(bits))

    if status == "failed" and job.get("error"):
        lines.append("")
        lines.append(f"Error: {_truncate(job.get('error'), 220)}")
    return "\n".join(lines)


async def job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")

    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a job ID.\n\n"
            "Example: /job job_abc123\n\n"
            "💡 Use /jobs to see your recent jobs."
        )
        return

    if not user_id:
        await update.message.reply_text(
            "⚠️ Could not resolve your account. Please try /start first."
        )
        return

    job_id = context.args[0].strip()
    job = get_job_for_user(job_id, user_id)
    if job is None:
        await update.message.reply_text(
            f"❌ Job `{job_id}` not found or you are not authorized to view it.\n\n"
            "Jobs are private — only the requesting user can inspect a job."
        )
        return

    await update.message.reply_text(_format_job_status(job))


async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")

    if not user_id:
        await update.message.reply_text(
            "⚠️ Could not resolve your account. Please try /start first."
        )
        return

    limit = 5
    if context.args:
        try:
            limit = int(context.args[0])
        except (ValueError, TypeError):
            limit = 5
    limit = max(1, min(limit, 10))

    jobs = list_jobs_for_user(user_id, limit=limit)
    if not jobs:
        await update.message.reply_text(
            "📋 You have no recent jobs yet. Send a Reel URL to get started!"
        )
        return

    lines = ["📋 Recent Jobs:", ""]
    for job in jobs:
        status = job.get("status") or "unknown"
        icon = _JOB_STATUS_ICONS.get(status, "•")
        reel = job.get("reel_id") or "?"
        ts = (job.get("timestamps") or {}).get("created_at")
        created = _format_job_ts(ts) if ts else "?"
        lines.append(
            f"• `{job.get('_id', '?')}` — {icon} {status} — "
            f"reel `{reel}` — {created}"
        )
    lines.append("")
    lines.append("💡 Use /job <job_id> for full details.")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Ingestion Flow (Writes & Ingestion Lock)
# ---------------------------------------------------------------------------

_PROCESS_LOCK = asyncio.Lock()


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    message = update.message.text.strip()

    if not is_valid_instagram_url(message):
        await update.message.reply_text(
            "Please send a valid Instagram Reel link, or type /help for discovery commands."
        )
        return

    chat = getattr(update, "effective_chat", None)
    chat_id = getattr(chat, "id", None) or getattr(update.message, "chat_id", None)

    # Instant cache-hit fast path (preserves today's behavior for repeat Reels).
    candidate_id = extract_reel_id_from_url(message)
    if candidate_id:
        cached_brain = get_cached_brain_object(candidate_id)
        if cached_brain is not None:
            if user_id:
                try:
                    associate_user_with_brain(candidate_id, user_id)
                except Exception as ae:
                    print(f"Notice: Could not associate user with cached brain: {ae}")

            result = {
                "success": True,
                "cached": True,
                "onenote_success": True,
                "onenote_error": None,
                "brain": cached_brain,
                "brain_path": str(Path(BRAINS_DIR) / f"{candidate_id}.json"),
                "transcript": cached_brain.get("content", {}).get("transcript"),
                "category": cached_brain.get("knowledge", {}).get("category"),
                "knowledge": cached_brain.get("knowledge"),
                "error": None,
            }
            await update.message.reply_text(_result_summary_text(result, "cached"))
            brain_path = result["brain_path"]
            if Path(brain_path).exists():
                with open(brain_path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=Path(brain_path).name,
                    )
            return

    job, duplicate = submit_job(
        claim_key=candidate_id or message,
        reel_url=message,
        user_id=user_id,
        force=False,
        reel_id=candidate_id,
        chat_id=chat_id,
        job_type="reel",
    )

    if duplicate:
        await update.message.reply_text(
            "📥 This Reel is already being processed.\n"
            f"Job: `{job['_id']}` — you'll receive the result here when it finishes."
        )
    else:
        await update.message.reply_text(
            "📥 Reel received. Queued for processing.\n"
            f"Job: `{job['_id']}`"
        )


async def force_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
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

    chat = getattr(update, "effective_chat", None)
    chat_id = getattr(chat, "id", None) or getattr(update.message, "chat_id", None)
    reel_id = extract_reel_id_from_url(url)

    job, duplicate = submit_job(
        claim_key=reel_id or url,
        reel_url=url,
        user_id=user_id,
        force=True,
        reel_id=reel_id,
        chat_id=chat_id,
        job_type="force",
    )

    if duplicate:
        await update.message.reply_text(
            "⚡ Force ingestion requested — a job for this Reel is already running.\n"
            f"Job: `{job['_id']}`"
        )
    else:
        await update.message.reply_text(
            "⚡ Force ingestion requested. Queued, bypassing cache...\n"
            f"Job: `{job['_id']}`"
        )


async def reprocess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID to reprocess.\n\n"
            "Example: /reprocess DcK7QPXuRBJ\n\n"
            "💡 Use /recent or /search to find Reel IDs."
        )
        return

    reel_id = context.args[0].strip()

    try:
        brain = load_brain_object(reel_id, user_id=user_id)
    except PermissionError:
        await update.message.reply_text(
            f"❌ Reel '{reel_id}' is not in your saved library.\n\n"
            "💡 Use /recent or /search to find your saved Reels."
        )
        return
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

    chat = getattr(update, "effective_chat", None)
    chat_id = getattr(chat, "id", None) or getattr(update.message, "chat_id", None)

    job, duplicate = submit_job(
        claim_key=reel_id,
        reel_url=source_url,
        user_id=user_id,
        force=True,
        reel_id=reel_id,
        chat_id=chat_id,
        job_type="reprocess",
    )

    if duplicate:
        await update.message.reply_text(
            f"🔄 Reprocessing Reel `{reel_id}` — a job is already running.\n"
            f"Job: `{job['_id']}`"
        )
    else:
        await update.message.reply_text(
            f"🔄 Reprocessing Reel `{reel_id}` queued.\n"
            f"🔗 Source: {source_url}\n"
            f"Job: `{job['_id']}`\n"
            "Re-running Whisper, Vision, and LLM extraction..."
        )


async def publish_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retry this user's OneNote publication without rerunning extraction."""
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not user_id:
        await update.message.reply_text("Please use /start before requesting a publication retry.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /publish <reel_id>")
        return

    reel_id = context.args[0].strip()
    try:
        brain = load_brain_object(reel_id, user_id=user_id)
    except PermissionError:
        await update.message.reply_text("That Reel is not in your saved library.")
        return
    except FileNotFoundError:
        await update.message.reply_text("Reel not found in your saved library.")
        return

    publication = get_publication(reel_id, user_id=user_id)
    if publication and publication.get("state") == "complete":
        await update.message.reply_text("Your OneNote page is already published.")
        return

    chat = getattr(update, "effective_chat", None)
    chat_id = getattr(chat, "id", None) or getattr(update.message, "chat_id", None)
    source_url = (brain.get("source") or {}).get("url") or ""
    job, duplicate = submit_job(
        claim_key=f"publication:{reel_id}:{user_id}",
        reel_url=source_url,
        user_id=user_id,
        reel_id=reel_id,
        chat_id=chat_id,
        job_type=JOB_TYPE_PUBLICATION,
    )
    if duplicate:
        await update.message.reply_text(
            f"Your OneNote publication retry is already queued. Job: `{job['_id']}`"
        )
    else:
        await update.message.reply_text(
            f"OneNote publication retry queued without reprocessing the Reel. Job: `{job['_id']}`"
        )


async def archive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID to archive.\n\n"
            "Example: /archive DcK7QPXuRBJ\n\n"
            "💡 Use /recent or /search to find Reel IDs."
        )
        return

    reel_id = context.args[0].strip()
    async with _PROCESS_LOCK:
        success, msg = archive_brain_object(reel_id, user_id=user_id)

    if success:
        await update.message.reply_text(
            f"📦 Reel `{reel_id}` archived successfully.\n\n"
            "It will no longer appear in /recent, /search, /category, or /stats.\n"
            f"💡 To restore it later: /restore {reel_id}"
        )
    else:
        await update.message.reply_text(f"❌ {msg}")


async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID to restore.\n\n"
            "Example: /restore DcK7QPXuRBJ"
        )
        return

    reel_id = context.args[0].strip()
    async with _PROCESS_LOCK:
        success, msg = restore_brain_object(reel_id, user_id=user_id)

    if success:
        await update.message.reply_text(
            f"♻️ Reel `{reel_id}` restored successfully.\n\n"
            "It is now active and will appear in discovery commands and cache checks.\n"
            f"👉 View details: /get {reel_id}"
        )
    else:
        await update.message.reply_text(f"❌ {msg}")


async def recat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
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
        success, msg, updated_brain = recategorize_brain_object(reel_id, new_category, user_id=user_id)

    if success and updated_brain:
        cat = updated_brain.get("knowledge", {}).get("category")
        await update.message.reply_text(
            f"🏷️ Category updated to '{cat}' for Reel `{reel_id}`.\n\n"
            f"💡 Note: Knowledge metadata and schema have been updated locally. Use /reprocess {reel_id} if you want full multimodal knowledge re-extracted under the {cat} category."
        )
    else:
        await update.message.reply_text(f"❌ {msg}")


async def related_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a Reel ID.\n\n"
            "Example: /related DcK7QPXuRBJ\n\n"
            "💡 Use /recent or /search to find Reel IDs."
        )
        return

    target_id = context.args[0].strip()
    brain = get_cached_brain_object(target_id, user_id=user_id)
    if not brain:
        matched = [
            b for b in scan_valid_brain_objects(user_id=user_id)
            if b.get("id") == target_id or (b.get("source") or {}).get("shortcode") == target_id
        ]
        if matched:
            brain = matched[0]

    if not brain:
        library_label = "your library" if user_id else "active library"
        await update.message.reply_text(
            f"❌ Reel '{target_id}' not found in {library_label}.\n\n"
            "💡 Check the Reel ID with /recent or /search."
        )
        return

    reel_id = brain.get("id") or target_id
    related_matches = find_related_brain_objects(reel_id, limit=5, user_id=user_id)

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
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    limit = 15
    if context.args:
        try:
            limit = int(context.args[0])
        except (ValueError, TypeError):
            limit = 15
    clamped_limit = max(1, min(limit, 50))

    topics = get_all_topics(user_id=user_id)
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
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
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
    brains = get_brain_objects_by_topic(topic_name, limit=10, user_id=user_id)

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
    user, _ = _resolve_user(update)
    user_id = (user or {}).get("user_id")
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please specify a creator username.\n\n"
            "Example: /creator Noor\n\n"
            "💡 You can omit the leading '@'."
        )
        return

    creator_name = " ".join(context.args).strip()
    clean_name = creator_name.lstrip("@")
    brains = get_brain_objects_by_creator(creator_name, limit=10, user_id=user_id)

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
    async def _start_job_worker(_app):
        validate_worker_runtime()
        loop = asyncio.get_running_loop()

        def make_notifier(job):
            kind = notifier_kind_for_job(job)
            return _JobNotifier(
                bot=_app.bot,
                loop=loop,
                chats=job.get("chats") or [],
                kind=kind,
            )

        task = loop.create_task(
            worker_loop(
                make_notifier=make_notifier,
                worker_id=f"bot-{os.getpid()}",
                poll_interval=2.0,
            )
        )
        _app.bot_data["job_worker_task"] = task
        return _app

    async def _stop_job_worker(_app):
        task = _app.bot_data.get("job_worker_task") if hasattr(_app, "bot_data") else None
        if task is not None:
            task.cancel()
            if isinstance(task, (asyncio.Task, asyncio.Future)):
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _start_oauth_server_task(_app):
        import config
        secret = getattr(config, "OAUTH_STATE_SECRET", "").strip()
        if not secret:
            print(
                "OAuth callback server: disabled (OAUTH_STATE_SECRET not configured). "
                "Set OAUTH_STATE_SECRET to enable Microsoft /connect callback."
            )
            return

        try:
            server = start_oauth_server()
            _app.bot_data["oauth_server"] = server
            print(
                f"OAuth callback server: listening on http://{server.host}:{server.port}{server.path}"
            )
        except Exception as e:
            print(f"Warning: Could not start OAuth callback server: {e}")

    async def _stop_oauth_server_task(_app):
        try:
            stop_oauth_server()
            if hasattr(_app, "bot_data"):
                _app.bot_data.pop("oauth_server", None)
            print("OAuth callback server: stopped.")
        except Exception as e:
            print(f"Notice: error stopping OAuth callback server: {e}")

    post_init_hooks = []
    post_shutdown_hooks = []

    if REELFORGE_EMBEDDED_WORKER:
        post_init_hooks.append(_start_job_worker)
        post_shutdown_hooks.append(_stop_job_worker)
        print("Embedded worker: ON (single-process mode). Set REELFORGE_EMBEDDED_WORKER=0 to run a standalone worker instead.")
    else:
        print("Embedded worker: OFF (standalone mode). Job processing handled by `python -m processing.worker`.")

    if REELFORGE_START_OAUTH_SERVER:
        post_init_hooks.append(_start_oauth_server_task)
        post_shutdown_hooks.append(_stop_oauth_server_task)
        print("Embedded OAuth server: ON (single-process mode). Set REELFORGE_START_OAUTH_SERVER=0 to run standalone.")
    else:
        print("Embedded OAuth server: OFF (standalone mode). Run via `python -m auth.oauth_server`.")

    async def _stop_device_flows_task(_app):
        stop_active_device_flows(_app)

    post_shutdown_hooks.append(_stop_device_flows_task)

    builder = ApplicationBuilder().token(BOT_TOKEN)

    if post_init_hooks:
        async def _on_post_init(app_instance):
            for hook in post_init_hooks:
                await hook(app_instance)

        async def _on_post_shutdown(app_instance):
            for hook in post_shutdown_hooks:
                await hook(app_instance)

        builder = builder.post_init(_on_post_init).post_shutdown(_on_post_shutdown)

    app = builder.build()

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
    app.add_handler(CommandHandler("publish", publish_command))
    app.add_handler(CommandHandler("job", job_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("archive", archive_command))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(CommandHandler("recat", recat_command))
    app.add_handler(CommandHandler("connect", connect_command))
    app.add_handler(CommandHandler("mestatus", mestatus_command))
    app.add_handler(CommandHandler("disconnect", disconnect_command))
    app.add_handler(
        CallbackQueryHandler(
            connection_callback,
            pattern=r"^(disconnect:|status:|device_cancel:)",
        )
    )

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
