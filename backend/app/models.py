from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
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
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
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
    captures: Mapped[list["CaptureSnapshot"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="CaptureSnapshot.received_at",
    )


class CaptureSnapshot(Base):
    __tablename__ = "capture_snapshots"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_capture_snapshots_idempotency_key"),
        Index("ix_capture_snapshots_item_received", "item_id", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    item_id: Mapped[str] = mapped_column(
        ForeignKey("product_items.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(16))
    adapter_id: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    page_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    item: Mapped[ProductItem] = relationship(back_populates="captures")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_task_created", "task_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    model: Mapped[str] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    events: Mapped[list[dict]] = mapped_column(JSON, default=list)
    question: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BrowserCaptureJob(Base):
    __tablename__ = "browser_capture_jobs"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_browser_capture_tool_call"),
        Index("ix_browser_capture_pending", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    item_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    tool_call_id: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(String(800))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attempts: Mapped[int] = mapped_column(default=0)
    snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("capture_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Short diagnostic from the extension; a capture failure was previously
    # indistinguishable from a lease timeout, which made the cause hard to find.
    error_detail: Mapped[str | None] = mapped_column(String(200), nullable=True)
