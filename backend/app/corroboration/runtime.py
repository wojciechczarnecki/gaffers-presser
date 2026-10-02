from app.core.clock import Clock
from app.corroboration.config import CorroborationSettings
from app.corroboration.judge import build_judge
from app.corroboration.service import CorroborationRuntime
from app.corroboration.tracing import (
    NULL_CORROBORATION_TRACER,
    CorroborationTracer,
    make_corroboration_tracer,
)
from app.llm.chat import build_chat_model, resolve_llm
from app.llm.pricing import load_prices
from app.llm.structured import StructuredCaller
from app.llm.tracing import resolve_tracing
from app.retrieval.config import resolve_embedding
from app.retrieval.embedder import build_embedder

NOT_CONFIGURED = "OPENROUTER_API_KEY is not set"


def sql_only_runtime(
    reason: str, tracer: CorroborationTracer = NULL_CORROBORATION_TRACER
) -> CorroborationRuntime:
    return CorroborationRuntime(embedder=None, judge=None, tracer=tracer, skipped_reason=reason)


def build_runtime(settings: CorroborationSettings, clock: Clock) -> CorroborationRuntime:
    tracer = make_corroboration_tracer(resolve_tracing(settings))
    if settings.openrouter_api_key is None:
        return sql_only_runtime(NOT_CONFIGURED, tracer)
    prices = load_prices()
    embedding = resolve_embedding(settings, prices=prices)
    llm = resolve_llm(settings)
    assert embedding is not None and llm is not None
    spec = build_chat_model(llm)
    judge = build_judge(StructuredCaller.from_spec(spec, prices, clock))
    return CorroborationRuntime(
        embedder=build_embedder(embedding), judge=judge, tracer=tracer, prices=prices
    )
