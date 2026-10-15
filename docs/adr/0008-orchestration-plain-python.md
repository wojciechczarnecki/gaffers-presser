# 0008 — Orchestration in plain Python, LangGraph for the LLM steps

Status: accepted (2026-10-15); supersedes the consequence of
[0001](0001-llm-orchestration-langgraph.md) that "the graph structure of the pipeline stays
visible in code and traces"

## Context

ADR 0001 chose LangGraph for a pipeline it expected to branch: post → extract → link →
retrieve → corroborate → decide → notify. What was built by spec 015 differs:

- Extraction is a two-node graph (`extract → link`, `backend/app/extraction/flow.py`); the
  corroboration judge and the presser writer are one-node graphs over the shared structured
  caller.
- Retrieval, corroboration and alerts are plain Python. An alert slot lists the players,
  corroborates them in a thread pool (`CORROBORATION_WORKERS`), renders a template and sends it.
- Recovery after a crash comes from the database, not from a checkpointer: the alert log is the
  alerts' only state and the delivery idempotency key prevents a second send
  (DECISIONS 2026-10-01, 2026-10-02).

The code audit of October 2026 found README, PROJECT and ADR 0001 describing a graph
pipeline that does not exist ([report](../reports/code-audit-2026-10.md)).

## Options

| | A: plain Python, graphs for LLM steps | B: corroboration as a graph | C: the alert pipeline as a graph |
|---|---|---|---|
| Shape | as built | retrieve → judge fan-out (`Send`) → count → grade | every step a node, a checkpointer for recovery |
| Gains | nothing to change; one recovery mechanism | parallel judge calls (BACKLOG #33); node-level traces | the pipeline drawn as a graph |
| Costs | the README cannot show a pipeline graph | M; the behaviour must stay identical while specs 016 and 017 measure corroboration | L; a checkpointer duplicates the database's idempotency |
| Uses what makes a graph pay (branches, loops, checkpoints, interrupts) | — | no: the topology is fixed | checkpoints only, already covered |

## Decision

A. LangGraph wraps the LLM steps, with typed Pydantic outputs as ADR 0001 decided; the
orchestration around them stays plain Python with its recovery in the database. A real graph is
kept for the Stage 4 Q&A agent, whose routing between SQL and retrieval runs in a tool loop;
there BACKLOG #1 compares PydanticAI on the same flow. B is deferred (BACKLOG #49).

## Consequences

- Waiting does not make B dearer: corroboration is already split into `sources`, `judge`,
  `rules` and `service`, so a graph stays a mechanical change, and a safer one once specs 016
  and 017 give a baseline to check it against.
- The parallel judge calls of BACKLOG #33 can come from a second thread pool or from B; the
  choice is made when #33 returns.
- README and PROJECT describe the orchestration as built.
