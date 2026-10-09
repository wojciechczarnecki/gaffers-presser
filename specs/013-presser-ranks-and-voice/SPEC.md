---
status: spec-ready
stage_history:
  - "spec-draft — 2026-10-09"
  - "spec-ready — 2026-10-09"
---

# SPEC 013 — Presser ranks and natural voice

## Goal

The presser from spec 012 works, but the owner's reading of the first real texts found two
gaps. The presser ignores FPL ranks: 60 points in a weak gameweek can beat 120 in a strong one,
and only the gameweek rank (GW rank) shows that. It also ignores overall-rank moves such as
entering the top 10k. And every presser reads alike, because the glossary's slang is forced
into every sentence and phrases are copied from the style examples. It works when the fact
sheet carries each manager's GW rank and overall-rank movements, the season's best and worst
gameweeks are judged by GW rank, a sixth "Overall" section reports notable overall-rank moves,
and the writer uses slang only where it sounds natural and never copies the examples' phrases.
The evaluation tooling also counts inflection errors, so the model comparison (BACKLOG #32) can
decide the name-inflection problem.

## Context

- Spec 012 (`specs/012-league-presser/SPEC.md`, merged as #22) built the presser: the fact
  sheet (`backend/app/presser/facts/`, `FactSheet` version 1 in `schema.py`, season facts in
  `season.py`), the writer (`backend/app/presser/writer.py`, one LangGraph node, the prompt
  `backend/app/content/prompts/presser_writer.md` version 2), the style examples
  (`presser_style_examples.md`), the 70-term glossary (`presser_glossary.toml`), the presser
  log, the worker trigger, the e-mail and the evaluation (`backend/app/presser/evaluation/`,
  set v1 in `backend/evals/presser/`).
- `manager_gameweek` already stores `overall_rank` per manager and gameweek
  (`backend/app/fpl/models/leagues.py`), but not the GW rank. The FPL API returns it as `rank`
  both in `entry/{id}/event/{gw}/picks/` → `entry_history` and in `entry/{id}/history/` →
  `current[]`. The league sync calls both endpoints for every manager
  (`backend/app/fpl/leagues.py`), and `EntryHistory` in `backend/app/fpl/schemas.py` ignores
  the field (`extra="ignore"`). Checked on the live API on 2026-10-09: `current[]` holds `rank`,
  `overall_rank` and `percentile_rank` for every gameweek. A league sync of past gameweeks
  re-fetches them, so the local GW1–5 data can be filled by a manual league sync. Production is
  not deployed yet, and its first-deploy catch-up (DEPLOYMENT step 6) syncs every finished
  gameweek.
- Whether FPL ranks a gameweek on points before or after hits could not be settled from one
  live example. The design does not depend on it: the manager of the gameweek stays the most
  net points (FR-3.1), and the GW rank is FPL's number passed on as is.
- Diagnosis of the 25 real pressers in the local database (prompt versions 1 and 2,
  `openai/gpt-6-luna`):
  - Repetition comes from copying. GW1 has "odskok od peletonu" verbatim from style example 3,
    "zielona strzałka" and "transfer tygodnia w złą stronę" come from examples 1–2, and
    "peleton" appears in most texts.
  - The prompt says "write in the FPL slang of the glossary" and passes all 70 terms, so slang
    is forced in. The owner's diagnosis: the headers are fine, the content is too similar
    because glossary words are pushed in.
  - Every winner and flop line reads "X punktów netto, Y ponad średnią".
  - Inflection errors occur, e.g. "Free Hit kacpra", "Wildcard Jasiek", "u Jasieka",
    "z Calvert-Lewinem" next to "z Calvertem-Lewinem".
- The chat factory sends `temperature = 0` to every model that accepts a temperature
  (`backend/app/llm/chat.py`). The default writer model `openai/gpt-6-luna` accepts none
  (`temperature = false` in `backend/app/llm/model_settings.toml`), so a writer temperature
  takes effect only on models that accept one.

## Read context

- `docs/ROADMAP.md` — read in full: Stage 3 item 1 (the presser, spec 012) is ticked; this spec
  refines it and gets a link at that item. Item 2 (glossary and style examples from X and
  podcasts) stays its own spec; this spec changes only how the existing glossary is used and
  rewrites the 3 style examples. The definition of done for an LLM stage (Langfuse traces, an
  evaluation set with recorded results) applies.
- `docs/PROJECT.md` — read in full: FR-3.1 (manager of the gameweek = most net points, ties
  allowed, mild banter) and FR-3.3 (the season race) stand; non-functional: the 20 PLN budget,
  Polish content in files, evaluation before done, privacy (manager names never in logs or the
  repository; FPL ranks are public FPL data).
- `docs/DECISIONS.md` — read in full: 2026-10-09 rows on the presser (fact sheet by SQL, the LLM
  only writes; the presser table; the evaluation set and pass rule; net points, whole points,
  `nth_of_season`); spec 012's own decision "five fixed sections, at most 1500 characters"
  (extended here by a sixth section); league standings only for the latest sync while
  per-gameweek data comes from `manager_gameweek` (2026-09-26); request settings per model in
  `model_settings.toml` (2026-09-29, path 2026-09-30); a refactor a feature needs is done in
  that feature's spec (2026-09-30).
- `docs/BACKLOG.md` — read in full: #32 (the presser model comparison, P1, trigger "spec 012 is
  merged") runs after this spec and decides the inflection problem; #5 and #25 stay out of
  scope.
- `docs/CONVENTIONS.md` — searched for "content", "Polish", "test", "migration": product content
  in Polish in `backend/app/content/`; LLM steps unit-tested against a fake model; external APIs
  tested against recorded payloads.
- `docs/DEPLOYMENT.md` — searched for "presser", "league sync", "backfill", "migration": every
  migration is listed in the runbook; the first-deploy catch-up syncs every finished gameweek.

## Scope

- **GW rank in the collector.** The league sync stores each manager's GW rank per gameweek in
  `manager_gameweek` (a new nullable column; empty for a manager without a team). Existing
  rows stay empty until the next league sync of that gameweek. The runbook lists the
  migration, and the owner fills the local GW1–5 by a manual league sync.
- **Fact sheet version 2** (`FactSheet.version = 2`). Unchanged from version 1 except:
  1. Every winner and flop carries its `gameweek_rank` (null when unknown).
  2. Season records `best_gameweek` / `worst_gameweek` are the best and worst GW rank in the
     league so far this season (including this gameweek), each with the manager, the
     gameweek, the GW rank and the net points. Ties give several records. Managers or
     gameweeks without a GW rank are left out.
  3. Personal season bests and worsts: managers whose GW rank this gameweek is their best or
     worst of the season so far. This is flagged only once a manager has at least 3 gameweeks
     with a GW rank.
  4. A new `overall` section, ranked by the code, per manager with an overall rank this
     gameweek: the overall rank now and before (the previous gameweek), the movement in
     places, and the thresholds crossed: entered or dropped out of the top 1M, the top 100k or
     the top 10k. Inside the top 10k every rise is notable. Outside it, a move is notable
     only when the rank at least halves (a rise) or at least doubles (a fall): 4M → 2M,
     400k → 200k, 30k → 15k. A rise of 100k places around 1M is not news, so the bar is
     relative and scales with the rank. The league's biggest climber and faller are chosen
     by that ratio (old rank ÷ new rank), not by places, and the places are still given. At
     the season's first gameweek there is no movement, and a manager finishing GW1 inside a
     threshold counts as having entered it. The section is empty, and listed in
     `empty_sections`, when no threshold was crossed, no one rose inside the top 10k and no
     move reached the 2× ratio.
  5. The league average stays the only average. The overall FPL average is not collected
     (the owner: zombie accounts drag it down).
  6. `check_fact_sheet` also checks the new facts for internal consistency: positive ranks,
     movement = before − now, thresholds consistent with the ranks, and records consistent with
     the season rows.
- **Six sections.** The presser keeps the fixed headers of spec 012 in the same order and adds
  a sixth section, "🌍 Overall", after the table, skipped when `overall` is empty. The length
  limit stays at 1500 characters.
- **Writer prompt version 3** (in `backend/app/content/prompts/`):
  - Slang only where it sounds natural. The glossary is a reference for correct usage, not a
    list to work in.
  - Never copy phrases or jokes from the style examples, and vary how a paragraph is built
    from one presser to the next.
  - Explain what the GW rank means: how good the score was against all of FPL, so a modest
    score with a strong GW rank is a good week. Use it in the winner, flop and season-record
    lines when it adds something.
  - Ranks may be quoted exactly or rounded the way FPL players say them ("top 10k",
    "1,2 mln"). Rounding is not arithmetic the writer may not do.
  - Previous pressers stay at most 2. The prompt states that they are for referring back to
    events, not a source of phrases.
- **Style examples rewritten.** The 3 examples keep the six headers but build each paragraph
  differently. They use slang sparingly, include GW rank and overall-rank lines, and share no
  stock phrase with each other. They are written for the project with made-up managers and
  numbers, and the owner approves them at the final review.
- **Writer temperature.** The writer sends a non-zero temperature (0.8) to models
  that accept one. Other LLM steps keep 0, and models with `temperature = false` get none.
- **Evaluation.**
  - The set is rebuilt as v2 on fact sheet version 2: the 10 real cases from the local
    GW1–5, after the owner's league sync fills the GW rank, pseudonymised as in spec 012,
    plus the 6 synthetic edge cases updated.
  - New synthetic cases: a manager entering the top 10k; a rise inside the top 10k; a drop out
    of the top 1M; a case with the GW rank unknown.
  - The judge prompt treats ranks, thresholds and rounded ranks as factual claims.
  - The `review` command also records the owner's count of inflection errors (names of managers
    and players) per presser, and `summary` reports the average per model. No threshold yet:
    BACKLOG #32 sets it.
- **Documents.** BACKLOG #32's trigger changes to "spec 013 is merged" and its item adds the
  inflection measure. If no candidate inflects acceptably, a name-forms dictionary becomes a
  new BACKLOG item, decided in #32 (see Out of scope). DEPLOYMENT lists the migration, and
  DECISIONS records the GW-rank records and the sixth section.

## Out of scope

- A name-forms dictionary (a stronger model generating the 7 cases once per name, cached in
  the database, correctable from the CLI). The owner wants to see first whether another
  writer model inflects well: BACKLOG #32 measures it with this spec's counter, and the
  dictionary becomes a BACKLOG item only if no candidate passes.
- The overall FPL average and highest score of a gameweek: not needed, because the owner
  measures a gameweek by GW rank, and zombie accounts drag the overall average down.
- A list of overused phrases, a "league member" persona and a repetition metric: offered and
  not chosen by the owner. If the style ratings in #32 still show sameness, they come back
  as a BACKLOG item.
- More than 2 previous pressers.
- The model comparison run itself, BACKLOG #32 (P1), after this spec.
- The full glossary from X and podcasts: ROADMAP Stage 3 item 2.

## Requirements and acceptance criteria

- [ ] AC1: A league sync stores each manager's GW rank (`rank` from FPL) per gameweek, and a
  gameweek without a team stores none; a re-sync of a past gameweek fills an empty GW rank
  (tested on recorded payloads).
- [ ] AC2: The migration adds the GW rank column with existing rows empty and downgrades
  cleanly; `docs/DEPLOYMENT.md` lists it.
- [ ] AC3: In the fact sheet (version 2) every winner and flop carries its GW rank, null when
  unknown.
- [ ] AC4: `best_gameweek` / `worst_gameweek` are the best and worst GW rank in the league so
  far this season including this gameweek, with the manager, gameweek, GW rank and net points;
  ties give several records; rows without a GW rank are skipped.
- [ ] AC5: A manager whose GW rank this gameweek is their best (or worst) of the season is
  flagged only when they have at least 3 gameweeks with a GW rank.
- [ ] AC6: The `overall` section gives, per manager, the overall rank now and before, the
  movement in places, and the thresholds (1M, 100k, 10k) entered or left, with the threshold
  edges tested: 10 000 is inside the top 10k and 10 001 is not. A rise inside the top 10k is
  notable. Outside the top 10k a rise is notable when the new rank is at most half the old
  one (2 000 000 → 1 000 000 is, 2 000 000 → 1 000 001 is not), and a fall when the new rank
  is at least double the old one. The biggest climber and faller are chosen by the ratio of
  the ranks, not by places (a 3M → 1.4M rise ranks below a 300k → 100k rise). At GW1 there is
  no movement, and a manager inside a threshold counts as having entered it.
- [ ] AC7: `overall` is listed in `empty_sections` when no threshold was crossed, no one rose
  inside the top 10k and no move reached the 2× ratio.
- [ ] AC8: `check_fact_sheet` reports an inconsistent rank fact: a non-positive rank, a movement
  ≠ before − now, a threshold that does not match the ranks, or a record not matching the
  season rows.
- [ ] AC9: The writer's prompt (version 3) asks for slang only where natural, forbids copying
  phrases or jokes from the style examples, explains the GW rank, covers the six sections in
  order with "Overall" after the table and skipped when empty, and keeps at most 2 previous
  pressers; the stored presser records `presser_writer@3`.
- [ ] AC10: The 3 style examples carry the six headers, each under 1500 characters; a test
  checks that no sentence of one example is repeated in another and that each has a GW-rank
  and an overall line. The owner approves the text at the final review.
- [ ] AC11: The writer requests temperature 0.8 from a model whose settings accept a
  temperature and none from one that does not; the extraction, judge and other steps still
  send 0 (tested with the fake model / factory).
- [ ] AC12: Evaluation set v2: the 10 pseudonymised real cases on fact sheet version 2 with GW
  ranks, the 6 updated synthetic cases and the 4 new rank cases, split dev / test; a test
  checks that every case validates and no real name is present.
- [ ] AC13: `review` records an inflection-error count (a whole number ≥ 0) per presser next to
  the style rating, and `summary` reports its average per model.
- [ ] AC14: The judge prompt lists ranks, thresholds and rounded ranks among the claims it
  checks; a rounded rank ("1,2 mln" for 1 234 567) is `supported`, a wrong one is not (tested
  with a fake judge on a recorded reply, plus the prompt text check).
- [ ] AC15: The application logs carry no manager names or ranks tied to a manager (the spec
  012 log capture test still passes with the new facts).
- [ ] AC16: BACKLOG #32's trigger reads "spec 013 is merged" and its item mentions the
  inflection count; DECISIONS records the GW-rank records, the overall section and its
  thresholds.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| The season's best and worst gameweeks are judged by GW rank | by net points (spec 012) | the owner: a gameweek is good or bad against all of FPL, and 60 in a weak week beats 120 in a strong one |
| The manager of the gameweek stays the most net points | by GW rank | within one gameweek of one league the two agree unless FPL ranks before hits; FR-3.1 and the league's scoring are net points |
| Only the GW rank is collected; averages only from the league | storing the overall FPL average and highest score | the owner: zombie accounts drag the overall average down, and the GW rank says the same thing better |
| Overall thresholds 1M, 100k, 10k; every rise inside the top 10k is notable | 1k/10k/100k/1M; denser thresholds; percentiles | the owner: those are the milestones FPL players celebrate, and inside the top 10k any rise is spectacular |
| A notable overall move outside the top 10k is a 2× change of the rank (halved or doubled); the biggest climber and faller are chosen by the ratio | a fixed number of places (100k or 25%); ranking movers by places | the owner: 100k places around 1M is nothing, while at several million a move must be huge to matter; a ratio scales with the rank |
| A sixth fixed section "🌍 Overall" after the table, skipped when empty | inside the table paragraph | the league table and the overall rank are two stories, and one paragraph gets too long |
| The five headers of spec 012 stay; the content changes (slang only where natural, no copied phrases, rewritten examples) | free structure built around ranked storylines; rotating formats | the owner: the headers are fine, the sameness comes from forced glossary words (confirmed by the copied phrases in the real texts) |
| Name inflection is measured (an error count in `review`) and decided in the model comparison #32 | a name-forms dictionary now; prompt rules only | the owner: maybe another model inflects well, so measure before building |
| A non-zero writer temperature where the model accepts one | temperature 0 everywhere; a list of overused phrases; a persona; a repetition metric | the owner chose temperature only; it takes effect after #32 if the chosen model accepts it |
| Previous pressers stay at 2, for referring back to events | 4 or 6 | the owner: memory is for events, and repetition comes from the examples, not from memory |

## Owner decisions

- A migration adding the GW rank column to `manager_gameweek`: accepted up front.
- No new dependency expected; a new one still needs the owner's approval.
- This spec runs before BACKLOG #32; #32 runs on prompt version 3 and set v2.
- The owner fills the local GW1–5 GW ranks by a manual league sync before the evaluation set
  v2 is built. A migration on the local development database is allowed for agents; production
  stays the owner's.
- Rejected by the owner: the overall FPL average, a name-forms dictionary now, a list of
  overused phrases, a persona, a repetition metric, more previous pressers, free structure.

## Open questions (non-blocking)

- Does FPL rank a gameweek on points before or after hits? It matters only if a hit-taker's GW
  rank looks odd next to the league's net-points winner. The fact sheet passes the FPL number
  on as is and does not check it against net points.
