import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

from app.extraction.evaluation.cases import EvalCase, ExpectedEvent
from app.extraction.evaluation.metrics import CaseResult, Metrics, compute_metrics
from app.extraction.flow import PROMPT_VERSION, Flow, build_flow
from app.extraction.linking import PlayerIndex
from app.extraction.pricing import compute_cost, load_prices
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import FlowResult, PostInput
from app.extraction.service import run_with_retries
from app.extraction.tracing import run_config
from app.tweets.loop import Clock


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

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "run_name": self.run_name,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "split": self.split,
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
                    "expected": [e.model_dump() for e in outcome.result.expected],
                    "predicted": [e.model_dump() for e in outcome.result.predicted],
                }
                for outcome in self.outcomes
            ],
        }


class _TimedFlow(Flow):
    """Measures the duration of the last successful run, without the retry back-off."""

    def __init__(self, inner: Flow) -> None:
        self._inner = inner
        self.last_seconds: float | None = None

    def run(self, post: PostInput, config: RunnableConfig) -> FlowResult:
        started = time.perf_counter()
        result = self._inner.run(post, config)
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


def run_evaluation(
    cases: Sequence[EvalCase],
    spec: ChatModelSpec,
    index: PlayerIndex,
    handler: BaseCallbackHandler | None,
    run_name: str,
    split: str,
    clock: Clock,
    posts_per_month: float,
    usd_pln_rate: float,
) -> EvaluationReport:
    flow = _TimedFlow(build_flow(spec, index))
    prices = load_prices()
    stop_event = threading.Event()
    outcomes: list[CaseOutcome] = []

    for case in cases:
        post = _post_from_case(case)
        config = run_config(
            case.id, PROMPT_VERSION, spec.provider, spec.model, handler, run_name=run_name
        )
        retry = run_with_retries(flow, post, config, clock, stop_event)
        expected = list(case.expected_events)
        if retry.result is None:
            error_class = type(retry.error).__name__ if retry.error else "UnknownError"
            result = CaseResult(expected=expected, predicted=[], error_class=error_class)
        else:
            usage = retry.result.usage
            result = CaseResult(
                expected=expected,
                predicted=[
                    ExpectedEvent(
                        mention=event.mention,
                        fpl_id=event.player_fpl_id,
                        event_type=event.event_type,
                        certainty=event.certainty,
                    )
                    for event in retry.result.events
                ],
                latency_seconds=flow.last_seconds,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=compute_cost(spec.model, usage.input_tokens, usage.output_tokens, prices),
            )
        outcomes.append(CaseOutcome(case.id, result, retry.attempts))

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
    )
