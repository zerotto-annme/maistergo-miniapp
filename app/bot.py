import os
from urllib import parse, request

from dotenv import load_dotenv
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MINI_APP_URL = os.getenv("MINI_APP_URL", "")
BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_URL", "http://127.0.0.1:8000")


def link_chat(telegram_id: int, chat_id: int) -> None:
    payload = parse.urlencode({"telegram_id": str(telegram_id), "chat_id": str(chat_id)}).encode()
    try:
        request.urlopen(
            f"{BACKEND_INTERNAL_URL}/api/telegram/link",
            data=payload,
            timeout=5,
        ).read()
    except Exception:
        return


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if update.effective_user and update.effective_chat:
        link_chat(update.effective_user.id, update.effective_chat.id)

    if not MINI_APP_URL:
        await update.message.reply_text(
            "MINI_APP_URL не задано у .env. Додайте URL міні-додатку та перезапустіть бота."
        )
        return

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(text="МайстерGO", web_app=WebAppInfo(url=MINI_APP_URL))]],
        resize_keyboard=True,
        is_persistent=True,
    )

    await update.message.reply_text(
        "Натисніть кнопку нижче, щоб відкрити МайстерGO:",
        reply_markup=keyboard,
    )


def run() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задано у .env")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    run()
