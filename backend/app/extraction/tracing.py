from typing import Any

from langfuse import Langfuse

from app.core.retry import RetryOutcome
from app.extraction.schemas import FlowResult
from app.llm.tracing import (
    GenerationTracer,
    LangfuseGenerationTracer,
    NullGenerationTracer,
    TracingConfig,
    make_generation_tracer,
)

ExtractionTracer = GenerationTracer
NULL_EXTRACTION_TRACER = NullGenerationTracer()


class LangfuseExtractionTracer(LangfuseGenerationTracer):
    def __init__(self, client: Langfuse) -> None:
        super().__init__(client, label="extraction", generation_name="extraction-llm")


def make_extraction_tracer(tracing: TracingConfig | None) -> ExtractionTracer:
    return make_generation_tracer(
        tracing, "extraction", LangfuseExtractionTracer, NULL_EXTRACTION_TRACER
    )


def span_input(x_id: int | str, prompt_version: str, provider: str, model: str) -> dict[str, Any]:
    return {"x_id": x_id, "prompt_version": prompt_version, "provider": provider, "model": model}


def span_output(retry: RetryOutcome[FlowResult]) -> dict[str, Any]:
    if retry.result is not None:
        return {"events": len(retry.result.events), "attempts": retry.attempts}
    if retry.stopped:
        return {"stopped": True}
    error_class = type(retry.error).__name__ if retry.error is not None else "UnknownError"
    return {"error_class": error_class, "attempts": retry.attempts}
