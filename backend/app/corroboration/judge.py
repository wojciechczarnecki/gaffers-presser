from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from app.content import load_prompt
from app.corroboration.schemas import Claim, Label, PlayerRef, PostRef
from app.llm.structured import StructuredCaller, StructuredReply

JUDGE_PROMPT = load_prompt("corroboration_judge")
PROMPT_VERSION = f"corroboration_judge@{JUDGE_PROMPT.version}"


class JudgeOutput(BaseModel):
    label: Label


@dataclass(frozen=True)
class JudgeInput:
    player: PlayerRef
    anchor: Claim
    post: PostRef


class JudgeState(TypedDict):
    input: JudgeInput
    reply: StructuredReply[JudgeOutput] | None


def render_input(item: JudgeInput) -> str:
    team = f" ({item.player.team_name})" if item.player.team_name else ""
    anchor = item.anchor
    post = item.post
    lines = [
        f"Player: {item.player.web_name}{team}",
        "",
        "Anchor:",
        f"Event type: {anchor.event_type}",
        f"Certainty: {anchor.certainty}",
        f"Author: @{anchor.post.author_handle}",
        f"Posted at: {anchor.post.created_at.isoformat()}",
        anchor.post.text,
        "",
        "Post:",
        f"Author: @{post.author_handle}",
    ]
    if post.is_repost:
        original = f"@{post.reposted_author_handle}" if post.reposted_author_handle else "unknown"
        lines.append(f"Repost of: {original}")
    lines.extend([f"Posted at: {post.created_at.isoformat()}", post.text])
    return "\n".join(lines)


class Judge:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def run(self, item: JudgeInput) -> StructuredReply[JudgeOutput]:
        state: JudgeState = {"input": item, "reply": None}
        reply = self._graph.invoke(state)["reply"]
        assert reply is not None
        return reply


def build_judge(caller: StructuredCaller) -> Judge:
    def judge_node(state: JudgeState) -> dict[str, Any]:
        reply = caller.call_with_usage(JudgeOutput, JUDGE_PROMPT.text, render_input(state["input"]))
        return {"reply": reply}

    graph: StateGraph = StateGraph(JudgeState)
    graph.add_node("judge", judge_node)
    graph.set_entry_point("judge")
    graph.add_edge("judge", END)
    return Judge(graph.compile())
