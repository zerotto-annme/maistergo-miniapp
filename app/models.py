from datetime import datetime
import json

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_photo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    is_client_registered: Mapped[int] = mapped_column(Integer, default=0)
    is_performer_registered: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    performer_categories_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tasks: Mapped[list["Task"]] = relationship(back_populates="client")
    bids: Mapped[list["Bid"]] = relationship(back_populates="performer")
    reviews_received: Mapped[list["Review"]] = relationship(
        back_populates="performer",
        foreign_keys="Review.performer_id",
    )
    reviews_left: Mapped[list["Review"]] = relationship(
        back_populates="client",
        foreign_keys="Review.client_id",
    )

    @property
    def performer_categories(self) -> list[str]:
        try:
            data = json.loads(self.performer_categories_json or "[]")
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
            return []
        except json.JSONDecodeError:
            return []

    def set_performer_categories(self, categories: list[str]) -> None:
        clean = [c.strip() for c in categories if c.strip()]
        self.performer_categories_json = json.dumps(clean, ensure_ascii=False)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(100))
    budget: Mapped[int] = mapped_column(Integer)
    photos_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="open")
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    client: Mapped["User"] = relationship(back_populates="tasks")
    bids: Mapped[list["Bid"]] = relationship(back_populates="task", cascade="all, delete-orphan")

    @property
    def photos(self) -> list[str]:
        try:
            data = json.loads(self.photos_json or "[]")
            if isinstance(data, list):
                return [str(x) for x in data]
            return []
        except json.JSONDecodeError:
            return []

    def set_photos(self, urls: list[str]) -> None:
        self.photos_json = json.dumps(urls, ensure_ascii=False)


class Bid(Base):
    __tablename__ = "bids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    performer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    price: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["Task"] = relationship(back_populates="bids")
    performer: Mapped["User"] = relationship(back_populates="bids")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    bid_id: Mapped[int] = mapped_column(ForeignKey("bids.id"), unique=True, index=True)
    performer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    performer: Mapped["User"] = relationship(back_populates="reviews_received", foreign_keys=[performer_id])
    client: Mapped["User"] = relationship(back_populates="reviews_left", foreign_keys=[client_id])
