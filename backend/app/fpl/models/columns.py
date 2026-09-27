from sqlalchemy import Column, DateTime


def utc_column(*, nullable: bool = False, primary_key: bool = False) -> Column:
    return Column(DateTime(timezone=True), nullable=nullable, primary_key=primary_key)
