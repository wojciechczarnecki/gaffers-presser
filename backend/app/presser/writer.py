import tomllib
from dataclasses import dataclass
from functools import cache
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, StringConstraints

from app.content import PROMPTS_DIR, load_prompt
from app.llm.structured import StructuredCaller, StructuredReply
from app.presser.facts import FactSheet

CONTENT_DIR = PROMPTS_DIR.parent
WRITER_PROMPT = load_prompt("presser_writer")
PROMPT_VERSION = f"presser_writer@{WRITER_PROMPT.version}"
NO_PREVIOUS = "none"


class PresserDraft(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@dataclass(frozen=True)
class PreviousPresser:
    gameweek: int
    text: str


@dataclass(frozen=True)
class WriterInput:
    facts: FactSheet
    previous: list[PreviousPresser]


class WriterState(TypedDict):
    input: WriterInput
    reply: StructuredReply[PresserDraft] | None


@cache
def load_glossary() -> str:
    data = tomllib.loads((CONTENT_DIR / "presser_glossary.toml").read_text(encoding="utf-8"))
    return "\n".join(
        f"{entry['term']} — {entry['meaning']} — {entry['example']}" for entry in data["term"]
    )


@cache
def load_style_examples() -> str:
    return (CONTENT_DIR / "presser_style_examples.md").read_text(encoding="utf-8").strip()


def human_message(item: WriterInput) -> str:
    return render_input(item, load_glossary(), load_style_examples())


def render_input(item: WriterInput, glossary: str, style_examples: str) -> str:
    previous = (
        "\n\n".join(f"GW{entry.gameweek}:\n{entry.text}" for entry in item.previous)
        if item.previous
        else NO_PREVIOUS
    )
    return "\n\n".join(
        [
            f"# Fact sheet\n{item.facts.model_dump_json(indent=1)}",
            f"# Glossary\n{glossary}",
            f"# Style examples\n{style_examples}",
            f"# Previous pressers\n{previous}",
        ]
    )


class Writer:
    def __init__(self, graph: Any, model: str) -> None:
        self._graph = graph
        self.model = model

    def run(self, item: WriterInput) -> StructuredReply[PresserDraft]:
        state: WriterState = {"input": item, "reply": None}
        reply = self._graph.invoke(state)["reply"]
        assert reply is not None
        return reply


def build_writer(caller: StructuredCaller) -> Writer:
    def write_node(state: WriterState) -> dict[str, Any]:
        human = human_message(state["input"])
        return {"reply": caller.call_with_usage(PresserDraft, WRITER_PROMPT.text, human)}

    graph: StateGraph = StateGraph(WriterState)
    graph.add_node("write", write_node)
    graph.set_entry_point("write")
    graph.add_edge("write", END)
    return Writer(graph.compile(), caller.model)
