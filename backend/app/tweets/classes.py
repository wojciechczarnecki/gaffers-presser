from typing import Literal

from sqlalchemy import text
from sqlmodel import Session

PostClass = Literal["list", "quoted", "context"]

_LATEST = "(SELECT handles FROM list_membership ORDER BY fetched_at DESC, id DESC LIMIT 1)"


def list_post_sql(alias: str) -> str:
    return f"({_LATEST} IS NULL OR lower({alias}.author_handle) = ANY({_LATEST}::text[]))"


def source_post_sql(alias: str) -> str:
    return (
        f"({list_post_sql(alias)} OR EXISTS (SELECT 1 FROM tweet quoting"
        f" WHERE quoting.quoted_x_id = {alias}.x_id AND {list_post_sql('quoting')}))"
    )


def post_classes(session: Session, x_ids: list[int]) -> dict[int, PostClass]:
    if not x_ids:
        return {}
    rows = session.execute(
        text(
            f"SELECT t.x_id, CASE WHEN {list_post_sql('t')} THEN 'list'"
            f" WHEN {source_post_sql('t')} THEN 'quoted' ELSE 'context' END"
            " FROM tweet t WHERE t.x_id = ANY(:ids)"
        ),
        {"ids": list(x_ids)},
    )
    return {row[0]: row[1] for row in rows}


def quoted_authors(session: Session, x_ids: list[int]) -> dict[int, str]:
    if not x_ids:
        return {}
    rows = session.execute(
        text(
            "SELECT quote.x_id, quoted.author_handle FROM tweet quote"
            " JOIN tweet quoted ON quoted.x_id = quote.quoted_x_id"
            " WHERE quote.x_id = ANY(:ids)"
        ),
        {"ids": list(x_ids)},
    )
    return {row[0]: row[1] for row in rows}
