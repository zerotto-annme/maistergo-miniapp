import json
import logging
import os
import re
from pathlib import Path
from urllib import request, parse
from urllib.parse import parse_qsl
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, func
from sqlalchemy.orm import Session

from .auth import validate_telegram_init_data
from .database import Base, engine, get_db
from .models import User, Task, Bid, Review
from .schemas import (
    TaskOut,
    BidCreate,
    BidOut,
    UserOut,
    PerformerProfileOut,
    ReviewCreate,
    ReviewOut,
    CabinetTaskOut,
)

load_dotenv()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEV_BYPASS_AUTH = os.getenv("DEV_BYPASS_AUTH", "true").lower() == "true"
ALLOWED_SERVICE_CATEGORIES = [
    "Сантехника",
    "Обои",
    "Электрика",
    "Плиточные работы",
    "Малярные работы",
    "Гипсокартонные работы",
    "Двери и окна",
    "Потолок",
]

app = FastAPI(title="Telegram Mini App - Services Marketplace")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "static" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_legacy_columns()


def ensure_legacy_columns() -> None:
    with engine.begin() as conn:
        user_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        if "full_name" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR(255)"))
        if "phone" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(64)"))
        if "city" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN city VARCHAR(120)"))
        if "address" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN address VARCHAR(255)"))
        if "profile_photo_url" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN profile_photo_url VARCHAR(255)"))
        if "telegram_chat_id" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN telegram_chat_id INTEGER"))
        if "is_client_registered" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_client_registered INTEGER DEFAULT 0"))
        if "is_performer_registered" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_performer_registered INTEGER DEFAULT 0"))
        if "role" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(32)"))
        if "performer_categories_json" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN performer_categories_json TEXT DEFAULT '[]'"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_telegram_id ON users(telegram_id)"))

        task_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tasks)"))}
        if "photos_json" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN photos_json TEXT DEFAULT '[]'"))

        bid_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(bids)"))}
        if "status" not in bid_cols:
            conn.execute(text("ALTER TABLE bids ADD COLUMN status VARCHAR(32) DEFAULT 'pending'"))

        # Backfill legacy users created before split cabinets logic.
        conn.execute(
            text(
                "UPDATE users SET is_client_registered=1 "
                "WHERE role='client' AND (is_client_registered IS NULL OR is_client_registered=0)"
            )
        )
        conn.execute(
            text(
                "UPDATE users SET is_performer_registered=1 "
                "WHERE role='performer' AND (is_performer_registered IS NULL OR is_performer_registered=0)"
            )
        )


def user_to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=user.full_name,
        phone=user.phone,
        city=user.city,
        address=user.address,
        profile_photo_url=user.profile_photo_url,
        telegram_chat_id=user.telegram_chat_id,
        is_client_registered=bool(user.is_client_registered),
        is_performer_registered=bool(user.is_performer_registered),
        role=user.role,
        performer_categories=user.performer_categories,
        created_at=user.created_at,
    )


def task_to_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        title=task.title,
        description=task.description,
        category=task.category,
        city=task.city,
        budget=task.budget,
        photos=task.photos,
        status=task.status,
        client_id=task.client_id,
        created_at=task.created_at,
    )


def get_performer_stats(db: Session, performer_id: int) -> tuple[float, int]:
    avg_rating = (
        db.query(func.avg(Review.rating)).filter(Review.performer_id == performer_id).scalar() or 0
    )
    completed_jobs = (
        db.query(func.count(Bid.id))
        .filter(Bid.performer_id == performer_id, Bid.status == "completed")
        .scalar()
        or 0
    )
    return float(round(avg_rating, 2)), int(completed_jobs)


def bid_to_out(db: Session, bid: Bid) -> BidOut:
    performer = db.query(User).filter(User.id == bid.performer_id).first()
    rating, completed_jobs = get_performer_stats(db, bid.performer_id)
    has_review = db.query(Review).filter(Review.bid_id == bid.id).first() is not None
    return BidOut(
        id=bid.id,
        task_id=bid.task_id,
        performer_id=bid.performer_id,
        price=bid.price,
        message=bid.message,
        status=bid.status,
        performer_name=(performer.full_name if performer else None),
        performer_photo_url=(performer.profile_photo_url if performer else None),
        performer_rating=rating,
        performer_completed_jobs=completed_jobs,
        has_review=has_review,
        created_at=bid.created_at,
    )


def extract_telegram_user_payload(
    telegram_user_json: str | None,
    telegram_init_data: str | None,
) -> dict | None:
    if telegram_user_json:
        try:
            payload = json.loads(telegram_user_json)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    if telegram_init_data:
        try:
            pairs = dict(parse_qsl(telegram_init_data, strict_parsing=False))
            raw_user = pairs.get("user")
            if raw_user:
                payload = json.loads(raw_user)
                if isinstance(payload, dict):
                    return payload
        except (ValueError, json.JSONDecodeError):
            pass
    return None


def get_or_create_user(
    db: Session,
    telegram_user_json: str | None,
    telegram_init_data: str | None,
    x_dev_user_id: str | None,
) -> User:
    telegram_payload = extract_telegram_user_payload(telegram_user_json, telegram_init_data)
    if telegram_payload:
        user_payload = telegram_payload
        telegram_id = int(user_payload.get("id", 0))
        if telegram_id <= 0:
            raise HTTPException(status_code=401, detail="Некоректний користувач Telegram")
    elif DEV_BYPASS_AUTH:
        telegram_id = int(x_dev_user_id) if x_dev_user_id else 999000
        user_payload = {
            "username": f"dev_{telegram_id}",
            "first_name": "Dev",
            "last_name": str(telegram_id),
        }
    else:
        raise HTTPException(status_code=401, detail="Відсутній користувач Telegram")

    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        logger.info(
            "auth_lookup telegram_id=%s found=true client_registered=%s performer_registered=%s role=%s",
            telegram_id,
            bool(user.is_client_registered),
            bool(user.is_performer_registered),
            user.role,
        )
        # Keep Telegram identity data fresh without forcing manual re-registration.
        user.username = user_payload.get("username") or user.username
        user.first_name = user_payload.get("first_name") or user.first_name
        user.last_name = user_payload.get("last_name") or user.last_name
        if not user.full_name:
            composed = " ".join(
                x for x in [user_payload.get("first_name", ""), user_payload.get("last_name", "")] if x
            ).strip()
            if composed:
                user.full_name = composed
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    default_full_name = " ".join(
        x for x in [user_payload.get("first_name", ""), user_payload.get("last_name", "")] if x
    ).strip()
    user = User(
        telegram_id=telegram_id,
        username=user_payload.get("username"),
        first_name=user_payload.get("first_name"),
        last_name=user_payload.get("last_name"),
        full_name=default_full_name or None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("auth_lookup telegram_id=%s found=false created=true", telegram_id)
    return user


def authorize(
    db: Session,
    x_telegram_init_data: str | None,
    x_telegram_user: str | None,
    x_dev_user_id: str | None,
) -> User:
    if x_telegram_init_data and BOT_TOKEN:
        is_valid = validate_telegram_init_data(x_telegram_init_data, BOT_TOKEN)
        if not is_valid:
            logger.warning("telegram_init_data_validation failed")
            raise HTTPException(status_code=401, detail="Некоректна авторизація Telegram")
        logger.info("telegram_init_data_validation ok")
        return get_or_create_user(db, x_telegram_user, x_telegram_init_data, x_dev_user_id)

    if DEV_BYPASS_AUTH:
        return get_or_create_user(db, x_telegram_user, x_telegram_init_data, x_dev_user_id)

    raise HTTPException(status_code=401, detail="Відсутні заголовки авторизації Telegram")


def require_registered(user: User) -> None:
    if not bool(user.is_client_registered) and not bool(user.is_performer_registered):
        raise HTTPException(status_code=403, detail="Спочатку пройдіть реєстрацію")


def require_client(user: User) -> None:
    if not bool(user.is_client_registered):
        raise HTTPException(status_code=403, detail="Кабінет клієнта не зареєстрований")


def require_performer(user: User) -> None:
    if not bool(user.is_performer_registered):
        raise HTTPException(status_code=403, detail="Кабінет майстра не зареєстрований")


def resolve_mode(user: User, mode: str | None) -> str:
    mode_clean = (mode or "").strip().lower()
    if mode_clean in {"client", "performer"}:
        return mode_clean
    if user.role in {"client", "performer"}:
        return str(user.role)
    if bool(user.is_client_registered):
        return "client"
    if bool(user.is_performer_registered):
        return "performer"
    return "client"


def notify_performer_about_task(chat_id: int, task: Task) -> None:
    if not BOT_TOKEN:
        return
    text_msg = (
        f"Нова заявка у вашій категорії:\\n"
        f"{task.title}\\n"
        f"{task.category} • {task.city} • {task.budget} грн"
    )
    payload = parse.urlencode({"chat_id": str(chat_id), "text": text_msg}).encode()
    try:
        request.urlopen(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=payload,
            timeout=5,
        ).read()
    except Exception:
        # Non-blocking best-effort notification.
        return


def notify_client_about_new_bid(chat_id: int, task: Task) -> None:
    if not BOT_TOKEN:
        return
    text_msg = "Є новий відгук на вашу заявку, перевірте будь ласка!"
    payload = parse.urlencode({"chat_id": str(chat_id), "text": text_msg}).encode()
    try:
        request.urlopen(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=payload,
            timeout=5,
        ).read()
    except Exception:
        return


def normalize_categories(values: list[str]) -> list[str]:
    allowed_map = {x.lower(): x for x in ALLOWED_SERVICE_CATEGORIES}
    normalized: list[str] = []
    for raw in values:
        key = raw.strip().lower()
        if not key:
            continue
        if key not in allowed_map:
            raise HTTPException(status_code=422, detail=f"Недопустима категорія: {raw}")
        canonical = allowed_map[key]
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def validate_phone(phone: str) -> str:
    clean = phone.strip()
    if not re.fullmatch(r"[0-9+()\-\s]{7,64}", clean):
        raise HTTPException(status_code=422, detail="Некоректний номер телефону")
    return clean


async def save_image(upload: UploadFile) -> str:
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise HTTPException(status_code=422, detail="Можна завантажувати лише зображення")
    suffix = Path(upload.filename or "").suffix.lower() or ".jpg"
    filename = f"{uuid4().hex}{suffix}"
    target = UPLOADS_DIR / filename
    content = await upload.read()
    target.write_bytes(content)
    return f"/static/uploads/{filename}"


@app.get("/", response_class=HTMLResponse)
def web_app():
    with open(BASE_DIR / "templates" / "index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/app", response_class=HTMLResponse)
def web_app_path():
    return web_app()


@app.get("/register", response_class=HTMLResponse)
def web_register_path():
    return web_app()


@app.get("/api/service-categories", response_model=list[str])
def service_categories():
    return ALLOWED_SERVICE_CATEGORIES


@app.get("/api/me", response_model=UserOut)
def get_me(
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    logger.info(
        "get_me telegram_id=%s client_registered=%s performer_registered=%s role=%s",
        user.telegram_id,
        bool(user.is_client_registered),
        bool(user.is_performer_registered),
        user.role,
    )
    return user_to_out(user)


@app.post("/api/me/photo", response_model=UserOut)
async def update_my_photo(
    profile_photo: UploadFile = File(...),
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    user.profile_photo_url = await save_image(profile_photo)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_to_out(user)


@app.post("/api/telegram/link")
def link_telegram_chat(
    telegram_id: int = Form(...),
    chat_id: int = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id)
    user.telegram_chat_id = chat_id
    db.add(user)
    db.commit()
    return {"ok": True}


@app.post("/api/register", response_model=UserOut)
async def register(
    role: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(...),
    city: str = Form(...),
    address: str = Form(default=""),
    categories: list[str] = Form(default=[]),
    profile_photo: UploadFile | None = File(default=None),
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    role_clean = role.strip()
    if role_clean not in {"client", "performer"}:
        raise HTTPException(status_code=422, detail="Некоректна роль")

    full_name_clean = full_name.strip()
    profile_city = city.strip()
    profile_address = address.strip()
    if len(full_name_clean) < 2:
        raise HTTPException(status_code=422, detail="Вкажіть ім'я")
    if len(profile_city) < 2:
        raise HTTPException(status_code=422, detail="Вкажіть місто")
    phone_clean = validate_phone(phone)

    if role_clean == "performer":
        if user.address is None:
            user.address = ""
        normalized_categories = normalize_categories(categories)
        if not normalized_categories:
            raise HTTPException(status_code=422, detail="Оберіть хоча б одну категорію")
        user.set_performer_categories(normalized_categories)
        if profile_photo is None and not user.profile_photo_url:
            raise HTTPException(status_code=422, detail="Фото виконавця обов'язкове")
        if profile_photo is not None:
            user.profile_photo_url = await save_image(profile_photo)
        user.is_performer_registered = 1
    else:
        if len(profile_address) < 3:
            raise HTTPException(status_code=422, detail="Вкажіть адресу")
        user.is_client_registered = 1
        user.address = profile_address

    user.role = role_clean
    user.full_name = full_name_clean
    user.phone = phone_clean
    user.city = profile_city
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(
        "register_success telegram_id=%s role=%s client_registered=%s performer_registered=%s",
        user.telegram_id,
        role_clean,
        bool(user.is_client_registered),
        bool(user.is_performer_registered),
    )
    return user_to_out(user)


@app.get("/api/tasks", response_model=list[TaskOut])
def list_tasks(
    city: str | None = None,
    category: str | None = None,
    min_budget: int | None = None,
    max_budget: int | None = None,
    mode: str | None = None,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    active_mode = resolve_mode(user, mode)
    if active_mode == "client":
        require_client(user)
    else:
        require_performer(user)

    query = db.query(Task).order_by(Task.created_at.desc())
    if city:
        query = query.filter(Task.city == city)
    if category:
        normalized = normalize_categories([category])
        query = query.filter(Task.category == normalized[0])
    if min_budget is not None:
        query = query.filter(Task.budget >= min_budget)
    if max_budget is not None:
        query = query.filter(Task.budget <= max_budget)

    tasks = query.all()
    if active_mode == "client":
        return [task_to_out(t) for t in tasks if t.client_id == user.id]

    categories = set(user.performer_categories)
    my_bids = db.query(Bid).filter(Bid.performer_id == user.id).all()
    my_task_status = {b.task_id: b.status for b in my_bids}

    result: list[TaskOut] = []
    for task in tasks:
        if task.client_id == user.id:
            continue
        my_status = my_task_status.get(task.id)
        is_matching_open = task.status == "open" and task.category in categories
        is_my_active = my_status in {"accepted", "completed"}
        if is_matching_open or is_my_active:
            result.append(task_to_out(task))
    return result


@app.get("/api/cabinet/tasks", response_model=list[CabinetTaskOut])
def cabinet_tasks(
    mode: str | None = None,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    active_mode = resolve_mode(user, mode)

    if active_mode == "client":
        require_client(user)
        tasks = db.query(Task).filter(Task.client_id == user.id).order_by(Task.created_at.desc()).all()
        result: list[CabinetTaskOut] = []
        for task in tasks:
            selected_bid = (
                db.query(Bid)
                .filter(Bid.task_id == task.id, Bid.status.in_(["accepted", "completed"]))
                .order_by(Bid.created_at.desc())
                .first()
            )
            if not selected_bid:
                selected_bid = (
                    db.query(Bid)
                    .filter(Bid.task_id == task.id, Bid.status == "pending")
                    .order_by(Bid.created_at.desc())
                    .first()
                )
            performer_name = None
            selected_price = None
            bid_status = None
            if selected_bid:
                performer = db.query(User).filter(User.id == selected_bid.performer_id).first()
                performer_name = performer.full_name if performer else None
                selected_price = selected_bid.price
                bid_status = selected_bid.status

            result.append(
                CabinetTaskOut(
                    task_id=task.id,
                    title=task.title,
                    category=task.category,
                    city=task.city,
                    budget=task.budget,
                    task_status=task.status,
                    selected_performer_name=performer_name,
                    selected_price=selected_price,
                    bid_status=bid_status,
                    created_at=task.created_at,
                )
            )
        return result

    require_performer(user)
    bids = db.query(Bid).filter(Bid.performer_id == user.id).order_by(Bid.created_at.desc()).all()
    result: list[CabinetTaskOut] = []
    for bid in bids:
        task = db.query(Task).filter(Task.id == bid.task_id).first()
        if not task:
            continue
        result.append(
            CabinetTaskOut(
                task_id=task.id,
                title=task.title,
                category=task.category,
                city=task.city,
                budget=task.budget,
                task_status=task.status,
                selected_performer_name=user.full_name,
                selected_price=bid.price,
                bid_status=bid.status,
                created_at=task.created_at,
            )
        )
    return result


@app.post("/api/tasks", response_model=TaskOut)
async def create_task(
    title: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    city: str = Form(...),
    budget: int = Form(...),
    photos: list[UploadFile] = File(default=[]),
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    require_client(user)

    if len(photos) > 10:
        raise HTTPException(status_code=422, detail="Можна додати не більше 10 фото")

    title_clean = title.strip()
    desc_clean = description.strip()
    city_clean = city.strip()
    category_clean = normalize_categories([category])[0]

    if len(title_clean) < 3:
        raise HTTPException(status_code=422, detail="Назва повинна містити щонайменше 3 символи")
    if len(desc_clean) < 5:
        raise HTTPException(status_code=422, detail="Опис повинен містити щонайменше 5 символів")
    if len(city_clean) < 2:
        raise HTTPException(status_code=422, detail="Вкажіть місто")
    if budget <= 0:
        raise HTTPException(status_code=422, detail="Бюджет має бути більше нуля")

    photo_urls: list[str] = []
    for photo in photos:
        photo_urls.append(await save_image(photo))

    task = Task(
        title=title_clean,
        description=desc_clean,
        category=category_clean,
        city=city_clean,
        budget=budget,
        client_id=user.id,
        status="open",
    )
    task.set_photos(photo_urls)
    db.add(task)
    db.commit()
    db.refresh(task)

    # Notify matching performers in Telegram if they linked chat via /start.
    performers = db.query(User).all()
    for performer in performers:
        if (
            bool(performer.is_performer_registered)
            and performer.telegram_chat_id
            and task.category in performer.performer_categories
            and performer.id != user.id
        ):
            notify_performer_about_task(int(performer.telegram_chat_id), task)

    return task_to_out(task)


@app.get("/api/tasks/{task_id}/bids", response_model=list[BidOut])
def list_bids(
    task_id: int,
    mode: str | None = None,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    active_mode = resolve_mode(user, mode)

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Завдання не знайдено")

    if active_mode == "client":
        require_client(user)
        if task.client_id != user.id:
            raise HTTPException(status_code=403, detail="Немає доступу до відгуків цієї заявки")
        bids = db.query(Bid).filter(Bid.task_id == task_id).order_by(Bid.created_at.desc()).all()
        return [bid_to_out(db, b) for b in bids]

    require_performer(user)
    bids = (
        db.query(Bid)
        .filter(Bid.task_id == task_id, Bid.performer_id == user.id)
        .order_by(Bid.created_at.desc())
        .all()
    )
    return [bid_to_out(db, b) for b in bids]


@app.post("/api/tasks/{task_id}/bids", response_model=BidOut)
def create_bid(
    task_id: int,
    payload: BidCreate,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Завдання не знайдено")
    if task.status != "open":
        raise HTTPException(status_code=400, detail="Заявка вже узгоджена або завершена")

    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    require_performer(user)
    if task.client_id == user.id:
        raise HTTPException(status_code=400, detail="Власник завдання не може відгукнутися на власне завдання")

    if task.category not in user.performer_categories:
        raise HTTPException(status_code=403, detail="Ваша спеціалізація не відповідає категорії завдання")
    client = db.query(User).filter(User.id == task.client_id).first()

    existing = db.query(Bid).filter(Bid.task_id == task_id, Bid.performer_id == user.id).first()
    if existing:
        existing.price = payload.price
        existing.message = payload.message
        existing.status = "pending"
        db.add(existing)
        db.commit()
        db.refresh(existing)
        if client and client.telegram_chat_id:
            notify_client_about_new_bid(int(client.telegram_chat_id), task)
        return bid_to_out(db, existing)

    bid = Bid(task_id=task_id, performer_id=user.id, price=payload.price, message=payload.message, status="pending")
    db.add(bid)
    db.commit()
    db.refresh(bid)
    if client and client.telegram_chat_id:
        notify_client_about_new_bid(int(client.telegram_chat_id), task)
    return bid_to_out(db, bid)


@app.post("/api/bids/{bid_id}/accept", response_model=BidOut)
def accept_bid(
    bid_id: int,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    require_client(user)

    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Відгук не знайдено")

    task = db.query(Task).filter(Task.id == bid.task_id).first()
    if not task or task.client_id != user.id:
        raise HTTPException(status_code=403, detail="Немає доступу")
    if task.status == "completed":
        raise HTTPException(status_code=400, detail="Заявка вже завершена")

    all_bids = db.query(Bid).filter(Bid.task_id == task.id).all()
    for one in all_bids:
        one.status = "accepted" if one.id == bid.id else "rejected"
        db.add(one)
    task.status = "in_progress"
    db.add(task)
    db.commit()
    db.refresh(bid)
    return bid_to_out(db, bid)


@app.post("/api/bids/{bid_id}/complete", response_model=BidOut)
def complete_bid(
    bid_id: int,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    require_client(user)

    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Відгук не знайдено")
    if bid.status != "accepted":
        raise HTTPException(status_code=400, detail="Спочатку погодьте майстра")

    task = db.query(Task).filter(Task.id == bid.task_id).first()
    if not task or task.client_id != user.id:
        raise HTTPException(status_code=403, detail="Немає доступу")

    bid.status = "completed"
    task.status = "completed"
    db.add(bid)
    db.add(task)
    db.commit()
    db.refresh(bid)
    return bid_to_out(db, bid)


@app.post("/api/bids/{bid_id}/review", response_model=ReviewOut)
def review_bid(
    bid_id: int,
    payload: ReviewCreate,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    require_client(user)

    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Відгук не знайдено")
    if bid.status != "completed":
        raise HTTPException(status_code=400, detail="Оцінка доступна після завершення роботи")

    task = db.query(Task).filter(Task.id == bid.task_id).first()
    if not task or task.client_id != user.id:
        raise HTTPException(status_code=403, detail="Немає доступу")

    existing = db.query(Review).filter(Review.bid_id == bid_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ви вже залишили відгук")

    review = Review(
        task_id=bid.task_id,
        bid_id=bid.id,
        performer_id=bid.performer_id,
        client_id=user.id,
        rating=payload.rating,
        comment=payload.comment.strip(),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


@app.get("/api/performers/{performer_id}/profile", response_model=PerformerProfileOut)
def performer_profile(
    performer_id: int,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    require_registered(user)

    performer = db.query(User).filter(User.id == performer_id, User.role == "performer").first()
    if not performer:
        raise HTTPException(status_code=404, detail="Майстра не знайдено")

    rating_avg, completed_jobs = get_performer_stats(db, performer_id)
    reviews = db.query(Review).filter(Review.performer_id == performer_id).order_by(Review.created_at.desc()).all()

    return PerformerProfileOut(
        performer_id=performer.id,
        full_name=performer.full_name,
        city=performer.city,
        profile_photo_url=performer.profile_photo_url,
        categories=performer.performer_categories,
        completed_jobs=completed_jobs,
        rating_avg=rating_avg,
        reviews=[ReviewOut.model_validate(r) for r in reviews],
    )


@app.get("/api/bids/{bid_id}/client-contact")
def bid_client_contact(
    bid_id: int,
    x_telegram_init_data: str | None = Header(default=None),
    x_telegram_user: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = authorize(db, x_telegram_init_data, x_telegram_user, x_dev_user_id)
    require_performer(user)

    bid = db.query(Bid).filter(Bid.id == bid_id, Bid.performer_id == user.id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Відгук не знайдено")
    if bid.status not in {"accepted", "completed"}:
        raise HTTPException(status_code=403, detail="Контакти клієнта будуть доступні після погодження")

    task = db.query(Task).filter(Task.id == bid.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Завдання не знайдено")
    client = db.query(User).filter(User.id == task.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клієнта не знайдено")

    return {
        "full_name": client.full_name,
        "phone": client.phone,
        "city": client.city,
        "address": client.address,
    }
