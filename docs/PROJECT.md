# The Gaffer's Presser

## Problem

Polish FPL players in small private mini-leagues get two things badly today:

1. **Team news arrives too late.** The official FPL flags (`news`, `chance_of_playing_next_round`)
   lag behind reality and are imprecise (a blanket 75% after a press conference). The real
   signal — line-up leaks and injury news from well-connected X accounts — appears minutes
   before the deadline, scattered across many accounts, and nobody can watch them all.
2. **League banter has no memory.** Group chats forget who captained the blank, who took the
   -8 hit, who won the gameweek. Nothing turns a gameweek into a story in the language of the
   Polish FPL community.

The project has two goals of equal weight:

- **Portfolio (urgent):** a real, evaluated RAG + LLM-workflow system (retrieval, agents,
  evaluation, observability) the owner can show in recruitment interviews.
- **Hobby with a monetization hypothesis:** people might pay for fast, reliable team-news
  alerts. Wrapped would be free (virality); the league bot is for the owner and friends.

## Users and roles

| Role | What it does | What it cannot do |
|---|---|---|
| Owner (operator) | configures leagues and watched accounts, receives alerts and presser e-mails, forwards the presser to WhatsApp by hand | — |
| League member | reads the presser the owner forwards | interact with the bot (MVP: publish only) |
| Alert subscriber (future) | receives team-news alerts for their players | configure sources; exists only after multi-tenancy |

## Functional requirements

Grouped by roadmap stage (`docs/ROADMAP.md`).

**Stage 0 — FPL data collector**
- FR-0.1 Collect players, teams, fixtures and gameweeks from the FPL API.
- FR-0.2 Collect standings, managers, picks, transfers and chips for a configured list of
  classic leagues (several leagues, 5–15 managers each).
- FR-0.3 Snapshot time-sensitive state (player status, `news`, ownership) around each
  deadline — the FPL API keeps only the current value.
- FR-0.4 After each gameweek, collect the ground truth: who started, minutes, points.

**Stage 1 — Tweet ingest and extraction**
- FR-1.1 Ingest new posts from a configured list of X accounts through a swappable source.
- FR-1.2 Extract from each post, as a typed result: player(s), linked to FPL player IDs;
  event type; source account; the author's stated certainty.
- FR-1.3 Event types in scope: **out** (injury, illness, suspension), **doubt**,
  **benched**, **confirmed starter**. Nothing else (no positions, set pieces, prices).
- FR-1.4 Keep every post and extraction; they are the raw material for RAG and for scoring
  sources.

**Stage 2 — RAG v1 and alert e-mails**
- FR-2.1 Index posts for hybrid retrieval (full-text + vectors).
- FR-2.2 Corroborate a new leak against earlier posts about the same player and state how
  many independent accounts agree.
- FR-2.3 Before each deadline, e-mail alerts about players owned in the configured leagues
  and widely owned players; each alert cites its sources with links.
- FR-2.4 An evaluation set for extraction and retrieval, run on demand, with results
  recorded.

**Stage 3 — League presser**
- FR-3.1 After each gameweek, generate a presser per league in Polish FPL slang: summary,
  best manager of the gameweek (most net points after hits; a tie gives several winners),
  mild banter with a slight edge.
- FR-3.2 Deliver it by e-mail, ready to paste into WhatsApp.
- FR-3.3 Track the season race for the end-of-season top 3.

**Stage 4 — Source credibility and Q&A**
- FR-4.1 After each gameweek, settle every leak against the ground truth (FR-0.4) and keep
  a per-account accuracy score; show it in alerts.
- FR-4.2 Answer questions such as "what do we know about Palmer before this gameweek?"
  with cited sources (an agent choosing between SQL and retrieval).
- FR-4.3 Add Polish FPL podcast transcripts as a knowledge and slang source.

## Non-functional requirements

- **Latency:** in the final window before a deadline, a leak must reach the owner's inbox
  within **60 s** of being posted (p95), measured and reported. Outside that window a
  coarser polling interval is acceptable.
- **Budget:** external APIs ≤ **20 PLN/month** in total (LLM, X data, e-mail). Hosting on the
  owner's existing Railway account.
- **Language:** product content in Polish FPL slang; repository, code, docs in English.
- **Swappability:** the tweet source, the LLM provider and the e-mail provider are adapters
  behind interfaces; switching one is a configuration change.
- **Observability:** every LLM call is traced (inputs, outputs, tokens, cost, latency) from
  the first stage that calls an LLM.
- **Evaluation:** every LLM step that produces data has an evaluation set before its
  stage is done.
- **FPL API etiquette:** unofficial API — cache, back off, never poll faster than needed.
- **Privacy:** league IDs, manager names, e-mail addresses and credentials live only in
  environment variables or local config, never in the repository, logs, commits or tests.
  Tests use synthetic data.

## Architecture

```
            ┌─────────────────┐     ┌──────────────────────┐
FPL API ───►│ FPL collector   │────►│                      │
            └─────────────────┘     │  PostgreSQL 16       │
            ┌─────────────────┐     │  + pgvector          │
X (source ─►│ Tweet ingest    │────►│  (facts, posts,      │
 adapter)   └────────┬────────┘     │   embeddings)        │
                     ▼              │                      │
            ┌─────────────────┐     │                      │
            │ LangGraph flow: │◄───►│                      │
            │ extract → link →│     └──────────────────────┘
            │ retrieve →      │               ▲
            │ corroborate →   │               │
            │ decide          │     ┌─────────┴────────────┐
            └────────┬────────┘     │ Presser generator    │
                     ▼              │ (SQL facts + LLM)    │
              E-mail adapter ◄──────┴──────────────────────┘
                     │
                     ▼
               owner's inbox ──(copy-paste)──► WhatsApp
```

- **Facts are SQL, narrative is retrieval.** Points, standings and transfers are queried
  with SQL; posts, and later presser history and podcast transcripts, are retrieved.
- **Scheduling:** jobs driven by the FPL deadline calendar (tight polling only in the
  final window).
- Decisions and their rationale: `docs/DECISIONS.md` and `docs/adr/`.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Free X scrapers break or get the account banned | alerts stop | source adapter; a dedicated X account; fallback to a cheap paid scraper or the official pay-per-use API |
| X scraping is against X's terms of service | legal / blocking for a paid product | official API before any monetization |
| 60 s latency not achievable with polling | alerts lose value | measured in stage 1 before building on it |
| FPL API is unofficial and may change | collector breaks | caching, contract tests on recorded payloads |
| FPL data resets each season (July) | history lost | store everything we need; never depend on the API for past seasons |
| WhatsApp/Messenger have no official group-bot path | no direct delivery | MVP by e-mail + copy-paste; unofficial libraries later on a second number |
| "Fantasy Premier League" is a trademark | naming, monetization | no FPL/PL names or logos in branding |
| Chat members' personal data | GDPR | MVP does not read chats; consent before any feature that stores what members write |

## Out of scope

- Transfer, captaincy or points predictors — against the spirit of the game.
- Price-change alerts — the FPL site already shows them.
- Alerts based on the official FPL injury flags — too late and too imprecise.
- Discord and Telegram as channels — the owner's friends do not use them.
- Reading or replying in group chats in the MVP.
- Images and audio (TTS) in the MVP.
- AWS/Terraform infrastructure — already in the owner's portfolio; Railway is enough.
