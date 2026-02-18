from datetime import datetime

from pydantic import BaseModel, Field


class UserBase(BaseModel):
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class UserOut(UserBase):
    id: int
    full_name: str | None = None
    phone: str | None = None
    city: str | None = None
    address: str | None = None
    profile_photo_url: str | None = None
    telegram_chat_id: int | None = None
    is_client_registered: bool = False
    is_performer_registered: bool = False
    role: str | None = None
    performer_categories: list[str] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class TaskOut(BaseModel):
    id: int
    title: str
    description: str
    category: str
    city: str
    budget: int
    photos: list[str] = Field(default_factory=list)
    status: str
    client_id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class BidCreate(BaseModel):
    price: int = Field(gt=0)
    message: str = Field(default="", max_length=1000)


class BidOut(BaseModel):
    id: int
    task_id: int
    performer_id: int
    price: int
    message: str
    status: str
    performer_name: str | None = None
    performer_photo_url: str | None = None
    performer_rating: float | None = None
    performer_completed_jobs: int | None = None
    has_review: bool | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(min_length=2, max_length=1000)


class ReviewOut(BaseModel):
    id: int
    task_id: int
    bid_id: int
    performer_id: int
    client_id: int
    rating: int
    comment: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PerformerProfileOut(BaseModel):
    performer_id: int
    full_name: str | None = None
    city: str | None = None
    profile_photo_url: str | None = None
    categories: list[str] = Field(default_factory=list)
    completed_jobs: int
    rating_avg: float
    reviews: list[ReviewOut] = Field(default_factory=list)


class CabinetTaskOut(BaseModel):
    task_id: int
    title: str
    category: str
    city: str
    budget: int
    task_status: str
    selected_performer_name: str | None = None
    selected_price: int | None = None
    bid_status: str | None = None
    created_at: datetime
