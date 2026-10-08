---
status: implemented
stage_history:
  - "spec-draft — 2026-10-08"
  - "spec-ready — 2026-10-08"
  - "plan-draft — 2026-10-08"
  - "plan-approved — 2026-10-08"
  - "implemented — 2026-10-08"
metrics:
  started_at: 2026-10-08T16:35
  plan_steps: 7
  plan_changes: 8
  escalations: 0
  escalations_permission: 0
  escalations_tooling: 0
  plan_review_blockers: 0
  plan_review_majors: 2
  implement_steps: 7
  implement_iterations: 4
  deviations_minor: 3
  deviations_major: 0
---

# SPEC 011 — Alert slots by the deadline calendar

## Goal

Time the pre-deadline alerts to when FPL team news actually lands: the digest goes out the
evening before the deadline (20:00 Warsaw time) after all press conferences, the news e-mail
one hour before the deadline, and breaking e-mails cover the last hour. It works when, for every
deadline type in the season (Fri 19:30, Sat 12:00, Sat 14:30, Tue/Wed 19:00–19:30, Sun), the
digest lands at 20:00 the day before, the news e-mail at T-60 and breaking e-mails between T-60
and the deadline, while fast X polling still runs only in the last 90 minutes.

## Context

- Spec 009 (pre-deadline alerts) built the slots: `ALERT_SLOTS_MINUTES` (default `120,30`),
  minutes before the deadline; the first slot is the digest, later ones news, breaking runs
  from the last slot to the deadline (`backend/app/alerts/config.py`, `schedule.py`,
  `loop.py`, `service.py`, `status.py`, `cli.py`).
- A slot is stored in the alert log as `alert.slot_minutes` and is part of the alert's
  idempotency key (`alert_key(deadline, kind, slot)`); `done_slots` is keyed by it
  (`backend/app/alerts/store.py`).
- Fast tweet polling (every 20 s) starts at the first slot plus 10 minutes, and at least 90
  minutes before the deadline (`polling_window` in `backend/app/alerts/schedule.py`, used by
  `_polling` in `backend/app/worker/cli.py`; the poll schedule is
  `backend/app/tweets/schedule.py`). Moving the first slot to the evening before would make
  that ~16–24 hours of 20 s polling (~2,900–4,300 requests through twscrape per deadline
  instead of ~400), so the window must follow the last slot instead.
- The rehearsal overlap check (`check_rehearsal`) uses the same first-slot window.
- Research behind the timing (conversation with the owner, 2026-10-08): this season's deadlines
  in Warsaw time are Sat 14:30 (18 gameweeks), Fri 19:30 (8), Sat 12:00 (5), Tue/Wed
  19:00–19:30 (5) and Sun 14:30/15:30 (2); weekend press conferences run Thursday and Friday,
  Friday's block roughly 10:00–14:30 Warsaw time; midweek ones Monday/Tuesday; line-up leaks
  come the evening before and the morning of the match, sometimes minutes before an early
  Saturday deadline. A fixed T-24h digest would land mid-Friday-pressers for Sat 12:00
  deadlines (Fri 12:00); 20:00 the day before is after all pressers for every deadline type,
  16 to 23.5 hours before it. The local database holds too few posts (363) to confirm the
  leak timing empirically.
- Production is not deployed yet (roadmap Stage 0), so renaming the variable breaks no
  running environment; the owner's local `backend/.env` sets `ALERT_SLOTS_MINUTES=120,30`.

## Read context

- `docs/ROADMAP.md` — read in full: the alerts (spec 009) and off-list posts (spec 010) are
  done; production deployment follows the owner's local alert run on GW6 (2026-10-10). This
  change refines a done item, so the spec is linked under the spec 009 item.
- `docs/PROJECT.md` — searched for "alert", "slot", "deadline", "polling", "window":
  FR-2.3 (alerts before each deadline); the 60 s latency target applies "in the final window
  before a deadline", with coarser polling outside it acceptable — unchanged by a 90-minute
  fast window; time is computed in UTC and shown in `Europe/Warsaw`.
- `docs/DECISIONS.md` — searched for "slot", "digest", "polling", "window", "rehearsal":
  2026-09-28 (tweet polling every 20 s in the 90 minutes before a deadline), 2026-10-02 (alert
  slots `ALERT_SLOTS_MINUTES` default `120,30`; "fast tweet polling starts from the first
  alert slot"; rehearsal keyed by its time), 2026-10-06 (the alert window from the previous
  deadline, capped by `ALERT_MAX_LOOKBACK_DAYS`). This spec supersedes the slot default, the
  slot format and the fast-polling start of 2026-10-02.
- `docs/BACKLOG.md` — read in full: no item covers alert timing; the deferred deadline-day
  slot (variant 4) is added here.
- `docs/CONVENTIONS.md` — searched for "time", "zone", "config", "env": datetimes are aware
  UTC, converted to `Europe/Warsaw` only for display and parsing of local times.
- `docs/DEPLOYMENT.md` — searched for "ALERT_", "slot", "polling", "rehearsal": step 12
  documents `ALERT_SLOTS_MINUTES`, the fast-polling start and the rehearsal overlap rule; all
  three change.
- `docs/adr/` — not applicable: no ADR covers alert timing.

## Scope

- A new variable `ALERT_SLOTS` replacing `ALERT_SLOTS_MINUTES`: a comma-separated list where
  each slot is either minutes before the deadline (`60`) or a Warsaw wall-clock time a number
  of days before the deadline's Warsaw date (`D-1@20:00`). Default `D-1@20:00,60`.
- Slots are resolved per deadline into moments; the first resolved slot is the digest, the
  others news, breaking runs from the last slot to the deadline (unchanged semantics).
- Fast tweet polling anchored to the last slot instead of the first.
- One extra tweet poll before each slot that falls outside the fast-polling window.
- The rehearsal overlap check based on the alert span of each deadline.
- Status lines, `preview`, worker start log and documentation updated to the resolved slots.
- DECISIONS entry, DEPLOYMENT step 12, BACKLOG item for the deferred deadline-day slot.

## Out of scope

- A news slot on the deadline day (e.g. 15:00 when the deadline is at 18:00 or later — the
  owner's "variant 4"): BACKLOG, P2, trigger: after a few evening deadlines (Fri/Tue/Wed), the
  owner sees press-conference news reaching friends only at T-60.
- Conditional slots (a slot applying only to some deadline types) — not needed without
  variant 4.
- Changing the alert content, grouping, the alert window (`ALERT_MAX_LOOKBACK_DAYS`) or the
  breaking rules.
- Validating the timing on production data (posts with events per hour before a deadline):
  the owner may run it after deployment; no code needed.

## Requirements and acceptance criteria

Configuration

- [ ] AC1: `ALERT_SLOTS` (default `D-1@20:00,60`) holds comma-separated slots; each slot is
  either a positive integer of minutes before the deadline, or `D-<n>@<HH:MM>` with `n` ≥ 1
  meaning `HH:MM` Warsaw time on the date `n` days before the deadline's Warsaw date. An empty
  value means the default. A malformed slot (e.g. `D-0@20:00`, `D-1@25:00`, `D1@20:00`, `0`,
  `abc`), minutes slots that are not strictly decreasing, or a wall-clock slot placed after a
  minutes slot stops the worker and the alerts CLI at start with a message naming
  `ALERT_SLOTS`.
- [ ] AC2: When `ALERT_SLOTS_MINUTES` is set (any non-empty value), the worker and the alerts
  CLI stop at start with a message saying it was replaced by `ALERT_SLOTS`.

Resolution and timing

- [ ] AC3: For a given deadline, a wall-clock slot resolves to the aware UTC moment of that
  Warsaw local time (DST-aware) and a minutes slot to the deadline minus the minutes. With the
  default, the digest is at 20:00 Warsaw time the day before and the news slot at T-60, for
  each deadline type: Fri 19:30 → Thu 20:00 and Fri 18:30; Sat 12:00 → Fri 20:00 and Sat
  11:00; Sat 14:30 → Fri 20:00 and Sat 13:30; Wed 19:30 → Tue 20:00 and Wed 18:30; Sun 15:30 →
  Sat 20:00 and Sun 14:30; including a deadline whose day before crosses a DST change.
- [ ] AC4: The first slot sends the digest and later slots send news e-mails, and breaking
  e-mails run from the last slot to the deadline, exactly as in spec 009 (AC8, AC11, AC12),
  with the slots taken as resolved moments.
- [ ] AC5: A slot is recorded in the alert log with `slot_minutes` = the whole minutes between
  its resolved moment and the deadline, and that number is the slot part of the idempotency
  key, so a restart never sends a slot twice and needs no schema change.
- [ ] AC6: For a deadline where the resolved moments are not strictly increasing (a
  wall-clock slot at or after a later slot, or at or after the deadline), the out-of-order
  wall-clock slot is skipped for that deadline with a warning in the log naming the deadline
  and the slot; the remaining slots work as usual.
- [ ] AC7: After a restart, a slot whose moment has passed but whose deadline has not is sent
  at once, as in spec 009 AC16; this covers a worker down at 20:00 the day before.

Tweet polling

- [ ] AC8: Fast tweet polling (every 20 s) runs from `max(90 min, last slot's minutes + 10
  min)` before the deadline — 90 minutes with the default — and no longer depends on the
  first slot; with alerts disabled it stays 90 minutes.
- [ ] AC9: For each slot whose moment falls before the fast-polling window, the tweet loop
  polls once at the slot moment minus 10 minutes (when the regular schedule would not poll
  between that moment and the slot), so the digest sees posts at most ~10 minutes old.

Rehearsal

- [ ] AC10: A rehearsal deadline whose alert span — from its first resolved slot (or the
  fast-polling window start, if earlier) to the deadline — overlaps a real deadline's alert
  span stops the worker at start with the existing message naming
  `ALERT_REHEARSAL_DEADLINE`; a rehearsal in the past is ignored as before.

Status and tools

- [ ] AC11: `python -m app.alerts status` and the `Alerts:` line of `python -m app.worker
  status` show the next slot's kind and its resolved Warsaw time (e.g. `next slot: digest Fri
  20:00`), or the breaking window's end.
- [ ] AC12: `python -m app.alerts preview --at <time> --kind digest|news` renders as before;
  `--kind news` with a single slot fails with a message naming `ALERT_SLOTS`.
- [ ] AC13: The worker start log lists the configured slots as written (e.g.
  `slots=D-1@20:00,60`).

Documentation

- [ ] AC14: `docs/DECISIONS.md` gets an entry for the slot format, the default and the fast
  polling following the last slot (superseding the 2026-10-02 slot default and fast-polling
  start); `docs/DEPLOYMENT.md` step 12 documents `ALERT_SLOTS`, the removed
  `ALERT_SLOTS_MINUTES`, the fast-polling window and the rehearsal span; `docs/BACKLOG.md`
  gets the deadline-day slot item; `backend/.env.example` uses `ALERT_SLOTS` instead of `ALERT_SLOTS_MINUTES`.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| Digest at 20:00 Warsaw time the day before the deadline | T-24h; T-120 (today) | deadline times vary from 12:00 to 19:30 and weekday to weekday; T-24h lands mid-Friday-pressers before Sat 12:00 deadlines, 20:00 the day before is after all pressers for every deadline type and when managers plan transfers |
| News at T-60, breaking from T-60 | T-30 (today); T-90 | an hour to react and for the owner to forward to WhatsApp; late leaks still come as breaking e-mails |
| One variable `ALERT_SLOTS` with mixed minutes and `D-n@HH:MM` slots, old variable rejected | extending `ALERT_SLOTS_MINUTES`; a separate digest-time variable | the name must describe the value; one list keeps "first = digest, last = breaking start"; a stale old variable must not be silently ignored (the owner) |
| A wall-clock slot is stored as its minutes before that deadline | a new column or a text slot id (migration) | the alert log, keys and `done_slots` stay unchanged; the number is deterministic per deadline |
| Fast polling follows the last slot (≥ 90 min) | following the first slot (today) | a digest the day before would mean ~16–24 h of 20 s polling and a ban risk for the twscrape account; the 60 s target applies only to the final window |
| One extra poll 10 min before a slot outside the fast window | 30 min of fast polling before each slot; nothing (sparse polls up to 30 min old) | near-fresh digest for one request; extraction has 10 minutes to catch up (the owner) |
| Rehearsal overlap by alert span | the fast-polling window | the alert loop works on the nearest deadline only, so a deadline inside another's span would delay its slots |
| Deadline-day news slot deferred | adding it now | starting simpler; evidence from evening deadlines decides (the owner) |

## Owner decisions

- Variant 1–3 of the conversation (digest 20:00 the day before, news T-60, breaking from T-60)
  — approved by the owner; variant 4 deferred to BACKLOG.
- `ALERT_SLOTS` replaces `ALERT_SLOTS_MINUTES`; a set old variable stops the start — approved.
- One extra tweet poll 10 minutes before a slot outside the fast window — approved.
- An out-of-order wall-clock slot is skipped for that deadline with a warning (AC6) — approved.
- Target: merged before the GW6 digest (Fri 2026-10-09 20:00 Warsaw time), so the owner's
  local GW6 run uses the new slots.
- No new dependency and no data migration.

## Open questions (non-blocking)

- A rehearsal now needs about a day of distance from a real deadline with the default slots;
  for quick local tests the owner can set e.g. `ALERT_SLOTS=120,30` alongside
  `ALERT_REHEARSAL_DEADLINE`.
