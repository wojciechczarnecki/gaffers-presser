from sqlalchemy import text


def test_container_database_is_postgres_16(db):
    with db.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar()
    assert version.startswith("PostgreSQL 16")


def test_engine_reads_back_utc(db):
    with db.connect() as conn:
        timezone = conn.execute(text("SHOW timezone")).scalar()
    assert timezone == "UTC"
