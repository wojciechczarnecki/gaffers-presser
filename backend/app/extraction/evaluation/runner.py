import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from app.core.clock import Clock
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent
from app.extraction.evaluation.metrics import CaseResult, Metrics, RawEvent, compute_metrics
from app.extraction.fixtures import FixtureIndex
from app.extraction.flow import PROMPT_VERSION, Flow, build_flow
from app.extraction.lineups import kept_at_read
from app.extraction.linking import PlayerIndex
from app.extraction.schemas import FlowResult, LinkedEvent, PostInput
from app.extraction.service import run_with_retries
from app.extraction.tracing import ExtractionTracer, span_input, span_output
from app.llm.chat import ChatModelSpec
from app.llm.models import ModelSettings
from app.llm.pricing import compute_cost, load_prices


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    result: CaseResult
    attempts: int


@dataclass(frozen=True)
class EvaluationReport:
    run_name: str
    provider: str
    model: str
    prompt_version: str
    split: str
    metrics: Metrics
    outcomes: list[CaseOutcome]
    settings: ModelSettings | None = None

    def to_json_dict(self) -> dict[str, Any]:
        settings = self.settings
        return {
            "run_name": self.run_name,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "split": self.split,
            "structured_method": settings.structured_method if settings else None,
            "reasoning_effort": settings.reasoning_effort if settings else None,
            "temperature": settings.temperature if settings else None,
            "cases": self.metrics.cases,
            "errored_cases": self.metrics.errored_cases,
            "metrics": asdict(self.metrics),
            "case_results": [
                {
                    "id": outcome.case_id,
                    "attempts": outcome.attempts,
                    "error_class": outcome.result.error_class,
                    "latency_seconds": outcome.result.latency_seconds,
                    "input_tokens": outcome.result.input_tokens,
                    "output_tokens": outcome.result.output_tokens,
                    "cost_usd": outcome.result.cost_usd,
                    "reasoning_tokens": outcome.result.reasoning_tokens,
                    "reported_cost_usd": outcome.result.reported_cost_usd,
                    "host": outcome.result.host,
                    "answered_model": outcome.result.answered_model,
                    "generation_id": outcome.result.generation_id,
                    "expected": [e.model_dump() for e in outcome.result.expected],
                    "predicted": [e.model_dump() for e in outcome.result.predicted],
                    "raw_predicted": [e.model_dump() for e in outcome.result.raw_predicted],
                    "other_match": outcome.result.other_match,
                }
                for outcome in self.outcomes
            ],
        }


class _TimedFlow(Flow):
    """Measures the duration of the last successful run, without the retry back-off."""

    def __init__(self, inner: Flow) -> None:
        self._inner = inner
        self.last_seconds: float | None = None

    def run(self, post: PostInput) -> FlowResult:
        started = time.perf_counter()
        result = self._inner.run(post)
        self.last_seconds = time.perf_counter() - started
        return result


def _post_from_case(case: EvalCase) -> PostInput:
    return PostInput(
        x_id=0,
        author_handle=case.author_handle,
        text=case.text,
        created_at=case.created_at,
        is_repost=case.is_repost,
        is_reply=case.is_reply,
    )


def _raw_events(
    events: Sequence[LinkedEvent], case: EvalCase, index: PlayerIndex, fixtures: FixtureIndex
) -> list[RawEvent]:
    raw = []
    for event in events:
        player = index.player(event.player_fpl_id) if event.player_fpl_id is not None else None
        next_kickoff = (
            fixtures.next_kickoff(player.team_fpl_id, case.created_at) if player else None
        )
        raw.append(
            RawEvent(
                mention=event.mention,
                fpl_id=event.player_fpl_id,
                event_type=event.event_type,
                certainty=event.certainty,
                for_next_pl_match=event.for_next_pl_match,
                kept=kept_at_read(
                    event.event_type,
                    event.certainty,
                    case.created_at,
                    next_kickoff,
                    event.for_next_pl_match,
                ),
                kept_by_lead_only=kept_at_read(
                    event.event_type, event.certainty, case.created_at, next_kickoff, None
                ),
            )
        )
    return raw


def _with_host(outcome: CaseOutcome, host_lookup: Callable[[str], str | None]) -> CaseOutcome:
    result = outcome.result
    if result.host is not None or result.generation_id is None:
        return outcome
    host = host_lookup(result.generation_id)
    return CaseOutcome(outcome.case_id, replace(result, host=host), outcome.attempts)


def run_evaluation(
    cases: Sequence[EvalCase],
    spec: ChatModelSpec,
    index: PlayerIndex,
    tracer: ExtractionTracer,
    run_name: str,
    split: str,
    clock: Clock,
    posts_per_month: float,
    usd_pln_rate: float,
    host_lookup: Callable[[str], str | None] | None = None,
    fixtures: FixtureIndex | None = None,
) -> EvaluationReport:
    prices = load_prices()
    known_fixtures = fixtures or FixtureIndex([])
    flow = _TimedFlow(build_flow(spec, index, tracer, prices, known_fixtures))
    stop_event = threading.Event()
    outcomes: list[CaseOutcome] = []

    for case in cases:
        post = _post_from_case(case)
        trace_input = span_input(case.id, PROMPT_VERSION, spec.provider, spec.model)
        with tracer.span("extraction", trace_input, session_id=run_name) as span:
            retry = run_with_retries(flow, post, clock, stop_event)
            span.update(output=span_output(retry))
        expected = list(case.expected_events)
        if retry.result is None:
            error_class = type(retry.error).__name__ if retry.error else "UnknownError"
            result = CaseResult(
                expected=expected,
                predicted=[],
                error_class=error_class,
                other_match="other_match_lineup" in case.tags,
            )
        else:
            usage = retry.result.usage
            answered_model = retry.result.answered_model or spec.model
            raw = _raw_events(retry.result.events, case, index, known_fixtures)
            result = CaseResult(
                expected=expected,
                predicted=[
                    ExpectedEvent(
                        mention=event.mention,
                        fpl_id=event.fpl_id,
                        event_type=event.event_type,
                        certainty=event.certainty,
                    )
                    for event in raw
                    if event.kept
                ],
                raw_predicted=raw,
                other_match="other_match_lineup" in case.tags,
                latency_seconds=flow.last_seconds,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=compute_cost(
                    answered_model, usage.input_tokens, usage.output_tokens, prices
                ),
                reasoning_tokens=usage.reasoning_tokens,
                reported_cost_usd=usage.reported_cost_usd,
                host=retry.result.host,
                answered_model=retry.result.answered_model,
                generation_id=retry.result.generation_id,
            )
        outcomes.append(CaseOutcome(case.id, result, retry.attempts))

    if host_lookup is not None:
        outcomes = [_with_host(outcome, host_lookup) for outcome in outcomes]

    metrics = compute_metrics(
        [outcome.result for outcome in outcomes],
        posts_per_month=posts_per_month,
        usd_pln_rate=usd_pln_rate,
    )
    return EvaluationReport(
        run_name=run_name,
        provider=spec.provider,
        model=spec.model,
        prompt_version=PROMPT_VERSION,
        split=split,
        metrics=metrics,
        outcomes=outcomes,
        settings=spec.settings,
    )
