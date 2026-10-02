from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class DeliveryLog(SQLModel, table=True):
    __tablename__ = "delivery_log"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_delivery_log_idempotency_key"),
        Index("ix_delivery_log_requested_at", "requested_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    idempotency_key: str
    kind: str  # alert | presser | test
    channel: str
    title: str
    text_body: str
    html_body: str | None = None
    status: str  # sent | failed (`sending` lives only inside the uncommitted send transaction)
    provider_message_id: str | None = None
    attempts: int
    requested_at: datetime = Field(sa_column=utc_column())
    accepted_at: datetime | None = Field(default=None, sa_column=utc_column(nullable=True))
    error_class: str | None = None
    http_status: int | None = None
