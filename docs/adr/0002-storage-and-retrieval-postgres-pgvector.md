# 0002 — Storage and retrieval: PostgreSQL with pgvector, hybrid search

Status: accepted (2026-09-26)

## Context

The system holds structured facts (players, picks, points, standings) and text (posts, later
presser history and podcast transcripts). Facts answer "how many", text answers "what was
said". Leaks mention player names, so exact keyword matches matter as much as meaning.
Budget and operations favour one database.

## Options

- **PostgreSQL + pgvector**: one database for facts and vectors; full-text search built in.
- **Dedicated vector database** (Qdrant, Pinecone, Weaviate): stronger vector features,
  a second system to run and pay for.
- **A framework's retriever** (LangChain, LlamaIndex): fastest start, hides how retrieval
  works.

## Decision

PostgreSQL 16 with pgvector. Retrieval is hybrid and our own: full-text search (`tsvector`)
and vector similarity, merged with reciprocal rank fusion, optional reranking on top. Facts
are queried with SQL and never retrieved by embedding.

## Consequences

- One database to host, back up and migrate (Alembic).
- The retrieval code is ours to test and evaluate — and to explain in interviews.
- A dedicated vector database is reconsidered only if pgvector becomes the bottleneck.
