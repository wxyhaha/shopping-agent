from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_updated_at", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(120), default="商品比较")
    status: Mapped[str] = mapped_column(String(24), default="open")
    create_idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    create_request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    items: Mapped[list["ProductItem"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ProductItem.created_at",
    )


class ProductItem(Base):
    __tablename__ = "product_items"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_product_items_idempotency_key"),
        Index("ix_product_items_task_created", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    page_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(24))
    browser: Mapped[str | None] = mapped_column(String(16), nullable=True)
    capture_status: Mapped[str] = mapped_column(String(32), default="not_started")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    task: Mapped[Task] = relationship(back_populates="items")
