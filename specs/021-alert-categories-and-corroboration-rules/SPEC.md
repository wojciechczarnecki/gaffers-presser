---
status: plan-draft
stage_history:
  - "spec-draft — 2026-11-01"
  - "spec-ready — 2026-11-01"
  - "plan-draft — 2026-11-01"
metrics:
  started_at: 2026-11-01T00:47
  plan_steps: 11
  plan_changes: 4
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
  plan_review_blockers: 1
  plan_review_majors: 0
---

# SPEC 021 — Alert categories and simpler corroboration rules

## Goal

A pre-deadline alert card must say, on one scale of five categories, what the player's next
Premier League match holds, why it says so, and how sure it is, without one extraction slip or
one judge label deciding the card. Success means the GW9 news alert of 2026-10-31, 11:00,
previewed from the live data, shows the cards listed in AC20, and the rules behind a card are one
set shared by digest, news and breaking alerts.

## Context

Evidence: the GW9 news alert of 2026-10-31, 11:00 (alert 14) and a digest preview at the same
moment, read-only from the Compose database, and the owner's review of the e-mail:

- **Categories.** The groups are "🔴 Nie zagra", "🟠 Pod znakiem zapytania", "🟡 Na ławce",
  "✅ Wraca do gry", "🟢 Zagra", and the card labels differ from the group names ("Wypada ze
  składu", "Wątpliwy", "Zaczyna na ławce", "Gotowy do gry", "W pierwszym składzie"). The owner
  rejected "✅ Wraca do gry" and wants five uniform categories with coloured circles.
- **"Gotowy do gry" needs reversed X news** (`reverses` in `app/corroboration/rules.py`, SPEC
  020 AC10). Manzambi got a card only because it reversed an extraction slip (the FFScout
  "rested for GW9!" post read as `out`); his FPL flag was `a` (100%) from 2026-10-24. A player
  returning from an injury nobody posted about gets no card.
- **Two definitions of a reversal.** `reverses` (anchor choice for `available`) counts the
  anchor's own account and judged posts; `reversal` (the "⚠️ Wcześniej pisano co innego" note)
  counts only other accounts' contradicting posts. F.Kadıoğlu sat under "Wraca do gry" with no
  note and no "Zaprzecza": the reversed `doubt` was BenDinnery's own earlier post, and BenDinnery's
  newest post supports.
- **One slip becomes the headline.** Kroupi.Jr: two accounts' `benched` posts (Rose: "won't start
  on Saturday") lost to one FPLFocal `out` slip an hour newer; `label_claim` makes `out` against
  `benched` `related`, so the `benched` posts were invisible on the card.
- **The judge confirms.** A SalomonYaniv tips post with no event ("too early for Kroupi") was
  judged `supports`, so the slip showed "2 konta". The judge passes no threshold (BACKLOG #58:
  accuracy 0.72, false support 12%).
- **●●● from one account.** `grade` gives `high` to any `confirmed` anchor; Evanilson showed
  "●●● potwierdzone · 1 konto" from the slip.
- **"New" means "not shown yet".** `included_origins` (`app/alerts/store.py`) decides what news
  is new; after the owner's re-extraction of the window under prompt v6, posts from 28.10 and
  29.10 became claims and put Kroupi.Jr and F.Kadıoğlu into the 31.10 news alert. Breaking alerts
  select posts by the current extraction's `finished_at` (`_candidates` in
  `app/alerts/breaking.py`), so a re-extraction inside the breaking window would send old posts
  as breaking.
- **Duplicated `available` logic.** `corroborate` runs the judge a first time against an
  `available` candidate and, when it reverses nothing, a second time against another anchor
  (`app/corroboration/service.py`); breaking alerts repeat the rule through `available_only`.
- The FPL flag history is already stored: `player_flag_change` (status, chance of playing,
  news, `observed_at`; a baseline row plus a row per change, DECISIONS 2026-09-26). No migration
  is needed.

Code: `app/corroboration/rules.py`, `service.py`, `sources.py`; `app/alerts/service.py`,
`breaking.py`, `render.py`, `store.py`, `players.py`; texts and palette in
`app/content/alert_email.toml`. The extraction side of the same review (the FFScout rest slip,
the match check's coupling to the event types) is SPEC 022.

## Read context

- `docs/ROADMAP.md` — searched for "alert", "Stage 2", "020". Stage 2's alert items are done;
  this spec is a fix to delivered Stage 2 alerts and, like SPEC 020, adds a ticked Stage 2 item
  linking it at delivery. The LLM-written alert summary stays a separate open item.
- `docs/PROJECT.md` — searched for "FR-1", "FR-2". FR-2.2 ("earlier posts are context and never
  outvote the newest leak") holds: the anchor stays the newest claim. FR-2.2's "flag a reversal"
  gets one definition here. FR-1.3 is unchanged.
- `docs/DECISIONS.md` — searched for "anchor", "reversal", "label table", "judge", "grade",
  "included", "new since", "group", "available", "FPL flag". Touched: 2026-09-26 (alerts come from
  X leaks, never from FPL flags: kept, the flag is only a filter for "had a problem"); 2026-09-30
  (label table, grade, the anchor's own account counts on neither side: superseded in part for
  the reversal); 2026-10-02 ×2 (included posts, breaking SQL-only); 2026-10-06 (groups, card
  layout); 2026-10-30 ×2 (the `available` anchor rule, the "✅ Wraca do gry" group, breaking for
  `available`). Each is extended or superseded by this spec's decisions, recorded in the same PR.
- `docs/BACKLOG.md` — searched for "#58", "#59", "#60", "judge", "alert". #58 stays open (the
  judge's prompt is unchanged here; this spec limits what its labels can do). #59 and #60 do not
  apply: no evaluation run.
- `docs/adr/` — searched ADR 0011 for "judge", "threshold". The judge model is not re-chosen.
- `docs/CONVENTIONS.md` — searched for "User-facing text", "Tests". Polish texts go to
  `app/content/`, every behaviour gets a test.
- `docs/DEVELOPMENT.md` — searched for "alerts", "preview". `python -m app.alerts preview` is the
  manual check; no CLI changes.
- `docs/EVALUATION.md` — searched for "corroboration". No evaluation is run: the judge's prompt
  and model do not change.
- `docs/DEPLOYMENT.md` — searched for "ALERT". No new variable or image change; does not apply.
- `docs/reports/` — read the extraction-eval-v3 summary and searched `corroboration-eval-v2.md`
  for "false support". No open audit item in this area.

## Scope

- Five uniform categories with one name each in the group header, the header counter and the card.
- When an `available` claim becomes the anchor ("Gotowy do gry"): reversed X news or an FPL flag.
- One reversal rule, used for the anchor choice, the card note and breaking alerts, and a card
  line naming what is reversed.
- `benched` against `out` contradicts.
- The judge's labels count only against the anchor.
- The grade's top level needs two accounts.
- "New" in news and breaking alerts follows the post's first successful extraction.
- One judge pass per player, and one shared anchor decision for slot and breaking alerts.

## Out of scope

- Extraction and match-check prompts, the evaluation set cases for the GW9 slips: SPEC 022.
- The judge's prompt and label set (BACKLOG #58 stays, P2). A judge that only answers
  "contradicts / unrelated", which would cut its cost: added to #58 as an option.
- The SalomonYaniv post as a corroboration evaluation case: added to BACKLOG #59's next build.
- A consensus anchor (the type with the most accounts in a news cycle wins): rejected, FR-2.2.
- Using FPL flags as a news source or showing flag changes as cards: DECISIONS 2026-09-26 stays.
- The LLM-written per-player summary (ROADMAP Stage 2, open).

## Requirements and acceptance criteria

### Categories and layout

- [ ] AC1: The five categories, in this order, with these names and circles: 🔴 Nie zagra
  (`out`), 🟠 Wątpliwy (`doubt`), 🟡 Na ławce (`benched`), 🟢 Gotowy do gry (`available`), 🟢 Zagra
  (`confirmed_starter`). The group header, the header counter (lower case: "🔴 nie zagra 1 ·
  🟠 wątpliwy 1 · 🟡 na ławce 2 · 🟢 gotowy do gry 5 · 🟢 zagra 3") and the card label use the
  same name; the card reads "Kroupi.Jr (Bournemouth) — 🟡 Na ławce". No category uses ✅. The
  texts live in `app/content/alert_email.toml`. Tested in render tests for the plain text, the
  HTML and the WhatsApp text.
- [ ] AC2: In the HTML part each category has its own palette entry; "Gotowy do gry" has a
  green-ish accent (for example lime `#84cc16`), clearly distinct from the green of "Zagra" and
  not blue. A test checks that the two accents differ and that neither is the old blue
  `#2e90fa`.

### "Gotowy do gry"

- [ ] AC3: An `available` claim is the anchor when it is the player's newest claim and either
  (a) it reverses by the rule of AC5, or (b) the player's FPL flag at the post's time (the latest
  `player_flag_change` row observed at or before the post's `created_at`) has a status other than
  `a`. Otherwise the anchor is the newest claim of another type, and a player with only
  `available` claims that meet neither (a) nor (b) has no card and uses up no post. Tested for:
  reversed X news; a flag `d` with no X news (the Kadıoğlu shape); a flag `a` with no X news (no
  card; the Manzambi shape without the slip); a flag that turned `a` before the post (no card).
- [ ] AC4: An `available` card that qualifies only through the flag shows the flag's news text
  as the reversed news (AC6), for example "⚠️ Wcześniej: flaga FPL — Hip injury - 75% chance of
  playing". The prefix lives in `alert_email.toml`; the flag text is shown as FPL gives it.

### One reversal rule

- [ ] AC5: A card is a reversal when the newest older SQL claim about the player in the window,
  from any account including the anchor's own, is labelled `contradicts` against the anchor by
  the label table (AC8), or, for an `available` anchor, by AC3 (b). Judged posts never make a
  reversal. The same rule decides the anchor choice of AC3, the card's reversal line and the
  breaking rule of AC14. Tested with a reporter changing their own story, another account's
  older contradiction, an older contradiction followed by a newer supporting claim (no
  reversal), and a judged older contradiction alone (no reversal).
- [ ] AC6: A reversal card shows one line "⚠️ Wcześniej: @account, <day time>: <link>" naming
  the reversed post (or the flag, AC4) instead of today's generic note "⚠️ Wcześniej pisano co
  innego — doniesienia się odwracają". The reversed post is not listed a second time under
  "Zaprzecza".
- [ ] AC7: Account counting is otherwise unchanged: an account counts once by its newest labelled
  post, and the anchor's account is neither supporting nor contradicting (DECISIONS 2026-09-30);
  only the reversal of AC5 reads the anchor's own older posts.

### Labels, judge and grade

- [ ] AC8: The label table gets `benched` against `out` (either side the anchor) = `contradicts`.
  `benched` against `doubt` stays `related` (a doubtful player often ends on the bench), and
  every other pair is unchanged. `label_claim` keeps a test for every pair of the five types.
- [ ] AC9: A judged post counts only when the judge labels it `contradicts`: it is listed under
  "Zaprzecza", counts as a contradicting account and lowers the grade by the existing rules. A
  judged `supports` or `related` post is not shown, not counted and not stored as an included
  post. Tested with a judged supporting post that no longer adds an account (the SalomonYaniv
  shape) and a judged contradiction that still lowers the grade.
- [ ] AC10: The grade: `high` (●●●) needs at least 2 independent supporting accounts, or a
  `confirmed` anchor with at least 1 supporting account; `medium` (●●○) is a `confirmed` or
  `likely` anchor, or 1 supporting account; `low` (●○○) is a `rumour` anchor with no support.
  The lowering by one step for a contradicting account new since the last look and for a
  newer judged contradiction is unchanged. Tested for each level and for the one-account
  `confirmed` anchor (the Evanilson shape: `medium`).

### What is new

- [ ] AC11: In a news alert a post is new when its origin is not among the included posts and the
  first successful extraction of the post finished after the previous alert's `as_of`. Only new
  claims put a player into a news alert (as today); a post that is not new may still appear on
  the card as context, without "(nowe)". Tested with a re-extracted old post (not new), a post
  ingested late but first extracted after the previous alert (new), and a post whose first
  extraction failed and whose retry succeeded after the previous alert (new).
- [ ] AC12: Breaking alerts select posts by the same rule: a post is a breaking candidate when
  its first successful extraction finished after the breaking loop's mark, so a re-extraction
  inside the breaking window sends no breaking e-mail. Tested in `run_breaking`.

### One pass, one rule

- [ ] AC13: The anchor is chosen from SQL claims and FPL flags only, before retrieval, so the
  judge runs at most once per player per alert, against the final anchor. Tested by counting
  judge calls for a player whose newest claim is an `available` that reverses nothing.
- [ ] AC14: A breaking alert fires for a new `available` post only when, as the anchor, it
  qualifies by AC3 for at least one listed player it names; slot and breaking alerts share one
  anchor decision (no separate `available_only` rule). A new `available` post that qualifies for
  no player sends nothing and is not recorded as included. Tested in `run_breaking` for the
  reversal, the flag and the no-card case.

### Documents and the owner's check

- [ ] AC15: `docs/DECISIONS.md` records the categories and names, the "Gotowy do gry" rule with the
  FPL flag filter, the single reversal rule (superseding in part 2026-09-30's own-account rule
  for the reversal), the `benched`/`out` label, the judge counting only against, the grade, and
  the new rule for news and breaking, each marking the rows it extends or supersedes
  (2026-09-26, 2026-09-30 ×2, 2026-10-02 ×2, 2026-10-06, 2026-10-30 ×2). `docs/PROJECT.md` FR-2.2
  names the reversal definition. `docs/ROADMAP.md` gets a ticked Stage 2 item linking this spec.
  `docs/BACKLOG.md` #58 gets the "contradicts-only judge" option, #59 the SalomonYaniv case.
- [ ] AC16: After the merge the owner runs `uv run python -m app.alerts preview --at
  2026-10-31T11:00 --kind news` and `--kind digest` on the Compose database (read-only) and
  checks: Manzambi has no "Gotowy do gry" card; F.Kadıoğlu is under "🟢 Gotowy do gry" with a
  "⚠️ Wcześniej:" line; Evanilson is not ●●●; Kroupi.Jr shows the `benched` accounts under
  "Zaprzecza" and a "⚠️ Wcześniej:" line, and no longer counts the SalomonYaniv post; the news
  alert does not list Kroupi.Jr or F.Kadıoğlu only because of re-extracted 28.10/29.10 posts;
  the five categories read as in AC1. This is a manual procedure; the agent does not run it.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Five categories, one name everywhere, 🟢 for both "Gotowy do gry" and "Zagra" in text, distinct accents in HTML | CSS circles in HTML with 🟢/🟩 in text; ⚪ for "Gotowy do gry"; headers only, card labels kept | Unicode has one green circle; the owner chose the same circles everywhere, the name tells the two apart; one name lets a breaking card stand alone |
| "Gotowy do gry" when the `available` claim reverses X news or the FPL flag at the post's time is not `a` | a "returning" sub-type in extraction; every `available` a card; reversed X news only | the flag says "there was a problem" without making FPL a news source; it is SQL, so breaking alerts use it; on GW9 it keeps Kadıoğlu and Maddison and drops Manzambi and "trained" posts about fit players |
| One reversal rule from SQL claims and the flag, the anchor's own account included, shown as "⚠️ Wcześniej:" | two definitions as today; one rule without the own account | a reporter changing their own story is a reversal the reader must see; one rule lets the anchor be chosen before retrieval, so the judge runs once |
| `benched` against `out` contradicts; against `doubt` stays related | consensus in a news cycle; no change | FR-2.2 keeps the newest leak; a benched player is in the squad, so it contradicts "out", while a doubtful player often ends on the bench (the owner) |
| The judge counts only as a contradiction | removing the judge from alerts; a new judge prompt (#58); judged posts as context only | a false "supports" confirms a wrong card, a false "contradicts" only warns; retrieval stays in alerts |
| ●●● needs 2 supporting accounts, or `confirmed` plus 1 | unchanged; a grade from account counts only | one extraction slip must not get the top grade; real club news gathers accounts quickly |
| New = not included and first successful extraction after the previous alert | `created_at` after the previous alert; unchanged | survives late ingest and extraction retries, but a re-extraction makes no news |

## Owner decisions

- 2026-11-01: two specs: 021 (alerts and corroboration, no evaluation run) and 022 (extraction
  and match check, the GW9 slips as evaluation cases).
- "Gotowy do gry" by reversed X news or a non-`a` FPL flag at the post's time.
- The judge's labels count only against the anchor.
- `benched` against `out` contradicts; `benched` against `doubt` stays related.
- The same circles in HTML and text: 🔴 🟠 🟡 🟢 🟢.
- ●●● needs two accounts.
- New = first successful extraction after the previous alert.
- One reversal rule with the anchor's own account, shown as a "⚠️ Wcześniej:" line.
- One name per category in the header, the counter and the card.
- No new dependency and no migration (`player_flag_change` and `extraction` already hold the data).

## Open questions (non-blocking)

- none
