from pydantic import BaseModel

from app.llm.chat import DEFAULT_MODEL

DEFAULT_LABEL_MODEL = DEFAULT_MODEL


class WrittenQuery(BaseModel):
    query: str


class RelevanceLabel(BaseModel):
    relevant: bool
