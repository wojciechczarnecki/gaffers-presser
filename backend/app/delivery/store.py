from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, func
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.delivery.models import DeliveryLog


def claim_row(
    session: Session,
    *,
    key: str,
    kind: str,
    channel: str,
    title: str,
    text_body: str,
    html_body: str | None,
    requested_at: datetime,
) -> DeliveryLog:
    session.execute(
        insert(DeliveryLog.__table__)
        .values(
            idempotency_key=key,
            kind=kind,
            channel=channel,
            title=title,
            text_body=text_body,
            html_body=html_body,
            status="sending",
            attempts=0,
            requested_at=requested_at,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    return session.exec(
        select(DeliveryLog)
        .where(DeliveryLog.idempotency_key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def recent_rows(engine: Engine, limit: int = 10) -> list[DeliveryLog]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(DeliveryLog)
                .order_by(col(DeliveryLog.requested_at).desc(), col(DeliveryLog.id).desc())
                .limit(limit)
            ).all()
        )


@dataclass(frozen=True)
class DeliverySummary:
    last_sent_at: datetime | None
    last_sent_kind: str | None
    failed_last_24h: int


def delivery_summary(engine: Engine, now: datetime) -> DeliverySummary:
    with Session(engine) as session:
        last = session.exec(
            select(DeliveryLog)
            .where(DeliveryLog.status == "sent")
            .order_by(col(DeliveryLog.accepted_at).desc(), col(DeliveryLog.id).desc())
            .limit(1)
        ).first()
        failed = session.exec(
            select(func.count())
            .select_from(DeliveryLog)
            .where(DeliveryLog.status == "failed")
            .where(DeliveryLog.requested_at >= now - timedelta(hours=24))
        ).one()
    return DeliverySummary(
        last_sent_at=last.accepted_at if last else None,
        last_sent_kind=last.kind if last else None,
        failed_last_24h=failed,
    )
