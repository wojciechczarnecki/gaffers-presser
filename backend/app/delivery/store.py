from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

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
