import logging
import time
from dataclasses import dataclass, replace
from datetime import UTC
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from app.content import load_prompt
from app.extraction.fixtures import FixtureIndex, FixtureRecord, render_fixture
from app.extraction.lineups import LINEUP_EVENTS
from app.extraction.linking import PlayerIndex, PlayerRecord, TeamRecord
from app.extraction.schemas import (
    Disambiguation,
    ExtractionOutput,
    FlowResult,
    LinkedEvent,
    MatchCheck,
    PostInput,
)
from app.llm.chat import ChatModelSpec
from app.llm.pricing import Price, compute_cost
from app.llm.structured import Usage, answer_from_raw, usage_from_raw
from app.llm.tracing import GenerationTracer, NullGenerationTracer

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = load_prompt("extraction")
LINK_DISAMBIGUATION_PROMPT = load_prompt("link_disambiguation")
MATCH_CHECK_PROMPT = load_prompt("match_check")

PROMPT_VERSION = (
    f"extraction@{EXTRACTION_PROMPT.version}"
    f"+link_disambiguation@{LINK_DISAMBIGUATION_PROMPT.version}"
    f"+match_check@{MATCH_CHECK_PROMPT.version}"
)


class ExtractionOutputError(Exception):
    pass


class FlowState(TypedDict):
    post: PostInput
    extracted: ExtractionOutput | None
    events: list[LinkedEvent]
    usage: Usage
    llm_calls: int
    answered_model: str | None
    host: str | None
    generation_id: str | None


def _render_post(post: PostInput) -> str:
    return "\n".join(
        [
            f"Author: @{post.author_handle}",
            f"Posted at: {post.created_at.isoformat()}",
            f"Repost: {post.is_repost}",
            f"Reply: {post.is_reply}",
            "",
            post.text,
        ]
    )


def _render_candidate(candidate: PlayerRecord, index: PlayerIndex) -> str:
    team = index.team_for(candidate.team_fpl_id)
    team_name = team.name if team is not None else "unknown team"
    full_name = f"{candidate.first_name} {candidate.second_name}".strip()
    return f"{candidate.fpl_id} — {full_name} ({candidate.web_name}), {team_name}"


def _render_disambiguation(
    post: PostInput,
    mention: str,
    team: str | None,
    candidates: list[PlayerRecord],
    index: PlayerIndex,
) -> str:
    lines = [
        f"Post: {post.text}",
        f"Mention: {mention}",
        f"Team named in the post: {team or 'none'}",
        "Candidates:",
        *[f"- {_render_candidate(candidate, index)}" for candidate in candidates],
    ]
    return "\n".join(lines)


def _render_match_check(
    post: PostInput,
    team: TeamRecord,
    previous: FixtureRecord | None,
    following: FixtureRecord,
    index: PlayerIndex,
) -> str:
    before = render_fixture(previous, team.fpl_id, index) if previous is not None else "none"
    return "\n".join(
        [
            f"Post: {post.text}",
            f"Posted at: {post.created_at.astimezone(UTC).isoformat()}",
            f"Club: {team.name}",
            f"Previous PL fixture: {before}",
            f"Next PL fixture: {render_fixture(following, team.fpl_id, index)}",
        ]
    )


@dataclass(frozen=True)
class _Reply:
    parsed: Any
    parsing_error: BaseException | None
    usage: Usage
    answered_model: str | None
    host: str | None
    generation_id: str | None

    @property
    def error_class(self) -> str | None:
        if self.parsing_error is not None:
            return type(self.parsing_error).__name__
        return "NoParsedResult" if self.parsed is None else None


class Flow:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def run(self, post: PostInput) -> FlowResult:
        state: FlowState = {
            "post": post,
            "extracted": None,
            "events": [],
            "usage": Usage(),
            "llm_calls": 0,
            "answered_model": None,
            "host": None,
            "generation_id": None,
        }
        result = self._graph.invoke(state)
        return FlowResult(
            events=result["events"],
            usage=result["usage"],
            llm_calls=result["llm_calls"],
            answered_model=result["answered_model"],
            host=result["host"],
            generation_id=result["generation_id"],
        )


def build_flow(
    spec: ChatModelSpec,
    index: PlayerIndex,
    tracer: GenerationTracer | None = None,
    prices: dict[str, Price] | None = None,
    fixtures: FixtureIndex | None = None,
) -> Flow:
    traced = tracer or NullGenerationTracer()
    known_prices = prices or {}

    def call(schema: type[BaseModel], system: str, human: str) -> _Reply:
        # One generation per LLM call, priced from prices.toml; a failed call is traced too.
        started = time.monotonic()
        try:
            structured = spec.chat_model.with_structured_output(
                schema, include_raw=True, **spec.structured_kwargs
            )
            result = structured.invoke([SystemMessage(system), HumanMessage(human)])
        except Exception as exc:
            traced.generation(
                model=spec.model,
                input=human,
                output=None,
                usage=None,
                cost_usd=None,
                latency_seconds=time.monotonic() - started,
                error_class=type(exc).__name__,
            )
            raise
        latency = time.monotonic() - started
        raw = result.get("raw")
        answered_model, host, generation_id = answer_from_raw(raw)
        reply = _Reply(
            parsed=result.get("parsed"),
            parsing_error=result.get("parsing_error"),
            usage=usage_from_raw(raw) if raw is not None else Usage(),
            answered_model=answered_model,
            host=host,
            generation_id=generation_id,
        )
        model = answered_model or spec.model
        usage = reply.usage
        traced.generation(
            model=model,
            input=human,
            output=reply.parsed.model_dump_json() if reply.parsed is not None else None,
            usage=usage,
            cost_usd=compute_cost(model, usage.input_tokens, usage.output_tokens, known_prices),
            latency_seconds=latency,
            error_class=reply.error_class,
        )
        return reply

    def extract_node(state: FlowState) -> dict[str, Any]:
        post = state["post"]
        reply = call(ExtractionOutput, EXTRACTION_PROMPT.text, _render_post(post))
        if reply.error_class is not None:
            reason = reply.error_class if reply.parsing_error else "no parsed result"
            raise ExtractionOutputError(f"extraction output failed validation: {reason}")
        return {
            "extracted": reply.parsed,
            "usage": state["usage"] + reply.usage,
            "llm_calls": state["llm_calls"] + 1,
            "answered_model": reply.answered_model,
            "host": reply.host,
            "generation_id": reply.generation_id,
        }

    def link_node(state: FlowState) -> dict[str, Any]:
        extracted = state["extracted"]
        assert extracted is not None
        events: list[LinkedEvent] = []
        usage = state["usage"]
        llm_calls = state["llm_calls"]

        resolved = index.resolve_post([(item.player, item.team) for item in extracted.events])
        for item, candidates in zip(extracted.events, resolved, strict=True):
            player: PlayerRecord | None = None

            if len(candidates) == 1:
                player = candidates[0]
            elif len(candidates) > 1:
                human = _render_disambiguation(
                    state["post"], item.player, item.team, candidates, index
                )
                fpl_id: int | None = None
                try:
                    reply = call(Disambiguation, LINK_DISAMBIGUATION_PROMPT.text, human)
                    llm_calls += 1
                    usage = usage + reply.usage
                    if reply.parsed is not None:
                        fpl_id = reply.parsed.fpl_id
                except Exception as exc:
                    logger.warning("disambiguation call failed: %s", type(exc).__name__)
                if fpl_id is not None:
                    player = next((c for c in candidates if c.fpl_id == fpl_id), None)

            events.append(
                LinkedEvent(
                    mention=item.player,
                    team=item.team,
                    player_season=player.season if player is not None else None,
                    player_fpl_id=player.fpl_id if player is not None else None,
                    event_type=item.event_type,
                    certainty=item.certainty,
                )
            )

        return {"events": events, "usage": usage, "llm_calls": llm_calls}

    def match_check_node(state: FlowState) -> dict[str, Any]:
        if fixtures is None:
            return {}
        post = state["post"]
        events = list(state["events"])
        usage = state["usage"]
        llm_calls = state["llm_calls"]

        by_club: dict[int, list[int]] = {}
        for position, event in enumerate(events):
            if event.event_type not in LINEUP_EVENTS or event.player_fpl_id is None:
                continue
            player = index.player(event.player_fpl_id)
            if player is not None:
                by_club.setdefault(player.team_fpl_id, []).append(position)

        for club, positions in by_club.items():
            team = index.team_for(club)
            previous, following = fixtures.around(club, post.created_at)
            if team is None or following is None:
                continue
            human = _render_match_check(post, team, previous, following, index)
            try:
                reply = call(MatchCheck, MATCH_CHECK_PROMPT.text, human)
            except Exception as exc:
                logger.warning("match check call failed: %s", type(exc).__name__)
                continue
            llm_calls += 1
            usage = usage + reply.usage
            if reply.parsed is None:
                continue
            for position in positions:
                events[position] = replace(
                    events[position], for_next_pl_match=reply.parsed.for_next_pl_match
                )

        return {"events": events, "usage": usage, "llm_calls": llm_calls}

    graph: StateGraph = StateGraph(FlowState)
    graph.add_node("extract", extract_node)
    graph.add_node("link", link_node)
    graph.add_node("match_check", match_check_node)
    graph.set_entry_point("extract")
    graph.add_edge("extract", "link")
    graph.add_edge("link", "match_check")
    graph.add_edge("match_check", END)
    return Flow(graph.compile())
