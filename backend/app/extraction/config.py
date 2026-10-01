from app.llm.chat import ChatSettings, load_chat_settings

ExtractionSettings = ChatSettings


def load_extraction_settings() -> ExtractionSettings:
    return load_chat_settings()
