# Telegram Mini App (Python MVP)

MVP маркетплейсу послуг у стилі kabanchik:
- стартова реєстрація ролі: клієнт або виконавець
- виконавець обирає категорії, в яких працює
- клієнт створює заявку та додає до 10 фото
- виконавець бачить заявки лише зі своїх категорій і надсилає ціну + коментар
- клієнт бачить відгуки на власні заявки

## Стек
- FastAPI
- SQLAlchemy + SQLite
- Vanilla JS frontend (внутри Telegram WebApp)

## Быстрый запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Відкрий: `http://127.0.0.1:8000`

## Telegram інтеграція

1. Створи бота через BotFather.
2. Вкажи Web App URL (домен з HTTPS).
3. В `.env` заповни `TELEGRAM_BOT_TOKEN`.
4. Для локальної розробки залиш `DEV_BYPASS_AUTH=true`.

## /start -> кнопка відкриття Mini App

1. У `.env` додай:
   - `TELEGRAM_BOT_TOKEN=...`
   - `WEBAPP_URL=https://your-public-url`
2. Запусти бота:

```bash
source .venv/bin/activate
python3 app/bot.py
```

Після цього команда `/start` у боті надсилає кнопку `МайстерGO`, яка відкриває Mini App.

Після `/start` бот також прив'язує `chat_id` користувача до його `telegram_id` у базі.
Це потрібно для сповіщень майстрам про нові заявки у їхніх категоріях.

У production:
- постав `DEV_BYPASS_AUTH=false`
- передавай заголовки `X-Telegram-Init-Data` і `X-Telegram-User`
- backend валідує підпис initData

## Локальний тест двох ролей

У UI є блок `Dev режим` з полем `Dev User ID`:
- постав `1001` -> зареєструй як клієнт, створи заявку
- постав `2002` -> зареєструй як виконавець, обери категорію заявки, надішли відгук

## Що далі для версії "як kabanchik"
- чат між клієнтом і виконавцем
- відгуки/рейтинг виконавців
- безпечна оплата та escrow
- модерація і антиспам
