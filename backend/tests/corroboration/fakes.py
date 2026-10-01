from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


class _Handle:
    def __init__(self, record: dict[str, Any], trace_id: str) -> None:
        self._record = record
        self.trace_id = trace_id

    def update(self, **kwargs: Any) -> None:
        self._record.setdefault("updates", []).append(kwargs)


class _Observation:
    def __init__(self, record: dict[str, Any]) -> None:
        self._record = record

    def end(self) -> None:
        self._record["ended"] = True


@dataclass
class FakeLangfuseClient:
    """Records the current-span stack so a test can see which root an observation nests under."""

    fail: bool = False
    trace_id: str = "trace-1"
    observations: list[dict[str, Any]] = field(default_factory=list)
    stack: list[str] = field(default_factory=list)
    flushed: int = 0

    def _record(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self.fail:
            raise ConnectionError("langfuse down")
        record = {**kwargs, "parent": self.stack[-1] if self.stack else None}
        self.observations.append(record)
        return record

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any) -> Iterator[_Handle]:
        record = self._record(kwargs)
        self.stack.append(kwargs["name"])
        try:
            yield _Handle(record, self.trace_id)
        finally:
            self.stack.pop()
            record["ended"] = True

    def start_observation(self, **kwargs: Any) -> _Observation:
        return _Observation(self._record(kwargs))

    def flush(self) -> None:
        if self.fail:
            raise ConnectionError("langfuse down")
        self.flushed += 1

    def named(self, name: str) -> list[dict[str, Any]]:
        return [o for o in self.observations if o["name"] == name]


class RecordingCorroborationTracer:
    def __init__(self) -> None:
        from tests.retrieval.fakes import RecordingTracer

        self.spans: list[dict[str, Any]] = []
        self.generations: list[dict[str, Any]] = []
        self.retrieval_tracer = RecordingTracer()
        self.flushed = 0

    @contextmanager
    def span(self, name: str, input: dict[str, Any]) -> Iterator[Any]:
        record: dict[str, Any] = {"name": name, "input": input, "outputs": []}
        self.spans.append(record)

        class Handle:
            trace_id = "trace-rec"

            @staticmethod
            def update(*, output: Any) -> None:
                record["outputs"].append(output)

        yield Handle()

    def generation(self, **kwargs: Any) -> None:
        self.generations.append(kwargs)

    def retrieval(self) -> Any:
        return self.retrieval_tracer

    def flush(self) -> None:
        self.flushed += 1
