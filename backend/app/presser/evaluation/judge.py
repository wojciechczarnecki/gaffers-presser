from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from app.content import load_prompt
from app.llm.structured import StructuredCaller, StructuredReply
from app.presser.facts import FactSheet
from app.presser.writer import NO_PREVIOUS, PreviousPresser

JUDGE_MODEL = "openai/gpt-6.1-sol"
JUDGE_PROMPT = load_prompt("presser_judge")
PROMPT_VERSION = f"presser_judge@{JUDGE_PROMPT.version}"


class JudgedClaim(BaseModel):
    claim: str
    label: Literal["supported", "unsupported"]


class JudgeVerdict(BaseModel):
    claims: list[JudgedClaim]


@dataclass(frozen=True)
class JudgeInput:
    facts: FactSheet
    previous: list[PreviousPresser]
    text: str


class JudgeState(TypedDict):
    input: JudgeInput
    reply: StructuredReply[JudgeVerdict] | None


def render_input(item: JudgeInput) -> str:
    previous = (
        "\n\n".join(f"GW{entry.gameweek}:\n{entry.text}" for entry in item.previous)
        if item.previous
        else NO_PREVIOUS
    )
    return "\n\n".join(
        [
            f"# Fact sheet\n{item.facts.model_dump_json(indent=1)}",
            f"# Previous pressers\n{previous}",
            f"# Presser to check\n{item.text}",
        ]
    )


class PresserJudge:
    def __init__(self, graph: Any, model: str) -> None:
        self._graph = graph
        self.model = model

    def run(self, item: JudgeInput) -> StructuredReply[JudgeVerdict]:
        state: JudgeState = {"input": item, "reply": None}
        reply = self._graph.invoke(state)["reply"]
        assert reply is not None
        return reply


def build_presser_judge(caller: StructuredCaller) -> PresserJudge:
    def judge_node(state: JudgeState) -> dict[str, Any]:
        reply = caller.call_with_usage(
            JudgeVerdict, JUDGE_PROMPT.text, render_input(state["input"])
        )
        return {"reply": reply}

    graph: StateGraph = StateGraph(JudgeState)
    graph.add_node("judge", judge_node)
    graph.set_entry_point("judge")
    graph.add_edge("judge", END)
    return PresserJudge(graph.compile(), caller.model)
