from typing import Protocol

from langfuse import Langfuse

from app.llm.tracing import (
    GenerationTracer,
    LangfuseGenerationTracer,
    NullGenerationTracer,
    TracingConfig,
    make_generation_tracer,
)
from app.retrieval.tracing import NULL_TRACER, LangfuseTracer, RetrievalTracer


class CorroborationTracer(GenerationTracer, Protocol):
    def retrieval(self) -> RetrievalTracer: ...


class NullCorroborationTracer(NullGenerationTracer):
    def retrieval(self) -> RetrievalTracer:
        return NULL_TRACER


NULL_CORROBORATION_TRACER = NullCorroborationTracer()


class LangfuseCorroborationTracer(LangfuseGenerationTracer):
    def __init__(self, client: Langfuse) -> None:
        super().__init__(client, label="corroboration", generation_name="corroboration-judge")

    def retrieval(self) -> RetrievalTracer:
        return LangfuseTracer(self._client)


def make_corroboration_tracer(tracing: TracingConfig | None) -> CorroborationTracer:
    return make_generation_tracer(
        tracing, "corroboration", LangfuseCorroborationTracer, NULL_CORROBORATION_TRACER
    )
