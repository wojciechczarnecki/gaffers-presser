from sqlalchemy import Connection, text
from sqlmodel import Session

# Fixed, arbitrary bigint keys for PostgreSQL advisory locks (see docs/DECISIONS.md).
SCHEDULE_LOCK_KEY = 8_002_001
JOB_LOCK_KEY = 8_002_002


def acquire_job_lock(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": JOB_LOCK_KEY})


def try_schedule_lock(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": SCHEDULE_LOCK_KEY}
        ).scalar()
    )
