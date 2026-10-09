from langfuse import Langfuse

from app.llm.tracing import (
    GenerationTracer,
    LangfuseGenerationTracer,
    NullGenerationTracer,
    TracingConfig,
    make_generation_tracer,
)

PresserTracer = GenerationTracer
NULL_PRESSER_TRACER = NullGenerationTracer()


class LangfusePresserTracer(LangfuseGenerationTracer):
    def __init__(self, client: Langfuse) -> None:
        super().__init__(client, label="presser", generation_name="presser-writer")


def make_presser_tracer(tracing: TracingConfig | None) -> PresserTracer:
    return make_generation_tracer(tracing, "presser", LangfusePresserTracer, NULL_PRESSER_TRACER)
