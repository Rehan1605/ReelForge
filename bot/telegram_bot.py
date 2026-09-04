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

from config import BOT_TOKEN
from processing.pipeline import process_reel


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("InstaBrain is online.")


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


_PROCESS_LOCK = asyncio.Lock()


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.strip()

    if "instagram.com" not in message.lower():
        await update.message.reply_text(
            "Please send a valid Instagram Reel link."
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


def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

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
