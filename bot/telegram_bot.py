import asyncio

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
    title = caption.splitlines()[0] if caption else source.get("shortcode", "Untitled")
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
        f"Main Topic:\n{category}\n\n"
        f"Key Takeaways:\n{_list_items(key_takeaways)}\n\n"
        f"Resources:\n{_list_items(resources)}\n\n"
        f"Action Items:\n{_list_items(action_items)}"
    )


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.strip()

    print("\n==============================")
    print("NEW MESSAGE RECEIVED")
    print("==============================")
    print(f"Message: {message}")

    if "instagram.com" not in message.lower():
        print("Not an Instagram Reel")

        await update.message.reply_text(
            "Please send a valid Instagram Reel link."
        )
        return

    print("Instagram Reel Detected")

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

    result = await asyncio.to_thread(process_reel, message, progress)

    if not result["success"]:
        await update.message.reply_text(
            f"Reel processing failed: {result['error']}"
        )
        return

    await update.message.reply_text(_format_summary(result))

    brain_path = result.get("brain_path")

    if brain_path is not None:
        with open(brain_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=brain_path.name
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
