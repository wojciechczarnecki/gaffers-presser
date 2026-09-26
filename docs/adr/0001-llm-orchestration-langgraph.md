# 0001 — LLM orchestration: LangGraph

Status: accepted (2026-09-26)

## Context

The alert pipeline is a workflow with branches: post → extract → link to FPL players →
retrieve earlier posts → corroborate → decide → notify. Later, the Q&A agent (stage 4) chooses
tools (SQL or retrieval). Individual LLM steps must return typed, validated data. The
framework should also be one that the job market recognises.

## Options

| | LangGraph | PydanticAI | Plain SDK + own code |
|---|---|---|---|
| Model | explicit state graph: nodes, edges, conditions; an agent is one pattern | typed agent: dependencies, tools, validated output; graphs via `pydantic-graph` | functions |
| Strengths | branching and loops made explicit, checkpoints (resume after failure), human-in-the-loop, ecosystem | end-to-end typing, dependency injection, tests without a real LLM (`TestModel`, `FunctionModel`) | no abstraction cost |
| Weaknesses | heavier, looser typing of state, LangChain abstractions underneath | less recognised in job ads, younger graph support | everything by hand; weaker portfolio signal |
| Observability | Langfuse, LangSmith | Langfuse, Logfire (OpenTelemetry) | own |

## Decision

LangGraph orchestrates the flows. LLM steps return typed results through
`with_structured_output` with Pydantic models. One framework only at the start.

## Consequences

- The graph structure of the pipeline stays visible in code and traces.
- Typed outputs are still Pydantic models, so a later move of single steps to PydanticAI stays
  cheap.
- PydanticAI is compared on a real flow when the Q&A agent is built (BACKLOG #1).
