from sqlalchemy import Engine
from sqlmodel import create_engine


def make_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"options": "-c timezone=UTC"},
    )
