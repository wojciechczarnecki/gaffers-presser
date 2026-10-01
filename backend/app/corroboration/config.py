from app.llm.chat import ChatSettings
from app.retrieval.config import RetrievalSettings


class CorroborationSettings(ChatSettings, RetrievalSettings):
    pass
