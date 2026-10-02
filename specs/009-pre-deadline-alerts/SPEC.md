---
status: implemented
stage_history:
  - "spec-draft — 2026-10-02"
  - "spec-ready — 2026-10-02"
  - "plan-draft — 2026-10-02"
  - "plan-approved — 2026-10-02"
  - "implemented — 2026-10-02"
metrics:
  started_at: 2026-10-02T09:43
  escalations: 0
  plan_steps: 18
  plan_review_blockers: 0
  plan_review_majors: 3
  plan_changes: 9
  implement_steps: 18
  implement_iterations: 10
  converge_gaps: 0
  deviations_minor: 8
  deviations_major: 0
---

# SPEC 009 — Pre-deadline alert e-mails

## Goal

Before each FPL deadline the owner gets e-mails about team news for the players that matter to
the league: what the latest news is, how well independent accounts back it, and links to the
posts. It works when, on a real deadline run locally, the T-120 digest, the T-30 news e-mail and
the breaking e-mails after T-30 arrive in the inbox with cited sources, no alert is sent twice or
after the deadline, and `python -m app.alerts latency` reports post → inbox latency for that
gameweek.

## Context

- **What this builds on.**
  - Corroboration (`app/corroboration/`, spec 007): `corroborate(engine, player, as_of,
    new_since, runtime)` returns the anchor claim (newest claim since the last deadline),
    supporting / contradicting / related citations (one per independent account, with URL,
    author and time), a reversal flag and a grade (`high` / `medium` / `low` with reasons).
    With no claim in the window it returns early with no LLM call; otherwise it runs one
    hybrid search and up to 10 judge calls one after another.
  - Extraction (`app/extraction/`): `extraction_event` rows (player FPL ID, event type
    `out` / `doubt` / `benched` / `confirmed_starter`, certainty `confirmed` / `likely` /
    `rumour`); `current_extractions(...)` lists the current extraction of every post in a
    time window.
  - Delivery (`app/delivery/`, spec 008): `DeliveryService.send(key, "alert", message)` with an
    idempotency key; `delivery_log.accepted_at` is the time Resend accepted the e-mail.
  - League data (`app/fpl/models/leagues.py`): `manager`, `manager_pick` (positions 1–11 the
    XI, 12–15 the bench). `league_sync` for gameweek N runs only after N is finished and
    data-checked, so before the GW N deadline the newest picks are from GW N-1 (transfers
    made for GW N are not visible).
  - Ownership: `selected_by_percent` is stored only in `deadline_snapshot_player`, written at
    T-30 and T-5; the `player` table has no ownership column.
  - Worker (`app/worker/`): FPL jobs are planned from `job_run`; tweet polling, extraction and
    indexing run as threads sharing the stop event. Tweet polling (`app/tweets/schedule.py`)
    polls every 20 s in a 90-minute window before a deadline and every 30 min otherwise.
- **The owner's schedule** (described in spec 007 and refined in this spec's dialogue): a
  digest at T-120, news at T-30, then breaking e-mails up to the deadline.
- **Preconditions moved by the owner.** The roadmap lists the production deployment and
  BACKLOG #11 as preconditions. The owner builds and tests this spec locally; the deployment
  follows it, and BACKLOG #11 is checked after this spec and before the deployment.

## Read context

- `docs/ROADMAP.md` — read in full. Stage 2 item "Pre-deadline alert e-mails" (first
  functional MVP); the alert text's LLM question is decided here; preconditions (deployment,
  BACKLOG #11) are moved by the owner (see Owner decisions); Stage 4 later shows per-account
  accuracy in alerts.
- `docs/PROJECT.md` — searched for "alert", "latency", "deadline". FR-2.3 (alerts about
  league-owned and widely owned players, each citing sources with links); the 60 s p95
  detection-latency requirement, with extraction and e-mail on top; budget ≤ 20 PLN/month;
  times shown in `Europe/Warsaw`; alerts never based on the official FPL injury flags.
- `docs/DECISIONS.md` — searched for "alert", "picks", "ownership", "selected_by",
  "deadline", "corroboration", "delivery". Alerts come from X leaks, never the FPL flags
  (2026-09-26); the module list includes `alerts` (2026-09-26); the worker schedule and its
  snapshots at T-30 / T-5 (2026-09-27); corroboration on demand with `as_of` / `new_since`
  (2026-09-30); delivery behind a channel-agnostic interface with idempotency keys
  (2026-10-01).
- `docs/BACKLOG.md` — searched for "alert", "11", "19", "20". #11 (twscrape drops posts) is
  checked after this spec; #19 (aggregators inflate independent counts) and #20 (players with
  no extracted claim) stay open and limit what alerts show.
- `docs/DEPLOYMENT.md` — searched for "alert", "tweet", "window", "delivery". Every feature
  is optional by environment variable and the worker runs as before without it; step 11
  configures delivery.
- `docs/adr/` — `0004-email-as-mvp-channel.md` read in full (e-mail to the owner; end-to-end
  alert latency includes e-mail delivery and is measured); `0005` and `0006` searched for
  "latency" (twscrape p95 detection 25.9 s; the extraction model).

## Scope

- An `alerts` module that decides, before each deadline, which players to report, renders
  Polish alert e-mails and sends them through `app/delivery`.
- Players in scope ("listed players"): league-owned (in the latest synced picks, XI or bench,
  of any manager of the configured leagues, with those managers named), widely owned
  (current ownership at or above a threshold) and trending (claims from at least N
  independent accounts in the window).
- Three alert kinds: the digest (first slot), news (later slots) and breaking (from the last
  slot to the deadline).
- A `selected_by_percent` column on `player`, refreshed by every reference sync.
- Alert log tables recording each alert and the posts it included, for "new since the
  previous alert" and the latency report.
- Fast tweet polling starting from the first alert slot instead of a fixed 90 minutes.
- A Polish alert template in `app/content/`.
- A rehearsal deadline (`ALERT_REHEARSAL_DEADLINE`) that runs the whole alert cycle against
  made-up deadline, with real e-mails, isolated from real deadlines by the alert log's
  deadline key.
- CLI `python -m app.alerts`: `preview`, `latency`, `status`; a line in
  `python -m app.worker status`.
- Configuration by environment variables with start-up validation; documentation
  (`.env.example`, `docs/DEPLOYMENT.md`); `docs/ROADMAP.md` updated: the preconditions line of
  this item, and a new Stage 2 item after the deployment for an LLM-written alert summary.

## Out of scope

- An LLM-written per-player summary in the alert, with a faithfulness evaluation — a new
  roadmap item after the production deployment (owner decision). It would only summarise
  facts already in the alert; it would not choose what or when to send, fetch data or change
  grades.
- Players with no extracted claim whom only retrieval finds — BACKLOG #20.
- Aggregator / shared-source detection in the independent counts — BACKLOG #19.
- Per-account accuracy in alerts — roadmap Stage 4.
- Transfers made for the coming gameweek: the FPL API does not show other managers' picks
  before the deadline; league ownership uses the latest synced picks.
- The production deployment and BACKLOG #11 — after this spec (owner decision).
- Several recipients or per-manager alerts — multi-tenancy, after the MVP.
- Alerts about the official FPL flags — rejected by DECISIONS 2026-09-26.

## Requirements and acceptance criteria

Configuration

- [ ] AC1: Alerts run in the worker only when delivery, tweet ingest and extraction are all
  enabled; otherwise `python -m app.worker status` shows `Alerts: disabled (<reason>)` and
  the worker runs as before. Setting `ALERTS_ENABLED=false` turns them off explicitly.
- [ ] AC2: `ALERT_SLOTS_MINUTES` (default `120,30`) sets the slots in minutes before the
  deadline; the first slot is the digest, the others are news slots, and breaking alerts run
  from the last slot to the deadline. `ALERT_TRENDING_MIN_ACCOUNTS` (default `3`) and
  `ALERT_WIDELY_OWNED_PERCENT` (default `15`) set the thresholds. An invalid value (not
  positive integers, not strictly decreasing, a percentage outside 0–100) fails start-up of
  the worker and the CLI with a message naming the variable.

Players

- [ ] AC3: A player is league-owned when he is in the picks (positions 1–15) of any manager of
  the configured leagues for the latest gameweek whose picks are synced; the alert names those
  managers (manager name and team name).
- [ ] AC4: A player is widely owned when `player.selected_by_percent` is at or above
  `ALERT_WIDELY_OWNED_PERCENT`.
- [ ] AC5: A player is trending when, in the alert window, posts from at least
  `ALERT_TRENDING_MIN_ACCOUNTS` independent accounts carry an extraction event for him;
  independence counts the original author of a repost, as corroboration does (spec 007).
- [ ] AC6: An alert reports only listed players who have a claim (an extraction event) in the
  window; a listed player with no claim is not mentioned except in the digest's "no news"
  line. A player in several categories appears once, with all his categories shown.

Ownership column

- [ ] AC7: Migration adds `player.selected_by_percent` (nullable); every reference sync writes
  the current value from the FPL bootstrap for every player; existing rows and other tables
  are unchanged; the migration downgrades cleanly.

Digest (first slot)

- [ ] AC8: At the first slot the worker sends one digest e-mail covering the window from the
  previous deadline to the slot time, with full corroboration (judge included) for every
  reported player. It is sent even when no listed player has a claim, saying there is no news.
- [ ] AC9: For each reported player the e-mail shows: name and club, categories (league-owned
  with managers / widely owned with the percentage / trending with the account count), the
  anchor claim (event type and certainty in Polish), the grade, the number of supporting and
  contradicting independent accounts, a reversal note when flagged, and links to the anchor
  and every supporting and contradicting post. Players are ordered by
  `selected_by_percent`, highest first, so a player owned by one league manager but rarely
  owned overall comes near the end.
- [ ] AC10: All times in the e-mail are `Europe/Warsaw`; every Polish text comes from a
  template in `app/content/`, none from string literals in code.

News slots

- [ ] AC11: At each later slot the worker sends one news e-mail, with full corroboration,
  reporting only listed players with at least one post not included in any earlier alert of
  the same alert deadline (AC18); the new posts are marked as new. With nothing new no e-mail is sent and the
  slot is recorded as skipped.

Breaking

- [ ] AC12: From the last slot until the deadline, each newly extracted post with an event for
  a listed player produces one breaking e-mail about that player, built from SQL rules only
  (no retrieval, no judge call), with the post as the anchor and the player's other
  extracted posts in the window as support or contradiction.
- [ ] AC13: A post already included in an earlier alert of the same alert deadline, or a repost of one,
  never triggers a breaking e-mail.
- [ ] AC14: A breaking e-mail is sent within 15 s of the post's extraction finishing when
  delivery succeeds on the first attempt, measured in a test with a fake channel and clock.

Timing and idempotency

- [ ] AC15: Each alert has a deterministic idempotency key (the alert deadline key, the slot,
  or the post for a breaking alert); a repeated run, a second worker or a restart never sends the same alert
  twice.
- [ ] AC16: After a worker restart, a slot whose time has passed but whose deadline has not is
  sent at once, covering the window up to the actual send time; no alert of any kind is sent
  at or after the deadline.
- [ ] AC17: A failed delivery is recorded as `failed` and is not retried by a later slot; a
  failed corroboration of one player leaves the player in the e-mail with the SQL-only result
  and a note that the search part failed; neither blocks other alerts, tweet polling,
  extraction or FPL jobs.

Alert log and latency

- [ ] AC18: Migration adds the alert log tables (each alert: alert deadline key, kind, slot,
  as-of time, status, delivery log row; each included post: alert, player, post, new or context); new
  tables only, no change to existing data; the migration downgrades cleanly. The alert
  deadline key is the season and gameweek for a real deadline (so a deadline FPL moves does
  not send the slots again) and the deadline time for a rehearsal deadline (AC22); "earlier
  alert" and "new since" in AC11 and AC13 are always within one alert deadline key.
- [ ] AC19: `python -m app.alerts latency [--gameweek N | --rehearsal]` prints, for the latest alert
  deadline by default, for posts that triggered or
  were first reported in an alert, the count and the p50 / p95 / max of post creation →
  e-mail accepted by the provider, split into post → first fetch, fetch → extraction done and
  extraction → accepted.

Tweet polling window

- [ ] AC20: Fast tweet polling starts at the first alert slot plus 10 minutes before the
  deadline (130 minutes with the defaults) and at least 90 minutes before it; with alerts
  disabled the window stays 90 minutes.

CLI and status

- [ ] AC21: `python -m app.alerts preview --at <Warsaw time> [--kind digest|news]` renders the
  alert the worker would send at that moment from the database and prints it, sending nothing
  and writing nothing.
Rehearsal

- [ ] AC22: With `ALERT_REHEARSAL_DEADLINE=<Warsaw time>` set, the worker treats that moment as
  one extra alert deadline: fast tweet polling, the digest, the news slots and breaking alerts
  run for it and are sent for real, in any environment. It is not written to the gameweek
  table, so FPL jobs, snapshots, gameweek numbering and the corroboration window (which
  starts at the previous real deadline) ignore it.
- [ ] AC23: Rehearsal alerts never affect a real deadline: after a rehearsal, the next real
  digest still covers the whole window since the previous real deadline, including the posts
  the rehearsal already reported, and the real news and breaking alerts treat them as not yet
  included.
- [ ] AC24: A rehearsal deadline in the past does nothing; one whose alert window (from the
  first slot plus the polling margin to the deadline) overlaps a real deadline's alert window
  fails start-up of the worker with a message naming the variable.
- [ ] AC25: `docs/DEPLOYMENT.md` and `.env.example` describe `ALERT_REHEARSAL_DEADLINE` as an
  optional variable for testing, to be removed after use.
- [ ] AC26: `python -m app.alerts status` and the worker status line show the next slot, the
  last alert (kind, time, outcome) and the number of failed alerts for the current alert deadline.

Quality

- [ ] AC27: The players-in-scope selection, the slot planning (including restart and the
  deadline cut-off), the "new since" rule and rendering are covered by tests on fixed data
  with a fake clock and a fake channel; an end-to-end test runs one simulated deadline
  (digest, news, two breaking posts, one repeated post) and one rehearsal deadline against a
  test database.
- [ ] AC28: Logs carry no e-mail bodies, addresses or post texts (alert ID, kind, counts and
  outcome only).

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Deterministic Polish template for the alert text | LLM-written text in this spec | an LLM step needs its own faithfulness evaluation; it moves to a roadmap item after the deployment (the owner) |
| Slots T-120 (digest) and T-30 (news), breaking from T-30 to the deadline; configurable | adding T-10 | fewer e-mails; after T-30 every new post goes out as breaking anyway (the owner) |
| Full corroboration (with the judge) for the digest and news; SQL rules only for breaking | the judge everywhere; SQL everywhere | breaking needs seconds, not tens of seconds; slots have time for the judge (the owner) |
| Digest always sent; news only with something new | always sending every slot | a "no news" digest confirms the system works; empty news e-mails are noise (the owner) |
| A missed slot is sent after a restart, never after the deadline | skipping missed slots | a late alert before the deadline is still useful; after it, useless (the owner) |
| Trending players (≥ N independent accounts) are reported even without ownership | owned players only | a player many accounts talk about may be popular this gameweek despite low ownership (the owner) |
| `player.selected_by_percent` refreshed by reference sync | ownership from the last deadline snapshot | the snapshot is a week old at T-120; reference sync runs every 15 min before a deadline (the owner) |
| Alert log tables with per-post inclusion | deriving "new" from slot times and latency from `delivery_log` | exact "new since the previous alert" and per-post latency (the owner) |
| Fast tweet polling from the first slot | keeping 90 minutes; one extra poll before the digest | the digest must see recent posts; the window follows the slot configuration (the owner) |
| `preview` for any past moment and a rehearsal deadline that runs the whole cycle with real e-mails; the alert log is keyed by alert deadline, which isolates a rehearsal with no flag | a manual `send` of one alert; a `rehearsal` flag filtered in every query; local-only rehearsals; waiting for a real deadline | the owner tests digest, news and breaking end to end, and measures latency, before GW6 (the owner) |
| Players ordered by overall ownership, highest first | league-owned first, then by grade | the most widely owned players matter most; a player one league manager owns comes near the end (the owner) |

## Owner decisions

- 2026-10-02 — preconditions: this spec is built and tested locally now; the production
  deployment follows it; BACKLOG #11 is checked after this spec and before the deployment.
- 2026-10-02 — alert text: a deterministic template; an LLM-written summary with a
  faithfulness evaluation is a new roadmap item after the deployment.
- 2026-10-02 — schedule: digest T-120 always sent; news T-30 only with something new; breaking
  from T-30 to the deadline, one e-mail per new post about a listed player; a missed slot is
  sent after a restart but never after the deadline; slots configurable.
- 2026-10-02 — players: league-owned (latest synced picks, XI and bench, managers named),
  widely owned ≥ 15%, trending ≥ 3 independent accounts, the threshold configurable.
- 2026-10-02 — corroboration: full for the slots, SQL rules only for breaking.
- 2026-10-02 — data migrations accepted: `player.selected_by_percent`; the alert log tables
  (new tables only).
- 2026-10-02 — fast tweet polling starts from the first alert slot.
- 2026-10-02 — testing: `preview` for any past moment and a rehearsal deadline
  (`ALERT_REHEARSAL_DEADLINE`) running the whole cycle with real e-mails, allowed in any
  environment, isolated by the alert deadline key; no manual `send`.
- 2026-10-02 — the assumptions in AC1, AC2, AC13, AC14, AC19 and AC20 are confirmed.
- 2026-10-02 — players in an alert are ordered by `selected_by_percent`, highest first.
- New dependency: none expected.

## Open questions (non-blocking)

- How many breaking e-mails a real T-30 window produces; if too many, a later spec can batch
  them (e.g. one e-mail per minute).
- The LLM cost of full corroboration for the slots is not measured yet; the latency report
  and Langfuse traces from the first real deadline show it.
