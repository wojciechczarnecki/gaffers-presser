from collections.abc import Callable

from app.corroboration.judge import JudgeInput, JudgeOutput
from app.llm.structured import StructuredReply, Usage


class ScriptedJudge:
    """A judge whose label comes from a function of the input; `model` is what it reports."""

    def __init__(
        self,
        label: Callable[[JudgeInput], str] | str = "supports",
        model: str = "fake/pre-labeller",
        cost_usd: float | None = 0.001,
    ) -> None:
        self._label = label
        self.model = model
        self.cost_usd = cost_usd
        self.inputs: list[JudgeInput] = []

    def run(self, item: JudgeInput) -> StructuredReply[JudgeOutput]:
        self.inputs.append(item)
        label = self._label(item) if callable(self._label) else self._label
        if isinstance(label, Exception):
            raise label
        return StructuredReply(
            parsed=JudgeOutput(label=label),
            usage=Usage(input_tokens=10, output_tokens=5),
            cost_usd=self.cost_usd,
            answered_model=self.model,
        )
