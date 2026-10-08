# PLAN 011 — Alert slots by the deadline calendar

## Owner summary

- **Approach:** `ALERT_SLOTS_MINUTES` becomes `ALERT_SLOTS` (default `D-1@20:00,60`). Each slot
  is either minutes before the deadline or a Warsaw wall-clock time `D-<n>@HH:MM`. For each
  deadline the slots resolve into "minutes before this deadline", so the alert log, idempotency
  keys and `done_slots` stay as they are (no migration). The existing slot logic (digest = first,
  news = later, breaking from the last) then runs on the resolved numbers. Fast tweet polling
  follows the last slot (90 min with the default). Each slot outside that window gets one extra
  tweet poll 10 minutes before it. The rehearsal overlap check compares alert spans.
- **Main risks:** spamming a warning every second for an out-of-order slot (handled: logged once
  per deadline and slot); a moved FPL deadline gives a wall-clock slot a new minutes number, so
  a digest already sent could be sent again (rare; listed as a backlog proposal); your local
  `backend/.env` still sets `ALERT_SLOTS_MINUTES=120,30`, so after the merge the worker refuses
  to start until you remove that line (as required by AC2).
- **New dependency:** no
- **Data migration:** no
- **Manual scenarios for the owner:** 3. They cover the start refusal with your real `.env`, the
  status lines and tweet schedule against your local database, and the GW6 digest arriving
  Fri 2026-10-09 at 20:00.

## Approach

Context read:

- `specs/011-alert-slot-calendar/SPEC.md`: read in full.
- `docs/CONVENTIONS.md`: read in full. Aware UTC datetimes with Warsaw only for display and
  parsing local times; no docstrings; tests mirror `app/`; CLI checks run with `env -u
  TWEET_SOURCE`.
- `docs/DECISIONS.md`: searched for "slot", "ALERT_SLOTS", "fast tweet polling" and "first
  alert slot". This found the 2026-10-02 entry (default `120,30`, fast polling from the first
  slot). It is superseded and gets a new entry; old rows stay as they are, as in the existing
  log.
- `docs/ROADMAP.md`: searched for "009", "010" and "alert". The spec 009 item is already `[x]`
  and already links spec 011 ("slot timing"), so there is nothing new to tick. The DoD only
  checks that the line still holds.
- `docs/BACKLOG.md`: header and table read; the highest number is #30, so the new item is #31.
- `docs/DEPLOYMENT.md`: searched for "ALERT_", "slot", "polling" and "rehearsal". Step 12
  (lines ~148–184) documents `ALERT_SLOTS_MINUTES`, the fast-polling start and the rehearsal
  overlap. Step 12 is rewritten in those places.
- `README.md`: searched for "T-120", "T-30" and "ALERT_". Line ~181 says "a digest at T-120,
  news at T-30" and must be updated so it stays true (part of the documentation scope).
- Code read in full: `backend/app/alerts/{config,schedule,loop,status,service,cli,store,breaking,
  schemas}.py`, `backend/app/tweets/{schedule,loop}.py`, `backend/app/worker/cli.py`,
  `backend/app/core/local_time.py`.
- Tests read: `tests/alerts/{test_config,test_schedule,test_status,test_loop,test_end_to_end}.py`
  (head and structure), `tests/alerts/test_cli.py` and `tests/alerts/test_slots.py`
  (function lists), `tests/tweets/test_schedule.py`, `tests/worker/test_cli.py` (alerts part,
  lines ~1350–1475), `tests/test_env_example.py`, `tests/test_readme.py` (alerts part),
  `tests/test_docs.py` (backlog helpers).

Design:

1. **Slot specification (config).** `AlertConfig.slots` becomes `tuple[int | WallClockSlot, ...]`.
   An `int` is a minutes slot. `WallClockSlot(days_before: int, at: datetime.time)` is a frozen
   dataclass in `app/alerts/config.py`, and its `__str__` gives `D-1@20:00`. Keeping plain
   `int` for minutes slots means the ~10 tests that build `AlertConfig((120, 30), ...)` still
   work unchanged.
   - Rejected alternative: a single `Slot` class for both kinds. It would force changes across
     every existing test for no gain.
   - `format_slots(slots) -> str` gives the canonical text for the start log (AC13).
   - `AlertSettings.alert_slots` (default `DEFAULT_SLOTS = "D-1@20:00,60"`) replaces
     `alert_slots_minutes`.
   - The retired variable keeps its own field, `alert_slots_minutes: str = ""`. Any non-empty
     value raises `ConfigError("ALERT_SLOTS_MINUTES was replaced by ALERT_SLOTS ...")` in
     `parse_alert_config`. Using a field (and not `os.environ`) means `.env` is honoured the
     same way and tests with `_env_file=None` stay isolated.
2. **Parsing.** Split on `,` and strip each part. A part that fully matches
   `D-(\d+)@(\d{1,2}):(\d{2})` with n ≥ 1, HH ≤ 23 and MM ≤ 59 is a wall-clock slot. Anything
   else must be a positive integer.
   - Validation: minutes slots strictly decreasing, no wall-clock slot after a minutes slot.
   - The order among wall-clock slots is left to resolution (AC6).
   - Every error message names `ALERT_SLOTS`. An empty value means the default (already handled
     by `env_ignore_empty` plus `or DEFAULT_SLOTS`).
3. **Resolution per deadline** goes in `app/alerts/schedule.py`:
   `resolve_slots(slots, deadline_at) -> ResolvedSlots`, a frozen dataclass with
   `minutes: tuple[int, ...]` and `skipped: tuple[WallClockSlot, ...]`.
   - A wall-clock slot resolves to `datetime.combine(warsaw_date(deadline) - n days, at,
     tzinfo=WARSAW).astimezone(UTC)`, and its minutes are
     `int((deadline_at - moment).total_seconds() // 60)` (AC5).
   - A minutes slot keeps its number.
   - Slots are walked from last to first with `bound = 0` minutes (the deadline). Minutes slots
     are always kept (validated already) and set `bound` to their minutes. A wall-clock slot is
     kept only when its floored minutes are `> bound`, otherwise it goes to `skipped` (AC6).
     Comparing the floored minutes (not the moments) guarantees strictly decreasing minutes, so
     two slots can never share one idempotency key even for a deadline with seconds.
   - `slot_moments(slots, deadline_at) -> list[datetime]` = `deadline_at - minutes` for the
     kept slots.
   - All existing int-based functions (`due_slots`, `next_wake`, `breaking_open`) stay unchanged
     and receive `resolved.minutes`.
   - Rejected alternative: resolving to datetimes everywhere. It would rewrite `due_slots`,
     `next_wake`, `done_slots` keys and their tests, with no behavioural gain.
4. **Alerts loop and service.** `AlertLoop.tick` resolves the slots for the nearest deadline
   and uses `resolved.minutes` wherever it now uses `config.slots`.
   - It logs `logger.warning("alert slot %s skipped for deadline %s: not before the next slot
     or the deadline", slot, deadline.key)` once per `(deadline.key, str(slot))`, tracked in an
     instance set.
   - `run_slot(engine, runtime, deadline, slot_index, clock)` keeps its signature. It resolves
     the slots itself and indexes `resolved.minutes`, so the ~30 existing calls in tests stay
     valid. Index 0 is the digest.
5. **Status/preview.**
   - `alert_status` uses `resolved.minutes`.
   - `status_line` keeps its `fmt` parameter, and the worker now passes a Warsaw formatter.
   - `app/core/local_time.py` gets `format_local_day(moment) -> "Fri 2026-10-09 20:00"`, with
     weekday names from a fixed English tuple so the output does not depend on the locale.
   - `python -m app.alerts status` prints `Next slot: digest Fri 2026-10-09 20:00` and the
     breaking end in the same format.
   - `python -m app.worker status` passes `format_local_day` to `status_line`, so the line
     reads `Alerts: next slot: digest Fri 2026-10-09 20:00  last alert: …`. AC11 requires
     Warsaw time there; the other worker lines stay UTC.
   - `preview` fails with `ALERT_SLOTS has no news slot` when `len(resolved.minutes) < 2` for
     that deadline. This covers both a single configured slot and a skipped one. Otherwise it
     passes `resolved.minutes[0 or 1]`.
6. **Polling window (AC8).** `polling_window(config)` = `max(90 min, last slot minutes + 10
   min)` when the last slot is a minutes slot.
   - When the last slot is a wall-clock slot (e.g. `ALERT_SLOTS=D-1@20:00`), its minutes vary
     per deadline, so the window is 90 min and the extra poll (AC9) covers that slot. The SPEC
     does not cover this case explicitly; this is the minimal reading of AC8.
   - `None` gives 90 min.
7. **Rehearsal span (AC10).** `alert_span_start(config, deadline_at) = min(first resolved
   moment, deadline_at - polling_window(config))`.
   - `check_rehearsal` raises when `rehearsal_start < real_deadline and real_start <
     rehearsal` for any real deadline. The message stays the existing one naming
     `ALERT_REHEARSAL_DEADLINE`, and a past rehearsal is still ignored.
8. **Extra poll before a slot (AC9).** The tweets module must not import alerts, so
   `app/tweets/schedule.py` gets a generic hook.
   - `next_poll_at(deadlines, last, now, window=WINDOW, slot_times=None)`, where
     `slot_times: Callable[[datetime], Sequence[datetime]] | None` maps a deadline to its slot
     moments.
   - Rule, applied after the window-start rule and before the rate-limit rule: for every
     deadline in `deadlines` and every slot moment `s` with `s < deadline - window`, let
     `p = s - PRE_SLOT_POLL` (`timedelta(minutes=10)`). If `last.started_at < p` and
     `candidate >= s`, then `candidate = min(candidate, p)`. "The regular schedule would not
     poll between p and the slot" is exactly `candidate >= s`, given that the last poll was
     before `p`.
   - `TweetPoller`/`start_poller` get a `slot_times` parameter (default `None`) and pass it
     through.
   - `worker/cli.py::_polling` returns `(window, extra_deadlines, slot_times)`, where
     `slot_times = lambda d: slot_moments(config.slots, d)`, or `None` without alerts.
   - `worker status` passes the same `slot_times` to `next_poll_at`, so `next poll` shows it.

Patterns reused: `ConfigError` naming the variable (`app/alerts/config.py`), `WARSAW`/
`format_local` (`app/core/local_time.py`), `FixedClock`/`VirtualClock` (`tests/delivery/fakes.py`,
`tests/alerts/test_loop.py`), `alerts_runtime(..., config=...)` (`tests/alerts/helpers.py`),
the `cli(...)` fixture with `AlertsSetup` (`tests/worker/test_cli.py::_alerts_setup`), `_poll`
(`tests/tweets/test_schedule.py`), `caplog` for log assertions.

Reference values for tests: deadlines in Oct 2026 are CEST (UTC+2), so Warsaw 20:00 is 18:00Z.
DST ends Sun 2026-10-25 03:00 CEST. For a deadline Sun 2026-10-25 15:30 Warsaw (14:30Z),
D-1@20:00 is Sat 2026-10-24 18:00Z, which is 1230 minutes (20 h 30 min, not 19 h 30 min). DST
starts Sun 2027-03-28. For a deadline Sun 2027-03-28 15:30 Warsaw (13:30Z), D-1@20:00 is Sat
2027-03-27 19:00Z, which is 1110 minutes.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 1 | `tests/alerts/test_config.py::test_alert_slots_defaults_and_mixed_values`, `::test_invalid_alert_slots_name_the_variable` (parametrised); `tests/alerts/test_cli.py::test_cli_rejects_invalid_alert_variable`; `tests/worker/test_cli.py::test_worker_rejects_invalid_alert_slots_naming_the_variable` |
| AC2 | 1 | `tests/alerts/test_config.py::test_retired_slots_minutes_variable_stops_the_start`; `tests/alerts/test_cli.py::test_cli_rejects_retired_slots_minutes`; `tests/worker/test_cli.py::test_worker_rejects_retired_slots_minutes` |
| AC3 | 2 | `tests/alerts/test_schedule.py::test_default_slots_resolve_per_deadline_type` (parametrised over five deadline types), `::test_wall_clock_slot_across_dst_change` |
| AC4 | 3 | `tests/alerts/test_loop.py::test_default_slots_digest_day_before_news_and_breaking_from_t60` |
| AC5 | 2, 3 | `tests/alerts/test_schedule.py::test_wall_clock_slot_minutes_are_whole_minutes_to_deadline`; `tests/alerts/test_loop.py::test_default_slots_digest_day_before_news_and_breaking_from_t60` (keys `digest:<n>`, `news:60`, and a second tick sends nothing) |
| AC6 | 2, 3 | `tests/alerts/test_schedule.py::test_out_of_order_wall_clock_slot_is_skipped`; `tests/alerts/test_loop.py::test_out_of_order_slot_skipped_with_one_warning` |
| AC7 | 3 | `tests/alerts/test_loop.py::test_restart_after_missed_evening_digest_sends_it_at_once` |
| AC8 | 5 | `tests/alerts/test_schedule.py::test_polling_window` (rewritten); `tests/worker/test_cli.py::test_status_tweet_window_follows_the_last_slot` |
| AC9 | 6 | `tests/tweets/test_schedule.py::test_extra_poll_before_a_slot_outside_the_window` and `::test_no_extra_poll_when_the_regular_schedule_polls_before_the_slot`; `tests/tweets/test_loop.py::test_poller_polls_ten_minutes_before_a_slot`; `tests/worker/test_cli.py::test_status_next_poll_is_ten_minutes_before_the_digest` |
| AC10 | 5 | `tests/alerts/test_schedule.py::test_rehearsal_overlap_by_alert_span` (replaces `test_rehearsal_past_ignored_and_overlap_rejected`) |
| AC11 | 4 | `tests/alerts/test_status.py::test_status_with_wall_clock_digest`; `tests/alerts/test_cli.py::test_status_shows_next_slot_last_alert_and_failures` (updated format); `tests/worker/test_cli.py::test_status_shows_alerts_line` (updated to Warsaw format); `tests/core/test_local_time.py::test_format_local_day` |
| AC12 | 4 | `tests/alerts/test_cli.py::test_preview_rejects_bad_input` (message `ALERT_SLOTS`), `::test_preview_prints_and_writes_nothing` (unchanged, still green) |
| AC13 | 5 | `tests/worker/test_cli.py::test_run_logs_the_configured_slots` |
| AC14 | 7 | `tests/test_readme.py::test_deployment_documents_alerts` (variables list updated), `::test_deployment_documents_alert_slots`; `tests/test_docs.py::test_backlog_has_deadline_day_slot_entry`, `::test_decisions_cover_alert_slots`; `tests/test_env_example.py::test_every_alert_variable_is_an_empty_placeholder`, `::test_retired_alert_variable_absent_from_env_example` |

## Steps

- [x] 1. **`ALERT_SLOTS` parsing and the retired variable** (AC1, AC2). `iterations: 1` Files:
      `backend/app/alerts/config.py`, `backend/app/alerts/cli.py` (only the news-slot message
      text → `ALERT_SLOTS`), `backend/.env.example`, `backend/tests/alerts/test_config.py`,
      `backend/tests/alerts/test_cli.py`, `backend/tests/worker/test_cli.py`,
      `backend/tests/test_env_example.py`.
      Tests first:
      - In `test_config.py`, replace the `ALERT_SLOTS_MINUTES` cases with `ALERT_SLOTS`. Cover
        the default `(WallClockSlot(1, time(20, 0)), 60)`; mixed `"D-2@18:00, D-1@20:00,120,30"`;
        empty → default; invalid `D-0@20:00`, `D-1@25:00`, `D-1@20:60`, `D1@20:00`, `0`, `abc`,
        `30,120`, `120,120`, `60,D-1@20:00` → `ConfigError` matching `ALERT_SLOTS`; a non-empty
        `ALERT_SLOTS_MINUTES` → `ConfigError` matching `replaced by ALERT_SLOTS`; and
        `format_slots` of the default == `"D-1@20:00,60"`.
      - In `test_cli.py`/`worker/test_cli.py`, switch the invalid-variable tests to
        `ALERT_SLOTS=30,120` and add the retired-variable tests (exit 1, stderr contains
        `ALERT_SLOTS_MINUTES` and `ALERT_SLOTS`).
      - In `test_env_example.py`, map `alert_slots` → `ALERT_SLOTS` and exclude the retired
        field `alert_slots_minutes` from the coverage set through an explicit
        `_RETIRED_ALERT_FIELDS`. Add `test_retired_alert_variable_absent_from_env_example`.
      Then the product change, and in `.env.example` replace the `ALERT_SLOTS_MINUTES` block
      with `ALERT_SLOTS=` and a comment that explains both slot forms and the default.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_config.py
      tests/alerts/test_cli.py tests/test_env_example.py tests/worker/test_cli.py -k "slots or
      alert or env"` then `cd backend && uv run pytest -q tests/alerts tests/worker
      tests/test_env_example.py`
- [x] 2. **Slot resolution per deadline** (AC3, AC5, AC6 pure part). `iterations: 1` Files:
      `backend/app/alerts/schedule.py` (`ResolvedSlots`, `resolve_slots`, `slot_moments`),
      `backend/tests/alerts/test_schedule.py`.
      Tests first:
      - `test_default_slots_resolve_per_deadline_type`, parametrised with Warsaw deadlines
        parsed through `parse_local`, asserting the digest moment and the news moment:
        - Fri 2026-10-09 19:30 → Thu 2026-10-08 20:00 and Fri 18:30;
        - Sat 2026-10-10 12:00 → Fri 20:00 and Sat 11:00;
        - Sat 2026-10-10 14:30 → Fri 20:00 and Sat 13:30;
        - Wed 2026-10-14 19:30 → Tue 20:00 and Wed 18:30;
        - Sun 2026-10-11 15:30 → Sat 20:00 and Sun 14:30.
      - `test_wall_clock_slot_across_dst_change`: the autumn case gives 1230 minutes and the
        spring case 1110 (see Approach).
      - `test_wall_clock_slot_minutes_are_whole_minutes_to_deadline`.
      - `test_out_of_order_wall_clock_slot_is_skipped`:
        - `(D-1@20:00, 1500)` for Sat 12:00 → minutes `(1500,)`, skipped `(D-1@20:00,)`;
        - `(D-1@23:59, 60)` for a deadline Sat 00:30 → skipped;
        - `(D-1@20:00, D-2@20:00, 60)` → skipped `D-1@20:00`;
        - a minutes-only config is never skipped.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_schedule.py`
- [ ] 3. **Alerts loop and `run_slot` on resolved slots** (AC4, AC5, AC6 warning, AC7). Files:
      `backend/app/alerts/loop.py`, `backend/app/alerts/service.py`,
      `backend/tests/alerts/test_loop.py`.
      Tests first, with `alerts_runtime(..., config=AlertConfig((WallClockSlot(1, time(20, 0)),
      60), 3, Decimal("15"), None))`, the seeded deadline `DEADLINE_AT` from
      `tests/alerts/test_slots.py` and claims created before the digest moment:
      - `test_default_slots_digest_day_before_news_and_breaking_from_t60`:
        - a tick one minute before the resolved digest sends nothing;
        - at the digest moment, it sends the digest with key `alert:2026/27:gw6:digest:<n>`,
          where `<n>` is computed from `resolve_slots`, and the row has `slot_minutes == n`;
        - a repeated tick sends nothing new;
        - at T-60 the news slot is recorded (key `…:news:60`);
        - a post extracted after T-60 produces a breaking alert;
        - a post extracted between the digest and T-60 does not.
      - Reference values: `DEADLINE_AT` (test_slots) = 2026-09-29T20:00Z = Tue 22:00 Warsaw,
        so the digest resolves to Mon 2026-09-28 20:00 Warsaw = 2026-09-28T18:00Z (1560
        minutes, key `…:digest:1560`); claims and extractions are seeded before 18:00Z and
        after `PREVIOUS` (2026-09-26T18:00Z) so they fall in the alert window.
      - `test_restart_after_missed_evening_digest_sends_it_at_once`: a fresh `AlertLoop` with
        the clock at the digest moment + 13 h (before T-60) sends the digest in its first tick.
      - `test_out_of_order_slot_skipped_with_one_warning`: config `(D-1@20:00, 1600)` (for
        this `DEADLINE_AT` the wall-clock slot is 1560 minutes before, after the 1600 slot),
        three ticks. The out-of-order slot is never sent, the 1600 slot works as the digest, and
        `caplog` holds exactly one warning that contains the deadline key and `D-1@20:00`.
      Then the product change: `run_slot` indexes `resolve_slots(runtime.config.slots,
      deadline.deadline_at).minutes`; `tick` uses the resolved minutes and the once-only warning
      set.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_loop.py
      tests/alerts/test_slots.py tests/alerts/test_breaking.py tests/alerts/test_end_to_end.py`
- [ ] 4. **Status, worker `Alerts:` line and preview** (AC11, AC12). Files:
      `backend/app/core/local_time.py` (`format_local_day`), `backend/app/alerts/status.py`,
      `backend/app/alerts/cli.py`, `backend/app/worker/cli.py` (Alerts line formatter only),
      `backend/tests/core/test_local_time.py` (exists; add the test), `backend/tests/alerts/test_status.py`,
      `backend/tests/alerts/test_cli.py`, `backend/tests/worker/test_cli.py`.
      Tests first:
      - `test_format_local_day`: 2026-10-09 18:00Z → `Fri 2026-10-09 20:00`.
      - `test_status_with_wall_clock_digest`: default config. The next slot is a digest at the
        resolved moment; after recording `digest:<n>` the next slot is news at T-60; after
        `news:60` it shows breaking until the deadline.
      - Update the alerts CLI status test to expect `Next slot: digest <format_local_day>`.
      - Update the worker `test_status_shows_alerts_line` to expect the Warsaw format for the
        `(120, 30)` setup (`D6` = 2026-10-10T10:00Z = Sat 12:00 Warsaw): `Alerts: next slot:
        digest Sat 2026-10-10 10:00`. The same `fmt` renders `breaking until` and the `last
        alert` time, so those expectations move to Warsaw too (`breaking until Sat 2026-10-10
        12:00`, `last alert: news Sat 2026-10-10 11:30 failed`).
      - In `test_preview_rejects_bad_input`, expect `ALERT_SLOTS has no news slot` for a
        single-slot config.
      Automatic verification: `cd backend && uv run pytest -q tests/core tests/alerts/test_status.py
      tests/alerts/test_cli.py tests/worker/test_cli.py -k "status or preview or local"`
- [ ] 5. **Fast-polling window, rehearsal span, start log** (AC8, AC10, AC13). Files:
      `backend/app/alerts/schedule.py` (`polling_window`, `alert_span_start`,
      `check_rehearsal`), `backend/app/worker/cli.py` (start log through `format_slots`),
      `backend/tests/alerts/test_schedule.py`, `backend/tests/worker/test_cli.py`.
      Tests first:
      - `test_polling_window`: `None` → 90; default → 90; `(120, 30)` → 90 (last slot 30 + 10
        < 90); `(D-1@20:00, 100)` → 110; `(D-1@20:00,)` → 90.
      - `test_rehearsal_overlap_by_alert_span`, with the default config:
        - a rehearsal Sat 18:00 after a real Sat 12:00 deadline is rejected (its digest Fri
          20:00 lies inside the real span);
        - a rehearsal 3 days away is accepted;
        - a rehearsal in the past is ignored.
        With `(120, 30)` the span starts at T-120 (the first slot is earlier than T-90):
        - a rehearsal 6 h after the real deadline is accepted;
        - a rehearsal 60 min after it is rejected;
        - spans that only touch at the boundary are accepted.
      - Worker tests build `AlertConfig` through `_alerts_setup(slots=(120, 30))`, not from
        `AlertSettings`, so "the default slots" below means passing
        `slots=(WallClockSlot(1, time(20, 0)), 60)` explicitly (a module constant
        `DEFAULT_SLOTS_TUPLE` in `tests/worker/test_cli.py`); the `_alerts_setup` default stays
        `(120, 30)` so the existing tests keep their values.
      - `test_status_tweet_window_follows_the_last_slot` (renamed from
        `…_follows_the_alert_slots`):
        - with the default slots at T-100 the mode is `sparse`;
        - at T-90 it is `window`;
        - with `(D-1@20:00, 100)` at T-105 it is `window`.
      - `test_run_logs_the_configured_slots`: with `_alerts_setup(slots=DEFAULT_SLOTS_TUPLE)`,
        `caplog` contains `alerts started: slots=D-1@20:00,60`; with `_alerts_setup()` it
        contains `slots=120,30`.
      Automatic verification: `cd backend && uv run pytest -q tests/alerts/test_schedule.py
      tests/worker/test_cli.py -k "window or rehearsal or slots or polling"`
- [ ] 6. **Extra tweet poll before a slot outside the window** (AC9). Files:
      `backend/app/tweets/schedule.py` (`PRE_SLOT_POLL`, the `slot_times` parameter),
      `backend/app/tweets/loop.py` (`TweetPoller`/`start_poller` take `slot_times`),
      `backend/app/worker/cli.py` (`_polling` returns `slot_times`; `run` and `status` pass it),
      `backend/tests/tweets/test_schedule.py`, `backend/tests/tweets/test_loop.py`,
      `backend/tests/worker/test_cli.py`.
      Tests first:
      - `test_extra_poll_before_a_slot_outside_the_window`, with slot `s` = deadline − 22 h,
        last poll at s − 25 min and a regular candidate of s + 5 min → `s − 10 min`.
      - `test_no_extra_poll_when_the_regular_schedule_polls_before_the_slot`, which checks:
        - last poll at s − 35 min (candidate s − 5) → regular candidate;
        - last poll at s − 5 min → no extra poll;
        - a slot inside the window adds nothing;
        - `slot_times=None` → unchanged behaviour;
        - a regular candidate exactly at `s` → extra poll at `s − 10 min`;
        - a rate-limit retry still wins (it is applied after).
      - `test_poller_polls_ten_minutes_before_a_slot` in `tests/tweets/test_loop.py`, modelled
        on `test_poller_uses_window_and_extra_deadlines`: a poll is recorded at s − 10 min.
      - `test_status_next_poll_is_ten_minutes_before_the_digest` in `tests/worker/test_cli.py`,
        with `_alerts_setup(slots=DEFAULT_SLOTS_TUPLE)` (see step 5), a recorded poll before p
        and the clock between them:
        `next poll` shows p in UTC format.
      Automatic verification: `cd backend && uv run pytest -q tests/tweets/test_schedule.py
      tests/tweets/test_loop.py tests/worker/test_cli.py`
- [ ] 7. **Documentation** (AC14). Files: `docs/DECISIONS.md`, `docs/DEPLOYMENT.md` (step 12),
      `docs/BACKLOG.md` (#31), `README.md` (the T-120/T-30 sentence), `backend/tests/test_readme.py`,
      `backend/tests/test_docs.py`.
      Tests first:
      - In `test_readme.py`, change `ALERT_VARIABLES` from `ALERT_SLOTS_MINUTES` to
        `ALERT_SLOTS`. Add `test_deployment_documents_alert_slots`: step 12 contains
        `D-1@20:00,60`, `ALERT_SLOTS_MINUTES` described as replaced/removed, `90 minutes`, and
        `alert span`.
      - In `test_docs.py`, add `test_backlog_has_deadline_day_slot_entry`: one row mentioning
        `deadline day`, P2, with a non-empty trigger.
      - Also add `test_decisions_cover_alert_slots`: a row with `ALERT_SLOTS` and `D-1@20:00`
        that mentions superseding 2026-10-02.
      Then write the documents:
      - DECISIONS: a new row dated 2026-10-08 for the slot format, the default, the minutes
        storage of wall-clock slots, fast polling following the last slot, the extra pre-slot
        poll and the rehearsal span. Its rejected-alternatives column comes from SPEC
        "Decisions and rejected alternatives", and it says it supersedes the 2026-10-02 slot
        default and fast-polling start.
      - DEPLOYMENT step 12:
        - `ALERT_SLOTS`, both forms, default `D-1@20:00,60`;
        - `ALERT_SLOTS_MINUTES` removed: a set value stops the start, so remove it;
        - fast polling from `max(90, last minutes slot + 10)` minutes before the deadline,
          90 with the default;
        - one extra poll 10 minutes before an earlier slot;
        - the rehearsal overlap measured by alert span, so it needs about a day of distance
          with the default; for quick tests, `ALERT_SLOTS=120,30`.
      - BACKLOG #31: P2, the deadline-day news slot (variant 4), with the trigger from SPEC
        "Out of scope".
      - README: "a digest at 20:00 Warsaw time the day before, news at T-60, …".
      Automatic verification: `cd backend && uv run pytest -q tests/test_readme.py
      tests/test_docs.py tests/test_env_example.py tests/test_deployment.py`

## Risks and traps

- **Time zones.** Resolve wall-clock slots from the deadline's **Warsaw** date, not its UTC
  date. A deadline at 00:30 Warsaw is the previous day in UTC. Always use
  `astimezone(WARSAW).date()` and `datetime.combine(..., tzinfo=WARSAW)`. Do not add a
  `timedelta(days=n)` to an aware Warsaw datetime across DST; subtract days from the `date`.
- **Seconds in the deadline.** Minutes are floored, so for a deadline with seconds the resolved
  moment is up to 59 s after the wall-clock time. FPL deadlines are whole minutes, so tests use
  whole minutes.
- **Warning spam.** The alerts loop ticks every ≤ 60 s (every 5 s in breaking). The AC6 warning
  must be logged once per deadline and slot. `alert_status`, `preview` and `check_rehearsal`
  resolve silently.
- **Moved deadline.** If FPL moves a deadline after the digest was sent, the wall-clock slot
  gets a new minutes number. That number is not in `done_slots`, and its moment has passed, so
  a second digest would go out. This is within the SPEC (AC5 accepts per-deadline numbers) but
  it is a behaviour change from spec 009; it goes to the stage summary as a backlog proposal,
  not into this plan.
- **Owner's `.env`.** `backend/.env` sets `ALERT_SLOTS_MINUTES=120,30`. Do not edit it (it holds
  credentials and belongs to the owner). After this change, any CLI run inside `backend/` exits
  with the AC2 message, which the automatic e2e check below relies on. Tests use
  `_env_file=None`, or `chdir(tmp_path)` for CLI tests, and are unaffected.
- **Module boundaries.** `app/tweets` must not import `app/alerts`. The slot moments reach the
  poller as a callable built in `app/worker/cli.py`.
- **Existing tests on old numbers.** Tests built on `(120, 30)` keep working because `int`
  slots are unchanged. Only the tests that assert the polling window of `(120, 30)` (130 min →
  now 90) and the rehearsal window change. Update them, do not delete them.
- **Locale.** `%a` depends on the locale, so use a fixed weekday tuple.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

- `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`:
  fully green.
- With the owner's real configuration (no X calls: the CLI only reads the settings),
  `cd backend && env -u TWEET_SOURCE uv run python -m app.alerts status; echo "exit=$?"`:
  expected `error: ALERT_SLOTS_MINUTES was replaced by ALERT_SLOTS …` and `exit=1` (AC2 on the
  real `.env`). Do not change `.env` to go further.
- `cd backend && env -u TWEET_SOURCE uv run python -m app.worker status; echo "exit=$?"`:
  the same message and `exit=1`.
- Record both outputs (without secrets) in this section when the step is done.

### Manual (performed by the owner)

- Remove `ALERT_SLOTS_MINUTES` from `backend/.env` (leave `ALERT_SLOTS` unset), then run
  `cd backend && uv run python -m app.alerts status`.
  Pass when: before the edit it exits 1 naming `ALERT_SLOTS_MINUTES`. After it, it prints
  `Next slot: digest Fri 2026-10-09 20:00` for the GW6 deadline (or `news Sat 2026-10-10
  <deadline − 60 min>` once the digest is recorded).
- `cd backend && uv run python -m app.worker status` with the edited `.env`.
  Pass when: the `Alerts:` line shows `next slot: digest Fri 2026-10-09 20:00`. The tweet
  ingest shows `mode: sparse` before T-90, and on Friday between 19:20 and 19:50 Warsaw time
  `next poll` is `2026-10-09T17:50:00Z` (19:50 Warsaw), unless a regular poll already falls
  between 19:50 and 20:00. In the worker log, `alerts started: slots=D-1@20:00,60` appears at
  start.
- The GW6 run with the worker running locally.
  Pass when: the digest e-mail arrives on Fri 2026-10-09 shortly after 20:00 Warsaw time, the
  news e-mail at T-60 on Sat 2026-10-10 (or a skip row in `python -m app.alerts status`
  `Last alert`), and breaking e-mails come only after T-60. `python -m app.alerts latency
  --gameweek 6` lists the digest and the breaking posts.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
      fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md`: the spec 009 item still links spec 011 and stays true (nothing new to
      tick); `docs/DECISIONS.md`, `docs/DEPLOYMENT.md`, `docs/BACKLOG.md`, `README.md` and
      `backend/.env.example` updated (step 7, step 1)
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

## Review log

2026-10-08 — plan review (fresh eye, anti-anchoring on the SPEC first).

Findings:

- `major` — Steps 5 and 6 said "with the default setup/slots" for `test_run_logs_the_configured_slots`, `test_status_tweet_window_follows_the_last_slot` and `test_status_next_poll_is_ten_minutes_before_the_digest`, but `_alerts_setup()` in `tests/worker/test_cli.py` builds `AlertConfig((120, 30), …)` directly and never reads `AlertSettings`, so the start-log test would see `slots=120,30` and fail or prove nothing about AC13/AC8/AC9 with the real default. Changed: the tests pass `slots=DEFAULT_SLOTS_TUPLE` (`(WallClockSlot(1, time(20, 0)), 60)`) explicitly; `_alerts_setup` keeps its `(120, 30)` default.
- `major` — Step 3's `test_out_of_order_slot_skipped_with_one_warning` used `(D-1@20:00, 1500)` against `DEADLINE_AT` from `tests/alerts/test_slots.py` (Tue 2026-09-29 22:00 Warsaw), where D-1@20:00 is 1560 minutes before the deadline, i.e. earlier than the 1500 slot and therefore in order: the test as written would fail against a correct implementation. Changed to `(D-1@20:00, 1600)` with the reasoning inline, and added the resolved reference values for that deadline to step 3.
- `minor` — Design 3 kept a wall-clock slot when its moment was `< bound`, but minutes are floored, so for a deadline with seconds a wall-clock slot less than a minute before a minutes slot would resolve to the same number and share an idempotency key with it (AC5). Changed: the walk compares floored minutes (`> bound`, starting at 0).
- `minor` — Step 4 asked to "check the exact value against D6" and did not say that the same `fmt` also renders `breaking until` and the `last alert` time in the worker `Alerts:` line. Changed: exact Warsaw expectations written out for all three parts.
- `minor` — Step 5 said "with `(120, 30)` the 90-minute window applies" to the rehearsal span, but by design 7 that span starts at the first slot (T-120). Changed the wording; the test cases themselves were correct.

Checked and found correct:

- Coverage: AC1–AC14 each have a step and a named proving test; the matrix matches the step list; every step writes its tests first.
- Step order: no forward dependency. Changing the default in step 1 does not break steps 2–4, because every test that reaches `polling_window`, `check_rehearsal`, the loop, status or preview builds `AlertConfig` with `(120, 30)` explicitly (`tests/alerts/helpers.py`, `test_status.py`, `test_cli.py`, worker `_alerts_setup`); the CLI only parses `AlertSettings()` in the invalid-variable tests, which stop before any slot use. The preview test asserts `"no news slot"`, so the step 1 message change keeps it green.
- `int | WallClockSlot` with `resolve_slots` feeding the existing int-based `due_slots`/`next_wake`/`breaking_open`/`done_slots` is the minimal design; the alert log, keys and schema stay unchanged (no migration, AC5).
- Out-of-order examples in step 2 resolve as stated (Sat 12:00: Fri 20:00 = 960 min < 1500; Sat 00:30: 31 min < 60; D-1 after D-2 skipped). DST references (1230 and 1110 minutes) are correct for 2026-10-25 and 2027-03-28.
- AC8 reading for a configuration whose last slot is a wall-clock slot (window stays 90 min, the AC9 extra poll covers the slot) is consistent with the SPEC's ban-risk rationale and needs no SPEC change.
- AC9 rule (`last < p` and `candidate >= s` → `min(candidate, p)`, before the rate-limit rule) matches "the regular schedule would not poll between that moment and the slot"; `app/tweets` stays free of `app/alerts` imports via the `slot_times` callable.
- Rehearsal span check and its boundary cases; past rehearsal still ignored.
- Compliance: aware UTC, Warsaw only for display/parsing, date arithmetic on the Warsaw `date`; logs carry only the deadline key and slot; no Polish product text in code (only English operator messages); DECISIONS 2026-09-28/2026-10-02/2026-10-06 respected or explicitly superseded in step 7.
- Docs: ROADMAP already links spec 011 under the spec 009 item; README line 181 (T-120/T-30) is in scope; `test_env_example.py` handles the retired field explicitly.
- E2E: automatic part runnable without touching the owner's `.env`; the three manual items each have `Pass when:`; owner summary flags (no dependency, no migration) agree with the SPEC's owner decisions.
- Language: the PLAN is in English, as `language: en` requires.

Decision: the plan is ready for implementation — all findings were fixed in place, no blocker remains, and it adds no dependency or migration.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
