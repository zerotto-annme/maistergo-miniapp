import os
import logging
from pathlib import Path
from urllib import parse, request

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "") or os.getenv("MINI_APP_URL", "")
BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_URL", "http://127.0.0.1:8000")
LOGO_PATH = Path(__file__).resolve().parent / "static" / "logo-new.png"


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


async def ensure_menu_button(application: Application) -> None:
    if not WEBAPP_URL:
        return
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="МайстерGO", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except Exception:
        logger.exception("Failed to set persistent menu button")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    # Clear any previous session/FSM-like in-memory state on /start.
    context.user_data.clear()
    context.chat_data.clear()

    if update.effective_user and update.effective_chat:
        link_chat(update.effective_user.id, update.effective_chat.id)

    if not WEBAPP_URL:
        await update.message.reply_text(
            "WEBAPP_URL не задано у .env. Додайте URL міні-додатку та перезапустіть бота."
        )
        return

    logger.info(
        "start telegram_id=%s chat_id=%s",
        (update.effective_user.id if update.effective_user else None),
        (update.effective_chat.id if update.effective_chat else None),
    )

    try:
        with LOGO_PATH.open("rb") as image_file:
            await update.message.reply_photo(photo=image_file)
    except FileNotFoundError:
        logger.error("Logo file not found: %s", LOGO_PATH)
    except Exception:
        logger.exception("Failed to send logo image")

    inline_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="Відкрити МайстерGO", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(text="МайстерGO", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
        is_persistent=True,
    )

    await update.message.reply_text(
        "Знайди майстра за кілька кліків 👇",
        reply_markup=inline_keyboard,
    )
    await update.message.reply_text(
        "Кнопка МайстерGO закріплена внизу чату.",
        reply_markup=keyboard,
    )
    await ensure_menu_button(context.application)


async def post_init(application: Application) -> None:
    await ensure_menu_button(application)


def run() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задано у .env")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    run()
