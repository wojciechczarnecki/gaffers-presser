from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, SQLModel

CHUNK_SIZE = 1000


def upsert(
    session: Session,
    model: type[SQLModel],
    rows: list[dict],
    conflict_cols: list[str],
) -> None:
    if not rows:
        return
    table = model.__table__
    non_key_cols = [c.name for c in table.columns if c.name not in conflict_cols]
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start : start + CHUNK_SIZE]
        stmt = insert(table).values(chunk)
        if non_key_cols:
            set_ = {col: getattr(stmt.excluded, col) for col in non_key_cols}
            stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=set_)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        session.execute(stmt)
