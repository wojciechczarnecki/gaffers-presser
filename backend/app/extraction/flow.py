import logging
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.content import load_prompt
from app.extraction.linking import PlayerIndex, PlayerRecord
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import (
    Disambiguation,
    ExtractionOutput,
    FlowResult,
    LinkedEvent,
    PostInput,
    Usage,
)

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = load_prompt("extraction")
LINK_DISAMBIGUATION_PROMPT = load_prompt("link_disambiguation")

PROMPT_VERSION = (
    f"extraction@{EXTRACTION_PROMPT.version}"
    f"+link_disambiguation@{LINK_DISAMBIGUATION_PROMPT.version}"
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


def _usage_from_raw(raw: Any) -> Usage:
    usage_metadata = getattr(raw, "usage_metadata", None) or {}
    response_metadata = getattr(raw, "response_metadata", None) or {}
    output_details = usage_metadata.get("output_token_details") or {}
    return Usage(
        input_tokens=usage_metadata.get("input_tokens"),
        output_tokens=usage_metadata.get("output_tokens"),
        reasoning_tokens=output_details.get("reasoning"),
        reported_cost_usd=response_metadata.get("cost"),
    )


def _answer_from_raw(raw: Any) -> tuple[str | None, str | None, str | None]:
    response_metadata = getattr(raw, "response_metadata", None) or {}
    return (
        response_metadata.get("model_name"),
        response_metadata.get("provider"),
        response_metadata.get("id"),
    )


class Flow:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def run(self, post: PostInput, config: RunnableConfig) -> FlowResult:
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
        result = self._graph.invoke(state, config=config)
        return FlowResult(
            events=result["events"],
            usage=result["usage"],
            llm_calls=result["llm_calls"],
            answered_model=result["answered_model"],
            host=result["host"],
            generation_id=result["generation_id"],
        )


def build_flow(spec: ChatModelSpec, index: PlayerIndex) -> Flow:
    def extract_node(state: FlowState) -> dict[str, Any]:
        post = state["post"]
        messages = [SystemMessage(EXTRACTION_PROMPT.text), HumanMessage(_render_post(post))]
        structured = spec.chat_model.with_structured_output(
            ExtractionOutput, include_raw=True, **spec.structured_kwargs
        )
        result = structured.invoke(messages)
        if result["parsing_error"] is not None or result["parsed"] is None:
            parsing_error = result["parsing_error"]
            reason = type(parsing_error).__name__ if parsing_error else "no parsed result"
            raise ExtractionOutputError(f"extraction output failed validation: {reason}")
        usage = state["usage"] + _usage_from_raw(result["raw"])
        answered_model, host, generation_id = _answer_from_raw(result["raw"])
        return {
            "extracted": result["parsed"],
            "usage": usage,
            "llm_calls": state["llm_calls"] + 1,
            "answered_model": answered_model,
            "host": host,
            "generation_id": generation_id,
        }

    def link_node(state: FlowState) -> dict[str, Any]:
        extracted = state["extracted"]
        assert extracted is not None
        events: list[LinkedEvent] = []
        usage = state["usage"]
        llm_calls = state["llm_calls"]

        for item in extracted.events:
            candidates = index.resolve(item.player, item.team)
            player: PlayerRecord | None = None

            if len(candidates) == 1:
                player = candidates[0]
            elif len(candidates) > 1:
                structured = spec.chat_model.with_structured_output(
                    Disambiguation, include_raw=True, **spec.structured_kwargs
                )
                messages = [
                    SystemMessage(LINK_DISAMBIGUATION_PROMPT.text),
                    HumanMessage(
                        _render_disambiguation(
                            state["post"], item.player, item.team, candidates, index
                        )
                    ),
                ]
                fpl_id: int | None = None
                try:
                    result = structured.invoke(messages)
                    llm_calls += 1
                    raw = result.get("raw")
                    if raw is not None:
                        usage = usage + _usage_from_raw(raw)
                    parsed = result.get("parsed")
                    if parsed is not None:
                        fpl_id = parsed.fpl_id
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

    graph: StateGraph = StateGraph(FlowState)
    graph.add_node("extract", extract_node)
    graph.add_node("link", link_node)
    graph.set_entry_point("extract")
    graph.add_edge("extract", "link")
    graph.add_edge("link", END)
    return Flow(graph.compile())
