from app.llm.chat import ChatSettings
from app.retrieval.config import RetrievalSettings

# The judge's default model, chosen by the owner on corroboration set v2 over the rule's
# qwen/qwen3.8-flash: the closest to the threshold, within the budget (ADR 0011).
DEFAULT_JUDGE_MODEL = "google/gemini-3.1-flash-lite"


class CorroborationSettings(ChatSettings, RetrievalSettings):
    llm_judge_model: str = ""


def judge_model(settings: CorroborationSettings) -> str:
    return settings.llm_judge_model or DEFAULT_JUDGE_MODEL
