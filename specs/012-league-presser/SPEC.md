---
status: done
stage_history:
  - "spec-draft — 2026-10-09"
  - "spec-ready — 2026-10-09"
  - "plan-draft — 2026-10-09"
  - "plan-approved — 2026-10-09"
  - "implemented — 2026-10-09"
  - "done — 2026-10-09"
metrics:
  started_at: 2026-10-09T13:06
  plan_steps: 21
  plan_changes: 8
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
  plan_review_blockers: 0
  plan_review_majors: 1
  implement_steps: 21
  implement_iterations: 6
  deviations_minor: 6
  deviations_major: 0
  final_review_blockers: 0
  final_review_worth_fixing: 8
  final_review_nits: 5
  findings_accepted: 13
  findings_rejected: 0
  cost_plan_cents: 300
  cost_plan_review_cents: 175
  cost_implement_cents: 805
  cost_final_review_cents: 880
  finished_at: 2026-10-09T14:34
---

# SPEC 012 — League presser

## Goal

After each gameweek, every configured classic league gets a "press conference": a short Polish
text in FPL slang that names the manager(s) of the gameweek, the flop of the gameweek, the
captaincy hits and misses, the bench, transfer and chip disasters and the season race, with mild
banter about FPL decisions. It reaches the owner by e-mail, with a "send to WhatsApp" button.
It works when, after a gameweek's league sync, the owner receives one presser per league whose
facts come from the database, and the evaluation tooling can measure a model's faithfulness to
the facts and the owner's rating of its style. Choosing the default model with that tooling
(faithfulness ≥ 0.95, style ≥ 3.5/5) is a follow-up run with the owner (BACKLOG #32).

## Context

- Stage 0 already collects everything a presser needs, per season and gameweek
  (`backend/app/fpl/models/leagues.py`, `reference.py`, `snapshots.py`): `league`, `manager`
  (`team_name`, `manager_name`), `league_membership`, `manager_gameweek` (`points`,
  `total_points`, `event_transfers_cost`, `points_on_bench`, `active_chip`, `has_team`),
  `manager_pick` (`multiplier`, `is_captain`, `is_vice_captain`), `manager_auto_sub`,
  `manager_transfer`, `manager_chip` and per-player points in `player_gameweek_result`.
  `league_standing` holds only the table at the latest sync (DECISIONS 2026-09-26), so the
  table after each gameweek comes from `manager_gameweek.total_points` of the league's members.
- The worker (`backend/app/worker/`) runs the results sync and then the league sync once a
  gameweek is `finished` and `data_checked` (DECISIONS 2026-09-27); spec 002 left "triggering
  the presser after the league sync" to Stage 3.
- Delivery (`backend/app/delivery/`, spec 008) sends a message (title, plain text, optional
  HTML) with a caller-given idempotency key and a kind (`alert | presser | test`), keeps the
  full message in `delivery_log` and never sends one key twice. The recipient is the owner
  (`DELIVERY_EMAIL_TO`).
- Alert e-mails carry a "send to WhatsApp" button built from a `wa.me` link
  (`backend/app/alerts/render.py`, DECISIONS 2026-10-08); the presser reuses the idea.
- LLM code shared by AI features lives in `backend/app/llm/` (the OpenRouter chat-model
  factory, `model_settings.toml`, `prices.toml`, structured calls with usage and cost, tracing
  to Langfuse); a model outside `model_settings.toml` and `prices.toml` cannot be used.
- Polish product content lives in `backend/app/content/` (CLAUDE.md iron rule; today alert
  and delivery templates in TOML and prompts in `prompts/*.md`).
- The local development database holds GW1–5 of season 2026-27 for two leagues (5 and 22
  managers), with chips (bench boost, triple captain, free hit, wildcard) and 341 transfers —
  enough to build the evaluation set before GW6.
- Model prices on OpenRouter checked 2026-10-09 (per million tokens, input/output):
  `openai/gpt-6-luna` $0.10/$0.50, `anthropic/claude-haiku-5.5` $0.10/$0.50,
  `deepseek/deepseek-v4.1-flash` $0.30/$1.20, `google/gemini-3.8-flash` $0.75/$3.75,
  `mistralai/mistral-large-4-0` $0.68/$2.09, `openai/gpt-6.1-sol` $2.00/$10.00. A presser is
  roughly 6k input and 1k output tokens, so even the most expensive candidate costs about
  $0.01 per presser (2 leagues × 38 gameweeks ≈ $0.8 a season).
- The owner approved starting Stage 3 before Stage 2 closes: the remaining Stage 2 items (the
  LLM-written alert summary, the retrieval evaluation report) wait for production and the GW6
  data (conversation with the owner, 2026-10-08; recorded in DECISIONS).

## Read context

- `docs/ROADMAP.md` — read in full: Stage 3 item 1 is this spec (summary, best manager of the
  gameweek by net points with ties, banter, e-mail); item 2 (slang glossary and style examples
  from X and podcasts) stays a separate spec — this one ships only a starter glossary; the
  "one stage at a time" rule gets the owner's exception above; the definition of done for an
  LLM stage (Langfuse traces, an evaluation set with recorded results) applies.
- `docs/PROJECT.md` — read in full: FR-3.1 (presser per league after each gameweek, manager of
  the gameweek = most net points after hits, a tie gives several winners, mild banter with a
  slight edge), FR-3.2 (by e-mail, ready to paste into WhatsApp), FR-3.3 (season race for the
  end-of-season top 3); non-functional: 20 PLN/month budget, Polish product content,
  observability, evaluation before the stage is done, privacy (manager and team names may go
  to the LLM provider and Langfuse, never into logs or the repository; tests use synthetic
  managers and leagues), times shown in `Europe/Warsaw`; architecture: "facts are SQL,
  narrative is retrieval", the presser generator = SQL facts + LLM.
- `docs/DECISIONS.md` — read in full: OpenRouter as the only provider and models only from
  `model_settings.toml` / `prices.toml` (2026-09-29); shared LLM code in `app/llm/`
  (2026-09-30); evaluation sets in the repository as JSONL split dev / test, judge
  pre-labelled by another vendor a tier above and reviewed by the owner (2026-09-28,
  2026-09-30); unit tests with a fake LLM, evaluations outside `pytest` (2026-09-26); delivery
  idempotency key and log (2026-10-01); WhatsApp button (2026-10-08); worker schedule and
  league sync after `data_checked` (2026-09-27); a refactor a feature needs is done in that
  feature's spec (2026-09-30); module per business area (`app/presser`, 2026-09-26/27).
- `docs/BACKLOG.md` — searched for "presser", "slang", "WhatsApp", "images": #5 (images and
  TTS presser, P3, after the text presser is in regular use) and #25 (automatic WhatsApp
  posting) stay out of scope.
- `docs/CONVENTIONS.md` — searched for "content", "Polish", "test": product content in Polish
  in `backend/app/content/`, one file per purpose; the rest in English.
- `docs/DEPLOYMENT.md` — searched for "environment", "variable": new variables are documented
  in the runbook and `backend/.env.example`.

## Scope

- **Fact sheet.** For a league and a finished gameweek, a deterministic fact sheet computed
  with SQL over the league's members with a team that gameweek:
  1. Manager(s) of the gameweek: the highest net points (`points − event_transfers_cost`);
     every manager on the top score is a winner.
  2. Flop of the gameweek: the lowest net points (ties → several).
  3. Captaincy: each manager's captain and the points he brought (with the multiplier; triple
     captain marked), the vice-captain stepping in when the captain did not play; the best and
     the worst captain of the league.
  4. Bench, transfers and chips: the most points left on the bench; hits taken
     (`event_transfers_cost > 0`); the gameweek's transfers whose incoming player scored less
     than the outgoing one, by the difference; chips played and what they gave (bench boost:
     bench points; triple captain: the captain's extra points; free hit / wildcard: the
     gameweek's points against the league average); automatic substitutions.
  5. Table and season race: the league table after the gameweek from `total_points`, each
     manager's movement since the previous gameweek, the top 3 and the gaps between them, the
     biggest climber and faller.
  Plus season facts computed with SQL over the season so far: each manager's number of
  gameweek wins and flops, current streaks (wins, flops, captain blanks), and records (the
  season's best and worst gameweek score in the league). The fact sheet also carries the league
  average of the gameweek. A section with nothing notable is marked empty. The code ranks the
  items in each section, so the writer can pick the most notable ones in a 22-manager league.
- **Names.** Managers appear under a nickname from local configuration (`PRESSER_NICKNAMES`,
  a mapping of FPL entry ID to nickname, never in the repository) or, without one, the first
  word of their FPL `manager_name`.
- **Writer.** An LLM step that turns the fact sheet into the presser: Polish, FPL slang, mild
  banter with a slight edge about FPL decisions only (captain, transfers, bench, chips, hits),
  never about personal traits, no vulgar words; at most 1500 characters; it covers the
  non-empty sections in the order above. Its input is the fact sheet, the starter slang
  glossary and style examples, and the league's previous 2 pressers (fewer at the start of
  the season): running jokes may continue, the same joke is not repeated, and facts stated in
  a previous presser may be recalled. The prompt lives in `backend/app/content/prompts/`.
  Every writer call is traced in Langfuse with tokens, cost and latency.
- **Starter slang glossary and style examples** in `backend/app/content/`
  (`presser_glossary.toml`, 70 expressions with meaning, example and source;
  `presser_style_examples.md`, 3 example pressers): compiled from public Polish FPL sources
  on the web, mainly the fantasypl.pl blog (read through the Wayback Machine), whose language
  the owner wants the presser to follow; the examples are written for the project (no copied
  third-party text); both reviewed and approved by the owner on 2026-10-09 and committed with
  this SPEC. The writer uses them as given; changing them is the owner's call.
- **Presser log.** A new table keeps every generated presser: league, season, gameweek, the
  fact sheet, the text, the model, tokens, cost, latency, the status (`generated`, `sent`,
  `failed`) and the delivery idempotency key; the writer's "previous pressers" are the
  league's latest sent pressers before the gameweek.
- **Worker trigger.** After a gameweek's league sync succeeds, the worker generates and sends
  one presser per configured league for that gameweek, only for the latest finished gameweek
  (a catch-up after downtime does not send pressers for older gameweeks). The delivery
  idempotency key is per season, gameweek and league, so a restart never sends a presser twice.
  A failed generation is retried in-call (the shared retry helper), then recorded as `failed`
  and not retried by the worker. The presser runs when `PRESSER_ENABLED` is true, the
  OpenRouter key is set and delivery is enabled; otherwise the worker skips it with one log
  line, like alerts.
- **E-mail.** Title `Presser GW<N> — <league name>`; the plain-text part is the presser;
  an HTML part shows the presser and a "send to WhatsApp" button with the presser as the
  `wa.me` text. The e-mail template lives in `backend/app/content/`.
- **CLI** (`python -m app.presser`): `facts` (print the fact sheet of a league and gameweek),
  `preview` (generate and print without sending or storing as sent), `send` (generate and
  send for a given league and gameweek, by hand, also for an older gameweek, with the same
  idempotency key), `status` (latest presser per league, failures).
- **Evaluation.**
  - The set lives in the repository as JSONL: one case per (league, gameweek) from the
    owner's GW1–5 (10 cases) with every manager, team and league name replaced by a
    synthetic one, plus about 6 synthetic edge cases (a tie for the win, a 1-point captain,
    every manager negative after hits, a chip that flopped, the first gameweek of the season
    with no history, a manager without a team); split dev (prompt work) / test (model
    comparison). Each case holds the fact sheet and its previous pressers as frozen text.
  - Faithfulness: a judge model reads the fact sheet, the previous pressers and the presser,
    lists every factual claim (numbers, who won, who captained whom, streaks, table
    positions) and labels each `supported` (by the fact sheet or a given previous presser) or
    `unsupported`; faithfulness = supported / all claims per presser, averaged. The judge is
    `openai/gpt-6.1-sol`; a judge-review command takes the owner's verdict on each labelled
    claim and reports the agreement with the judge.
  - Style: an interactive review CLI shows each presser and takes the owner's rating 1–5 and
    an optional note; ratings are stored in the repository next to the run results.
  - Also measured: length (share within 1500 characters), cost and latency per presser.
  - Candidates: `openai/gpt-6-luna`, `anthropic/claude-haiku-5.5`,
    `deepseek/deepseek-v4.1-flash`, `google/gemini-3.8-flash`, `mistralai/mistral-large-4-0`,
    each added with the judge to `model_settings.toml` and `prices.toml`. The pass rule, applied
    by the summary command: faithfulness ≥ 0.95 and an average style rating ≥ 3.5 on the test
    split; the cheapest passing model wins.
  - The comparison run itself (paid calls, the owner's style ratings and judge review, the
    report under `docs/reports/`, the ADR and the new default) is a follow-up with the owner,
    BACKLOG #32. Until then `PRESSER_MODEL` defaults to `openai/gpt-6-luna`, the extraction
    default (ADR 0006).

## Out of scope

- The full slang glossary and style examples collected from X posts and podcast transcripts
  — ROADMAP Stage 3 item 2, its own spec.
- Sending to league members directly or posting to WhatsApp — BACKLOG #25 (P3, a second
  number).
- Images or audio — BACKLOG #5 (P3).
- Retrieval over past pressers or tweets for the presser (only the last 2 pressers are given
  as text) — a future spec if running jokes need a longer memory.
- A judge running in production on every presser — the owner reads every presser before
  forwarding it; a production guard is a future spec if the evaluation shows a need.
- The model comparison run, its report, the ADR and the new `PRESSER_MODEL` default — BACKLOG
  #32 (P1), with the owner after this spec is merged: it needs paid calls and the owner's
  style ratings, which an agent cannot provide.
- End-of-season awards (the final top 3 ceremony) — a future spec before GW38.
- H2H leagues, cup and the overall rank — not configured leagues.

## Requirements and acceptance criteria

- [ ] AC1: For a synthetic league and gameweek in the test database, `facts` returns the
  manager(s) of the gameweek by net points; with two managers on the same top net score both
  are winners; a manager with `has_team = false` is in no section.
- [ ] AC2: The fact sheet gives each manager's captain points with the multiplier, marks a
  triple captain, and credits the vice-captain when the captain played 0 minutes.
- [ ] AC3: The fact sheet lists the gameweek's transfers whose incoming player scored less
  than the outgoing one, ordered by the difference, and every hit with its cost.
- [ ] AC4: The fact sheet gives each chip played with its effect (bench boost bench points,
  triple captain extra points, free hit / wildcard points against the league average).
- [ ] AC5: The table after gameweek N is computed from `total_points` at N, with each
  manager's movement since N−1 (no movement at the season's first gameweek), the top 3 with
  gaps, and the biggest climber and faller.
- [ ] AC6: Season facts give each manager's gameweek wins and flops so far and current streaks
  of wins, flops and captain blanks (a captain blank = captain points ≤ 2 before the
  multiplier).
- [ ] AC7: A manager with an entry in `PRESSER_NICKNAMES` appears under the nickname everywhere
  in the fact sheet and the writer's input; without one, under the first word of
  `manager_name`; an invalid `PRESSER_NICKNAMES` stops the worker and the CLI at start with a
  message naming the variable, never its value.
- [ ] AC8: The writer's input holds the fact sheet, the glossary, the style examples and the
  league's latest sent pressers before the gameweek (at most 2); with a fake LLM, the stored
  presser holds the text, the model, tokens, cost and latency, and the Langfuse trace is
  created.
- [ ] AC9: When the worker finishes the league sync of the latest finished gameweek, it sends
  one presser per configured league with the key `presser:<season>:gw<N>:league<id>`; a
  restart after that sends nothing; a league sync of an older gameweek during a catch-up
  sends nothing.
- [ ] AC10: A writer call that fails after the retries records the presser as `failed`, logs
  the error class without manager names or text, and sends nothing; the worker carries on
  with the next league and does not retry it later.
- [ ] AC11: With `PRESSER_ENABLED=false`, no OpenRouter key or delivery disabled, the worker
  skips the presser with one log line and `status` says why.
- [ ] AC12: The e-mail's title is `Presser GW<N> — <league name>`, the plain-text part is the
  presser, and the HTML part has a "send to WhatsApp" `wa.me` button carrying the presser.
- [ ] AC13: `preview` prints a presser without sending it or storing it as sent; `send` for an
  older gameweek sends it with the same key format, and a second `send` reports
  `already_sent`.
- [ ] AC14: Application logs never contain manager names, team names, nicknames or the
  presser text (a test captures the logs of a full run).
- [ ] AC15: The prompt, the glossary, the style examples and the e-mail template live in
  `backend/app/content/`; no Polish product text is a string literal in code.
- [ ] AC16: The evaluation set holds the 10 real cases (pseudonymised: no real manager, team or
  league name from the owner's leagues in the repository) and about 6 synthetic edge cases,
  split dev / test; a test checks that every case's fact sheet validates.
- [ ] AC17: The evaluation command runs a model on a split and writes per-presser
  faithfulness with the labelled claims, length, cost and latency; the review command records
  the owner's 1–5 style rating and note; a summary command reports per model the averages and
  whether the thresholds pass.
- [ ] AC18: The judge-review command records the owner's verdict on each labelled claim of a
  run's pressers and reports the agreement with the judge (tested on a recorded run with a fake
  judge).
- [ ] AC19: `PRESSER_MODEL` defaults to `openai/gpt-6-luna`; the five candidates and the judge
  are in `model_settings.toml` and `prices.toml` with the prices checked on 2026-10-09; the
  summary command applies the pass rule (faithfulness ≥ 0.95, style ≥ 3.5, the cheapest
  passing model) to recorded results.
- [ ] AC20: `backend/.env.example`, `docs/DEPLOYMENT.md` and the README document
  `PRESSER_ENABLED`, `PRESSER_MODEL` and `PRESSER_NICKNAMES` and the CLI.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Facts are computed by SQL into a fact sheet; the LLM only writes the text from it | the LLM reading raw tables or querying the database itself | LLMs miscount and invent numbers; a wrong winner kills the joke; the fact sheet is testable without an LLM (PROJECT: facts are SQL) |
| The code ranks the notable items per section; the writer picks among them | the code choosing exactly what to mention; the writer seeing only raw rows | a 22-manager league needs selection; ranking is deterministic and testable, the choice of the funniest is the writer's job |
| Five fixed sections, at most 1500 characters | a short 600-character version; a line for every manager | one WhatsApp screen; the structure keeps the pressers comparable for evaluation (the owner) |
| Managers by a local nickname, falling back to the first name from FPL | first name only; team name | banter works with the names friends use; nicknames stay out of the repository (the owner) |
| The previous 2 pressers are context, and facts stated in them may be recalled | facts only from the current fact sheet; no history | running jokes and continuity (the owner, accepting that an error in a sent presser can be repeated later) |
| Season facts (wins, flops, streaks, records) computed by SQL | the writer inferring the season from previous pressers | the "memory" of the league that does not depend on earlier texts being right |
| Only the latest finished gameweek is sent automatically; older ones by `send` | a presser for every gameweek missed during downtime | a two-week-old presser is not funny (the owner) |
| A presser table with the fact sheet and text | reading previous pressers from `delivery_log` | the fact sheet, model and cost per presser are needed for the history and the evaluation; a migration accepted by the owner |
| Faithfulness by an LLM judge (`openai/gpt-6.1-sol`) claim by claim, style by the owner's 1–5 rating | judge only; owner only; a judge for style | an automatic, repeatable faithfulness number to compare models, checked against the owner on a sample; humour cannot be judged automatically (the owner) |
| Five candidates incl. the newest cheap models; the cheapest passing faithfulness ≥ 0.95 and style ≥ 3.5 wins | the extraction model without comparison; quality first regardless of cost | the presser costs cents a season, but the extraction model was never tested on Polish writing (the owner) |
| This spec builds the evaluation tooling with `openai/gpt-6-luna` as the provisional default; the comparison run is a follow-up with the owner (BACKLOG #32) | the comparison inside this spec | the run needs about $3–6 of paid calls and about 40 style ratings from the owner, which the autonomous pipeline cannot do; the presser works from the merge on (the owner, as with specs 004 and 005) |
| Evaluation set: 10 real gameweeks pseudonymised plus ~6 synthetic edge cases | real cases only; waiting for GW8–10 | 10 cases miss ties, all-negative weeks and the first gameweek; waiting delays the stage (the owner) |
| A starter glossary compiled from public web sources, approved by the owner, before implementation | the owner writing it; no glossary; the full X/podcast glossary first | without examples models write stiff Polish; the full collection is its own roadmap item (the owner) |
| No vulgar words; jokes only about FPL decisions | light swearing; anything goes | the text goes to a group chat and models overshoot when allowed to swear (the owner) |

## Owner decisions

- Starting Stage 3 before Stage 2 is finished — approved (2026-10-08); recorded in
  `docs/DECISIONS.md`.
- A new table for the presser log (Alembic migration) — accepted up front.
- No new dependencies are expected; a new one still needs the owner's approval.
- New models in `model_settings.toml` and `prices.toml` for the five candidates and the judge
  — accepted up front.
- The starter glossary and style examples are prepared from web sources and approved by the
  owner (2026-10-09); the presser's language follows the fantasypl.pl blog; the league says
  "autosub", never "autozmiana".
- Facts from the previous 2 pressers may be recalled by the writer; the risk of repeating an
  error is accepted.

## Open questions (non-blocking)

- none
