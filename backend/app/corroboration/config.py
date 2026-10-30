from app.llm.chat import ChatSettings
from app.retrieval.config import RetrievalSettings

# The judge's default model, picked by the rule on corroboration set v2 (ADR 0011).
DEFAULT_JUDGE_MODEL = "qwen/qwen3.8-flash"


class CorroborationSettings(ChatSettings, RetrievalSettings):
    llm_judge_model: str = ""


def judge_model(settings: CorroborationSettings) -> str:
    return settings.llm_judge_model or DEFAULT_JUDGE_MODEL
