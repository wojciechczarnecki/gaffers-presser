# PLAN 014 — Presser match stories, chips section and table rivalries

## Owner summary

- **Approach:** The fact sheet moves to version 3 and stays pure SQL + Python. New loaders read
  what is already collected: per-fixture `explain` stats, fixtures, clubs, deadline ownership
  and FPL team names. New modules compute match facts (`facts/matches.py`), the pitch section
  (`facts/pitch.py`), key moments (`facts/moments.py`) and rivalry swaps and podium changes
  (`facts/table.py`). `check_fact_sheet` moves to `facts/checks.py` and gains one rule per new
  fact. Then come writer prompt 5 (eight sections, 2500 characters), judge prompt 4, three
  rewritten style examples and evaluation set v3 (10 real + 20 synthetic cases).
- **Main risks:** (1) The ten new synthetic cases are hand-written JSON; the consistency check
  catches arithmetic errors but not dull stories. (2) If GW6 is finished before the real cases
  are rebuilt, the spec wants it in the set, but its overall ranks would then be current
  (the spec 013 privacy gate). The step stops and asks you rather than committing them.
  (3) One unknown per fixture: FPL gives `starts` per gameweek, not per fixture, so in a
  double gameweek an "unlucky sub" is only flagged when the player started both games.
- **New dependency:** no.
- **Data migration:** no — every fact is computed from tables that already exist.
- **Manual scenarios for the owner:** 2 — approve the three style examples, and read one real
  preview (GW5 of your league) with the new sections.

## Approach

What the plan rests on:

- `specs/014-presser-match-stories/SPEC.md` — read in full.
- `docs/CONVENTIONS.md` — read in full (no docstrings, Polish content in
  `app/content/`, tests mirror `app/`, LLM steps against a fake model, `env -u` for
  credentials, split a growing file by subdomain).
- `docs/DECISIONS.md` — searched for "presser", "fact sheet", "privacy", "pseudonym",
  "team_name": the 2026-10-09/10 presser rows (fact sheet by SQL, net points, chip effects,
  GW ranks and Overall, temperature 0.8, rank rounding, set v2); no binding rule on
  committing current overall ranks beyond the spec 013 owner decision.
- `docs/ROADMAP.md` — searched for "Stage 3": items 1–2 ticked (specs 012, 013), item 3 is
  BACKLOG #32, item 4 the glossary.
- `docs/BACKLOG.md` — searched for "#32" / last rows: #32 row text (trigger "spec 013 is
  merged"), the last item is #33, so the new item is #34.
- `docs/PROJECT.md` — searched for "presser", "FR-3", "Privacy": FR-3.1–3.3 and the privacy
  rule; nothing else bears on this spec.
- `specs/013-presser-ranks-and-voice/PLAN.md` — searched for "synthetic", "build-cases",
  "Owner decisions": the scratchpad-helper pattern for synthetic cases, the `build-cases
  --force` command line, the GW6 privacy gate and its waiver for spec 013 only.
- Code read in full: `app/presser/facts/{schema,load,build,gameweek,table,overall,season,
  __init__,errors}.py`, `app/presser/{writer,names,render}.py`,
  `app/presser/evaluation/{building,cases,cli,judge}.py`, `app/content/__init__.py`,
  `app/content/prompts/presser_writer.md`, `presser_judge.md`,
  `app/fpl/models/{reference,snapshots}.py`, `app/fpl/models/leagues.py` (top),
  `tests/presser/helpers.py`, `tests/presser/test_facts_checks.py`,
  `tests/presser/evaluation/{factories,test_eval_set}.py`,
  `tests/content/test_presser_content.py`; skimmed for names: `test_facts_gameweek.py`,
  `test_facts_table.py`, `test_service.py` (log test), `test_writer.py`, `test_runner.py`,
  `test_building.py`, `tests/test_docs.py`.
- Local database (counts only, no names): `explain` identifiers present are `minutes`,
  `goals_scored`, `assists`, `clean_sheets`, `goals_conceded`, `saves`, `bonus`,
  `yellow_cards`, `red_cards`, `own_goals`, `penalties_missed`, `penalties_saved`,
  `defensive_contribution`; each `explain` item is `{"fixture": id, "stats": [{"identifier",
  "points", "value", "points_modification"}]}`. Deadline snapshots exist for GW6 only; GW6 is
  not finished; 2 leagues, 25 members, GW1–5 collected.

### Design

**Patterns reused.** `load.py` query style (`_member_ids`, frozen dataclasses);
`gameweek.counted_players`, `_actual_counted`, `credit_captain`, `multiplier_for`;
`table.competition_ranks`; the `World` test helper in `tests/presser/helpers.py`; the
parametrised `problems_after(mutate)` pattern in `test_facts_checks.py`; the recorded-verdict
judge test `test_recorded_rank_verdict_counted` in `test_runner.py`; `assign_pseudonyms` in
`evaluation/building.py`.

**File layout** (CONVENTIONS: split by subdomain; DECISIONS 2026-09-30: a refactor a feature
needs is done in it):

- `facts/schema.py` keeps the models, `SECTIONS` and `empty_sections`; the check functions move
  to a new `facts/checks.py` (`check_fact_sheet` re-exported from `facts/__init__.py`, so
  callers importing it from `app.presser.facts` do not change). Schema would otherwise pass
  ~550 lines.
- `facts/chips.py` takes the chip code out of `gameweek.py` (`_chip_effect`, `_squad_players`,
  `squad_change`, a new `build_chips`). `gameweek.py` keeps winners, captaincy, counted players
  and `build_bench_transfers`.
- New `facts/matches.py` (per-fixture facts and per-manager contributions), `facts/pitch.py`,
  `facts/moments.py`. Rivalry swaps and podium changes go into `facts/table.py`.

**Fact sheet version 3 shapes** (precision needed by every later step; `Strict` models):

```python
SECTIONS = ("winners", "flops", "captaincy", "pitch", "bench_transfers", "chips", "table", "overall")

class TransferPair(Strict): player_in: str; player_in_points: int; player_out: str; player_out_points: int
class Hit(Strict): manager: str; transfers: int; cost: int; transfer_gain: int; paid_off: bool
                   pairs: list[TransferPair] = []
class TransferWin(Strict): <same fields as TransferMiss>          # difference = in - out
class BenchTransfers(Strict): bench, hits, transfer_misses, transfer_wins, auto_subs
class Chips(Strict): plays: list[ChipPlay] = []; squad_changes: list[ChipSquadChange] = []

PitchKind = Literal["differential_haul", "template_haul", "template_blank", "red_card",
                    "own_goal", "penalty_missed", "lucky_sub", "unlucky_sub"]
class PitchOwner(Strict): manager: str; multiplier: int          # 1, 2 (captain) or 3 (TC)
class PitchEvent(Strict): kind: Literal["red_card","own_goal","penalty_missed","lucky_sub","unlucky_sub"]
                          count: int; points: int
    # points: the stat's points for cards/own goals/penalties (negative); for lucky_sub the
    # clean-sheet points kept, for unlucky_sub the clean-sheet points missed
class PitchItem(Strict): player: str; club: str; points: int; kinds: list[PitchKind]
                         owners: list[PitchOwner]; benched: list[str] = []; without: list[str] = []
                         global_differential: bool | None; league_differential: bool
                         events: list[PitchEvent] = []
class Pitch(Strict): items: list[PitchItem] = []

class KeyMoment(Strict): pair: Literal["winner", "podium", "rivalry"]; ahead: str; behind: str
                         margin: int
                         kind: Literal["player","clean_sheet_lost","red_card","own_goal","penalty_missed"]
                         players: list[str]; club: str
                         ahead_points: int; behind_points: int; swing: int   # swing = ahead - behind

class TableRow(Strict): rank; manager; team_name: str; total_points; movement
class RivalrySwap(Strict): ahead: str; behind: str               # ahead = now ahead (overtook)
                           ahead_previous_rank: int; ahead_rank: int
                           behind_previous_rank: int; behind_rank: int
                           gap: int; neighbour_gameweeks: int
class PodiumChange(Strict): kind: Literal["new_leader", "entered", "left", "swap"]; manager: str
                            previous_rank: int | None; rank: int
                            other: str | None = None; gap: int | None = None
class Table(Strict): rows, top3, climbers, fallers, rivalry_swaps = [], podium_changes = []

class FactSheet(Strict): version: Literal[3] = 3; league; season; gameweek; managers
    average_net_points; winners; flops; captaincy; pitch: Pitch; key_moments: list[KeyMoment]
    bench_transfers: BenchTransfers; chips: Chips; table; overall; season_facts; empty_sections
```

`empty_sections`: `pitch` is empty when it has no items **and** `key_moments` is empty (a
lost clean sheet can be a story with no pitch item); `bench_transfers` when bench, hits,
misses, wins and autosubs are all empty; `chips` when plays and squad changes are empty; the
rest as today.

**Rules, one constant each** (top of the module that uses it): `HAUL_MIN_POINTS = 10`,
`BLANK_MAX_POINTS = 2` (exists), `GLOBAL_DIFFERENTIAL_BELOW = Decimal("10")`,
`LEAGUE_DIFFERENTIAL_MAX_MANAGERS = 2`, template = `in_squad * 2 >= managers`,
`TRANSFER_WIN_MIN = 6`, `CLEAN_SHEET_MIN_MINUTES = 60`, `CLEAN_SHEET_POINTS = 4` (goalkeeper
and defender), `MAX_PITCH_ITEMS = 8`, `MAX_KEY_MOMENTS = 5`, `RIVALRY_MIN_NEIGHBOUR_GAMEWEEKS = 3`.

**Contributions (who a player counted for).** Per manager, from all 15 picks of the gameweek:
the counted set is `_actual_counted(squad, autosubs)` (all 15 with Bench Boost); the credited
captain (`credit_captain`, vice when the captain did not play) gets `multiplier_for(chip)` if
counted, the others 1. A player "counted for" a manager when he is in that set. The
contribution is `points × multiplier`. Picks are loaded for every member (one query).

**Per-fixture match facts** (`matches.py`), from `explain` grouped by fixture, only for a
goalkeeper or defender (`position` 1 or 2) for the clean-sheet facts:

- the club's goals conceded come from the fixture score on the player's side; when the
  player's `team_fpl_id` is neither team of the fixture (a club change), or the score is null,
  the clean-sheet facts of that fixture are skipped;
- `lost_clean_sheet`: minutes in the fixture ≥ 60, no `clean_sheets` stat, conceded ≥ 1;
- `lucky_sub`: a `clean_sheets` stat while the club conceded ≥ 1;
- `unlucky_sub`: started the fixture, 0 < minutes < 60, the club conceded 0. "Started" per
  fixture = `starts` ≥ 1 when he played one fixture, or `starts` equals the number of fixtures
  played in a double gameweek (otherwise unknown, not flagged);
- `red_card`, `own_goal`, `penalty_missed`: the stat with `value` as `count` and its `points`,
  one event per fixture.

**Pitch** (`pitch.py`). Candidates: every player counted for a member, plus template players.
Kinds: `differential_haul` (points ≥ 10 and (global flag true or league flag true));
`template_haul` / `template_blank` (in squads of ≥ 50% of the members, points ≥ 10 / ≤ 2,
listed even when counted for nobody); the event kinds. A non-template candidate needs at
least one kind and at least one owner. `global_differential` is `ownership < 10` from the
gameweek's deadline snapshot, `None` when the gameweek has no snapshot rows or the player has
none; `league_differential` is `1 ≤ len(owners) ≤ 2`. `benched` = in the squad, not counted;
`without` = not in the squad (template kinds only). Ranking key: (not tied to a listed key
moment, no red card/own goal/penalty missed, no haul/blank kind, −points, player); a player is
"tied" when he is in a listed key moment's `players`. Cap 8.

**Table stories** (`table.py`). Positions per gameweek come from the members with a row that
gameweek, ordered like the table: `(-total_points, display name)`.

- Rivalry swap: A was ahead of B with strictly more points after GW−1 and B now has strictly
  more points than A; A and B held adjacent positions after each of GW−1, GW−2, GW−3. The
  streak is the run of adjacent gameweeks counted back from GW−1, and it needs at least 3.
  This gives none before GW4. The swap carries the competition ranks then and now, and the
  gap is B's total minus A's.
- Podium change: top 3 = the first three positions now and after GW−1 (ties ordered as in
  the table). None at GW1.
  - `new_leader`: the first position changed; `other` = the previous leader.
  - `entered`: in the top 3 now and not before; `other` = the best-placed manager who left,
    or `None` if nobody left.
  - `left`: in the top 3 before and not now; no `other`.
  - `swap`: two managers in both top 3s whose order reversed and who are not the
    `new_leader` pair; `manager` = now ahead, `other` = the manager now behind.
  - `gap` = total(manager) − total(other) when `other` is set.
  - `new_leader`, `entered` and `swap` always carry `other`, so each gives a key-moment pair;
    a `left` change has no `other`, and its story is the pair of the matching `entered`
    change (the same two managers).

**Key moments** (`moments.py`). Pairs (ahead, behind, margin), deduplicated by
(ahead, behind) with the first source kept:

1. each winner against each manager on the best net points below the winners, with
   margin = the difference in net points;
2. each podium change with `other`, with margin = `gap`;
3. each rivalry swap, with margin = `gap`.

Events per pair, with ahead_points / behind_points per manager and swing = ahead − behind:

- `player`: one per player counted for either manager, contribution × multiplier;
- `clean_sheet_lost`: a club's lost clean sheets in one fixture, grouped, each player worth
  `−CLEAN_SHEET_POINTS × multiplier`; `players` lists only the players whose multiplier
  differs between the two managers (a defender both hold alike moves nothing);
- `red_card` / `own_goal` / `penalty_missed`: event points × multiplier.

An event is listed when `swing > 0` and `swing ≥ margin`. The order is winner pairs, then
podium, then rivalry, each by −swing, then ahead and players. Cap 5. The pitch ranking runs
after the moments.

**Team names.** `load_team_names` reads `manager.team_name`. `build_fact_sheet` gets an
optional `team_names: dict[int, str] | None` that replaces them (the evaluation pseudonymises
through it, as it does names through `nicknames`).

**Variants considered.**
- (a) Key-moment players sit in the pitch items only when they carry a pitch kind; a lost clean
  sheet is a moment with its own `club` and `players`. (b) A pitch kind `clean_sheet_lost`
  would put every lost clean sheet on the pitch. Chosen: (a), because the spec lists no such
  pitch kind and the cap of 8 would fill with defenders.
- (a) A hit carries its transfer `pairs`, so `check_fact_sheet` can recompute the gain.
  (b) A bare `transfer_gain` cannot be checked against anything in the sheet. Chosen: (a),
  because spec Scope 8 asks for the gain to be checked "against its pairs".
- (a) Hand-written synthetic cases validated by a scratchpad helper, as in spec 013.
  (b) Generating them from a `World` in a test database. Chosen: (a), because it keeps the
  spec 013 pattern, and (b) would need a committed generator, which is extra scope.

**Interim red test.** From step 2 until step 11 the committed set v2 no longer validates
against `FactSheet` version 3, so `tests/presser/evaluation/test_eval_set.py` fails at
collection. Each step's verification runs with
`--ignore=tests/presser/evaluation/test_eval_set.py` until step 12 restores it (step 11 converts
the synthetic cases, step 12 rebuilds the real ones). The full
`verify.command` runs at the end.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 2 | `tests/presser/test_facts_gameweek.py::test_sheet_v3_sections_in_order`, `::test_chips_section_empty_without_chips`, `::test_bench_transfers_holds_no_chip_facts` |
| AC2 | 3 | `tests/presser/test_facts_transfers.py::test_transfer_win_boundary_6_listed_5_not`, `::test_no_transfer_wins_in_free_hit_or_wildcard_week`, `::test_transfer_wins_ranked_by_difference` |
| AC3 | 3 | `tests/presser/test_facts_transfers.py::test_hit_gain_sums_all_transfers`, `::test_hit_paid_off_only_above_cost` |
| AC4 | 1, 5 | `tests/presser/test_facts_pitch.py::test_differential_haul_boundary_10_and_9`, `::test_global_flag_boundary_9_9_and_10_0`, `::test_global_flag_null_without_snapshot`, `::test_league_flag_two_managers` |
| AC5 | 5 | `tests/presser/test_facts_pitch.py::test_template_boundary_6_of_12_and_5_of_12`, `::test_template_item_lists_with_benched_and_without` |
| AC6 | 1, 4 | `tests/presser/test_facts_matches.py::test_red_card_own_goal_penalty_missed_with_points`, `::test_double_gameweek_events_per_fixture` |
| AC7 | 4 | `tests/presser/test_facts_matches.py::test_lucky_sub`, `::test_unlucky_sub`, `::test_lost_clean_sheet`, `::test_unlucky_sub_unknown_in_double_gameweek_with_one_start` |
| AC8 | 5, 7 | `tests/presser/test_facts_pitch.py::test_item_names_club_points_owners_multiplier`, `::test_player_counted_for_nobody_not_listed`, `::test_cap_and_ranking`; `tests/presser/test_facts_moments.py::test_key_moment_player_ranked_first_on_pitch` |
| AC9 | 7 | `tests/presser/test_facts_moments.py::test_winner_pair_differential_haul`, `::test_swing_equal_margin_listed_below_not`, `::test_moments_for_rivalry_swap_and_podium_change` |
| AC10 | 7 | `tests/presser/test_facts_moments.py::test_captain_multiplier_in_swing`, `::test_lost_clean_sheet_groups_club_defenders_and_goalkeeper`, `::test_cap_five_and_order` |
| AC11 | 6 | `tests/presser/test_facts_table.py::test_rivalry_swap_after_three_neighbour_gameweeks`, `::test_two_neighbour_gameweeks_no_swap`, `::test_no_swap_before_gw4` |
| AC12 | 6 | `tests/presser/test_facts_table.py::test_podium_new_leader_entered_left_swap`, `::test_podium_ties_ranked_as_in_table`, `::test_no_podium_changes_at_gw1` |
| AC13 | 2, 3, 5, 6, 7 | `tests/presser/test_facts_checks.py::test_inconsistent_v3_fact_is_reported` (one parametrised case per rule) |
| AC14 | 8 | `tests/content/test_presser_content.py::test_writer_prompt_v5_rules`; `tests/presser/test_service.py` (stored `presser_writer@5`); `tests/presser/test_writer.py::test_prompt_version_label` |
| AC15 | 9 | `tests/content/test_presser_content.py::test_judge_prompt_v4_checks_match_facts`; `tests/presser/evaluation/test_runner.py::test_recorded_swing_and_streak_verdicts_counted` |
| AC16 | 10 | `tests/content/test_presser_content.py::test_style_examples_eight_headers_no_shared_sentence` (plus the owner's approval, manual) |
| AC17 | 11, 12 | `tests/presser/evaluation/test_eval_set.py` (all), `tests/presser/evaluation/test_building.py::test_build_pseudonymises_team_names`, `::test_real_team_name_left_raises` |
| AC18 | 2, 8 | `tests/presser/test_facts_table.py::test_rows_carry_team_name`; `tests/presser/test_service.py::test_logs_carry_no_names_with_match_facts` |
| AC19 | 13 | `tests/test_docs.py::test_backlog_32_after_spec_014`, `::test_backlog_has_match_events_source_item`, `::test_decisions_cover_match_stories` |

## Steps

- [x] 1. **Loaders and test-world helpers.** Files: `backend/app/presser/facts/load.py`, `iterations: 0`
      `backend/tests/presser/helpers.py`, new `backend/tests/presser/test_facts_load.py`.
      - Test first, in `test_facts_load.py`, through `World`:
        - `explain` is parsed per fixture, with a double gameweek giving two fixtures;
        - fixtures are loaded by ID with their scores (a null score stays null);
        - clubs map to `Team.name`;
        - ownership is `None` for a gameweek without snapshot rows and a `Decimal` per player
          otherwise;
        - team names are read per member.
      - Loaders (frozen dataclasses, the style of `load.py`):
        - `FixtureStats(fixture_id, stats: dict[str, StatLine(value, points)])`;
        - `MatchResult(starts, minutes, points, fixtures: tuple[FixtureStats, ...])`;
        - `load_match_results(session, season, gameweek, player_ids) -> dict[int, MatchResult]`
          (minutes per fixture = the `minutes` stat value, 0 if absent);
        - `PlayerInfo(name, position, club_id)` and
          `load_player_info(session, season, ids) -> dict[int, PlayerInfo]`;
        - `load_club_names(session, season) -> dict[int, str]`;
        - `FixtureScore(home_id, away_id, home_score, away_score)` and
          `load_fixtures(session, season, fixture_ids) -> dict[int, FixtureScore]`;
        - `load_ownership(session, season, gameweek) -> dict[int, Decimal] | None`;
        - `load_team_names(session, season, league_id) -> dict[int, str]`;
        - `load_squads` is reused for every member's picks.
      - `World`, keeping the existing defaults so current tests do not change:
        - `club(fpl_id, name)`;
        - `player(..., club=1)`;
        - `result(..., starts=None, explain=None)`, defaulting to today's values;
        - `fixture(fpl_id, gameweek, home, away, home_score, away_score)`;
        - `snapshot(gameweek, player_id, percent)` with fixed status, news, cost and
          `captured_at`;
        - a helper `stats(fixture_id, **identifier_to_(value, points))` that builds one
          `explain` item.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_load.py tests/presser/test_facts_gameweek.py tests/presser/test_facts_table.py`

- [x] 2. **Schema v3 skeleton: sections split, team names, checks module (AC1, AC18 rows).** `iterations: 1`
      Files: `facts/schema.py`, new `facts/checks.py`, `facts/__init__.py`, `facts/build.py`,
      `facts/gameweek.py`, new `facts/chips.py`, `facts/table.py`,
      `evaluation/building.py` (`_manager_names` only), `tests/presser/evaluation/factories.py`,
      `tests/presser/test_facts_gameweek.py`, `tests/presser/test_facts_table.py`,
      `tests/presser/test_facts_checks.py`, `tests/presser/test_writer.py`,
      `tests/presser/evaluation/test_runner.py`.
      - Tests first:
        - `test_sheet_v3_sections_in_order`: `SECTIONS` equals the AC1 tuple and
          `sheet.version == 3`;
        - `test_chips_section_empty_without_chips`: `"chips" in empty_sections` when no
          member played a chip, and absent when one did;
        - `test_bench_transfers_holds_no_chip_facts`: the model has no chip fields;
        - `test_rows_carry_team_name` in `test_facts_table.py` checks `team_name` per row, and
          that `team_names=` overrides it.
        - in `test_facts_checks.py`, the new parametrised
          `test_inconsistent_v3_fact_is_reported` starts with "empty_sections does not match
          the sections": a sheet with a chip play whose `empty_sections` lists `chips`, and
          one with no pitch items and no key moments that omits `pitch`.
      - Migrate the existing tests from `bench_transfers_chips.X` to
        `bench_transfers.X` / `chips.plays` / `chips.squad_changes`.
      - Code:
        - the models and `FactSheet` exactly as in Approach; this step adds `Pitch` (empty),
          `key_moments` (empty list) and the `Table` list fields, with their models;
        - move `_check_ranks`, `_managers_named` and `check_fact_sheet` to `checks.py`, and
          extend `_managers_named` with every new manager field: transfer wins, pitch owners,
          `benched` and `without`, key moment ahead and behind, swaps, and podium
          `manager` / `other`;
        - `gameweek.bench_transfers_chips` becomes `build_bench_transfers` and
          `chips.build_chips`;
        - `build_table` takes the team names;
        - `build_fact_sheet(..., nicknames, team_names: dict[int, str] | None = None)`;
        - `evaluation/building._manager_names` follows the new fields.
      - `factories.sheet()` gives each row a `team_name` ("Drużyna Bartasa" style, made up)
        and stays consistent.
      Automatic verification: `cd backend && uv run pytest -q tests/presser tests/content --ignore=tests/presser/evaluation/test_eval_set.py`

- [x] 3. **Transfer wins and hits judged (AC2, AC3, AC13 hit rules).** Files: `iterations: 0`
      `facts/gameweek.py`, `facts/schema.py` (`TransferWin`, `TransferPair`, `Hit` fields),
      `facts/checks.py`, new `tests/presser/test_facts_transfers.py`,
      `tests/presser/test_facts_checks.py`.
      - Tests first:
        - `test_transfer_win_boundary_6_listed_5_not`;
        - `test_no_transfer_wins_in_free_hit_or_wildcard_week`;
        - `test_transfer_wins_ranked_by_difference`;
        - `test_hit_gain_sums_all_transfers`: two transfers, +7 and −3, give a gain of 4;
        - `test_hit_paid_off_only_above_cost`: a gain of 4 against a cost of 4 is false, a gain
          of 5 is true;
        - in `test_facts_checks.py`, the parametrised
          `test_inconsistent_v3_fact_is_reported` gets "hits: a transfer gain does not match
          its transfers" and "hits: paid_off does not match the gain and the cost".
      - Code: in `build_bench_transfers`, collect per manager the one-for-one pairs outside
        Free Hit and Wildcard weeks. A pair with `in − out ≥ TRANSFER_WIN_MIN` is a win, sorted
        by (−difference, manager, player_in). Each hit gets `pairs` (sorted by player_in),
        `transfer_gain = Σ(in − out)` and `paid_off = gain > cost`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_transfers.py tests/presser/test_facts_checks.py tests/presser/test_facts_gameweek.py`

- [x] 4. **Per-fixture match facts and contributions (AC6, AC7).** Files: new `iterations: 0`
      `facts/matches.py`, new `tests/presser/test_facts_matches.py`.
      - Tests first. The pure functions take `MatchResult`, `PlayerInfo` and `FixtureScore`
        values built in the test, so no database is needed:
        - `test_red_card_own_goal_penalty_missed_with_points`;
        - `test_double_gameweek_events_per_fixture`: a red card in fixture 1 and an own goal
          in fixture 2 give two events;
        - `test_lucky_sub`;
        - `test_unlucky_sub`: 59 minutes is unlucky, 60 is not;
        - `test_lost_clean_sheet`: 60+ minutes, no CS stat and conceded 1 is lost; a
          midfielder never is;
        - `test_unlucky_sub_unknown_in_double_gameweek_with_one_start`;
        - `test_club_change_skips_clean_sheet_facts`;
        - `test_contributions_captain_vice_bench_boost_and_autosub`: captain ×2, Triple
          Captain ×3, the vice ×2 when the captain did not play, all 15 with Bench Boost, and
          an autosub in.
      - Code:
        - `fixture_facts(...) -> list[FixtureFact]` with `player_id`, `fixture_id`, `kind`,
          `count` and `points`, covering the event kinds plus `lost_clean_sheet`;
        - `contributions(squad, subs, ref, chip, results) -> dict[int, int]` (player →
          multiplier), built on `_actual_counted` and `credit_captain`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_matches.py`

- [x] 5. **Pitch section (AC4, AC5, AC8, AC13 pitch rules).** Files: new `facts/pitch.py`, `iterations: 4`
      `facts/build.py` (load every member's squad, match results, player info, clubs,
      fixtures, ownership; call `build_pitch`), `facts/checks.py`, new
      `tests/presser/test_facts_pitch.py`, `tests/presser/test_facts_checks.py`.
      - Tests first, through `World` + `build_fact_sheet`:
        - `test_differential_haul_boundary_10_and_9`;
        - `test_global_flag_boundary_9_9_and_10_0`;
        - `test_global_flag_null_without_snapshot`;
        - `test_league_flag_two_managers`: 2 owners is true, 3 is false;
        - `test_template_boundary_6_of_12_and_5_of_12`;
        - `test_template_item_lists_with_benched_and_without`;
        - `test_item_names_club_points_owners_multiplier`;
        - `test_player_counted_for_nobody_not_listed`: a red card on a benched player is not
          listed, a template blank is;
        - `test_cap_and_ranking`: 10 candidates give 8 items, cards before hauls, hauls by
          points, subs last.
      - In `test_facts_checks.py`, add the rules "a section names a manager who is not in the
        table" (via a pitch owner) and "pitch: more than 8 items".
      - Code: `build_pitch(..., moment_players: set[str]) -> Pitch` with the ranking key from
        Approach. This step passes an empty set; step 7 wires the real one.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_pitch.py tests/presser/test_facts_checks.py tests/presser/test_facts_gameweek.py`

- [x] 6. **Rivalry swaps and podium changes (AC11, AC12, AC13 table rules).** Files: `iterations: 1`
      `facts/table.py`, `facts/build.py` (pass `all_rows` and `names`), `facts/checks.py`,
      `tests/presser/test_facts_table.py`, `tests/presser/test_facts_checks.py`.
      - Tests first:
        - `test_rivalry_swap_after_three_neighbour_gameweeks`: ranks, gap and streak 3 (and 4
          when they were neighbours one gameweek longer);
        - `test_two_neighbour_gameweeks_no_swap`;
        - `test_no_swap_before_gw4`;
        - `test_tie_now_is_not_a_swap`;
        - `test_podium_new_leader_entered_left_swap` (a `swap` carries `other` = the manager
          now behind and its `gap`);
        - `test_podium_ties_ranked_as_in_table`;
        - `test_no_podium_changes_at_gw1`.
      - In `test_facts_checks.py`, add the rules:
        - "table: a rivalry swap does not match the table": ranks, gap, a streak below 3, or
          a swap before GW4;
        - "table: a podium change does not match the top 3": the kind against the top 3, rank,
          gap, or a change at GW1.
      - Code: `rivalry_swaps(all_rows, names, gameweek)` and
        `podium_changes(all_rows, names, gameweek)` as in Approach, filled into `Table`.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_table.py tests/presser/test_facts_checks.py`

- [x] 7. **Key moments (AC9, AC10, AC8 ranking rule 1, AC13 moment rules).** Files: new `iterations: 2`
      `facts/moments.py`, `facts/build.py` (moments after the table; pitch ranked with the
      moment players), `facts/checks.py`, new `tests/presser/test_facts_moments.py`,
      `tests/presser/test_facts_checks.py`.
      - Tests first:
        - `test_winner_pair_differential_haul`: a 14-point player owned only by the winner,
          with a margin of 9, is a moment of swing 14;
        - `test_swing_equal_margin_listed_below_not`;
        - `test_captain_multiplier_in_swing`: the same player, captained by the winner and
          held ×1 by the runner-up, gives swing = points;
        - `test_lost_clean_sheet_groups_club_defenders_and_goalkeeper`: one moment, with 3
          players, the club, and swing = 12 × multiplier difference;
        - `test_moments_for_rivalry_swap_and_podium_change`;
        - `test_cap_five_and_order`: winner pairs before podium before rivalry, by swing;
        - `test_key_moment_player_ranked_first_on_pitch`.
      - In `test_facts_checks.py`, add the rules:
        - "key moments: a swing is below its margin";
        - "key moments: a swing does not match the points": `swing != ahead_points −
          behind_points` or `swing <= 0`;
        - "key moments: a pair is not a winner, a swap or a podium change", with the margin
          compared to the swap or podium `gap`;
        - "key moments: more than 5".
      - Code: `key_moments(...) -> list[KeyMoment]` as in Approach.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/test_facts_moments.py tests/presser/test_facts_pitch.py tests/presser/test_facts_checks.py`

- [x] 8. **Writer prompt version 5, the 2500 limit, log privacy (AC14, AC18 logs).** Files: `iterations: 1`
      `app/content/prompts/presser_writer.md`, `app/presser/evaluation/runner.py`
      (`LENGTH_LIMIT = 2500`, the label "within 2500 characters"),
      `tests/content/test_presser_content.py`, `tests/presser/test_writer.py`,
      `tests/presser/test_service.py`, `tests/presser/evaluation/test_runner.py`.
      - Tests first:
        - `test_writer_prompt_v5_rules`: version 5, and the needles:
          - the eight headers in order: `🏆 *Manager kolejki:*`, `🤦 *Wtopa kolejki:*`,
            `©️ *Kapitanowie:*`, `⚽ *Z boiska:*`, `🪑 *Ławka i transfery:*`,
            `🃏 *Chipy:*`, `📊 *Tabela:*`, `🌍 *Overall:*`;
          - `2500`, `empty_sections`, `transfer_wins`, `paid_off`, `key_moments`, `swing`,
            `neighbour_gameweeks`, `podium_changes`, `team_name`;
          - every v4 needle except `1500`.
        - `test_writer_prompt_v5_rules` replaces `test_writer_prompt_v4_rules` (it asserts
          `version == 4`), and `test_writer_prompt_loads_with_a_version` asserts `2500`
          instead of `1500`.
        - The test asserts that the header `⚽` comes before `🪑` in the text.
        - `test_prompt_version_label` and the `test_service.py` stored version use `@5`.
        - `test_length_flag_at_the_limit` uses (2500, True) and (2501, False).
        - New `test_logs_carry_no_names_with_match_facts` in `test_service.py`. Its world adds
          a club "Klub Testowy", a fixture and a red card, so `pitch` is filled. It asserts
          that `caplog.text` holds none of: the manager names, "Synthetic XI", the club name
          and the player name, on both the success and the failure path.
      - Prompt v5 (English instructions, Polish headers):
        - Sections, keys and headers:
          - the eight sections in order, with the keys `winners`, `flops`, `captaincy`,
            `pitch` + `key_moments`, `bench_transfers`, `chips`, `table` + `season_facts`,
            `overall`;
          - each section is skipped when it is in `empty_sections`;
          - `🏆 *Managerowie kolejki:*` is kept for several winners.
        - Banter and credit:
          - banter at a transfer miss, at a hit with `paid_off` false and at a chip with a low
            or negative `effect`;
          - credit for `transfer_wins`, a hit with `paid_off` true and a strong chip.
        - Key moments:
          - the winner and flop lines may name the decisive key moment in one sentence, with
            the details left for "⚽ Z boiska";
          - a `key_moment` means "without it `behind` would have caught or passed `ahead`";
          - quote `swing` and `margin` as given, never recomputed.
        - The pitch section:
          - report the most notable `pitch.items` (the first is the most notable);
          - name the club for a lost clean sheet;
          - `global_differential` null means unknown.
        - Table stories:
          - a rivalry swap mentions `neighbour_gameweeks`;
          - podium changes are named by kind.
        - Team names: `team_name` may stand in for a manager's name now and then, exactly as
          written and never invented.
        - The 2500-character limit.
        - The v4 rules stay: natural slang, never copy, rank rounding, "średnia ligowa",
          autosub, net points, the counts that include this gameweek.
      Automatic verification: `cd backend && uv run pytest -q tests/content/test_presser_content.py tests/presser/test_writer.py tests/presser/test_service.py tests/presser/evaluation/test_runner.py`

- [x] 9. **Judge prompt version 4 (AC15).** Files: `app/content/prompts/presser_judge.md`, `iterations: 1`
      `tests/content/test_presser_content.py`, `tests/presser/evaluation/test_runner.py`.
      - Tests first:
        - `test_judge_prompt_v4_checks_match_facts`: version 4, and the needles `pitch`,
          `key moment`, `swing`, `margin`, `transfer gain`, `paid off`, `rivalry`,
          `neighbour`, `podium`, `team name`, plus the v3 rank needles;
        - `test_recorded_swing_and_streak_verdicts_counted` (modelled on
          `test_recorded_rank_verdict_counted`): a sheet with a key moment (swing 14) and a
          rivalry swap (streak 3), and a presser claiming "swing 12" and "sąsiedzi od 5
          kolejek". The recorded verdict labels both `unsupported`, so the faithfulness is
          0.0. The sheet JSON sent to the judge holds `"swing": 14` and
          `"neighbour_gameweeks": 3`.
      - Prompt v4:
        - adds these to "What is a claim": a pitch item (a player's points, club, kind,
          owners and multiplier), a key moment (the swing, the margin, "without it X would
          have won"), a transfer gain or win, a hit verdict, a rivalry swap (ranks, gap,
          neighbour streak), a podium change, and a team name used for a manager;
        - adds to "Labels": a wrong swing, margin or streak, and a team name that is not that
          manager's `team_name`, are `unsupported`.
      Automatic verification: `cd backend && uv run pytest -q tests/content/test_presser_content.py tests/presser/evaluation/test_runner.py`

- [x] 10. **Style examples rewritten (AC16).** Files: `app/content/presser_style_examples.md`, `iterations: 1`
      `tests/content/test_presser_content.py`.
      - Test first. `test_style_examples_eight_headers_no_shared_sentence` replaces the six-header
        test:
        - `HEADER_MARKS = ("🏆", "🤦", "©️", "⚽", "🪑", "🃏", "📊", "🌍")`;
        - 8 sections each, in order;
        - under 2500 characters each;
        - every example has a `⚽` paragraph that names a manager of its `🏆` or `🤦` line
          (it ties a key moment to a result);
        - no shared sentence and no shared 5-word run;
        - "gw rank" is still present, and the flagged phrases and rounding test are kept.
      - Write the 3 examples in Polish, with made-up managers, leagues and numbers:
        - each has a different case: a differential haul decides the winner; a club's
          defence loses the clean sheet and costs a podium place; a rivalry swap after
          several gameweeks as neighbours, with a hit that paid off;
        - each has a chips paragraph and one FPL team name standing in for a manager once;
        - slang is sparing and "autosub" is used;
        - the header note at the top of the file is updated to eight sections and 2500.
      Automatic verification: `cd backend && uv run pytest -q tests/content/test_presser_content.py`

- [ ] 11. **Evaluation set v3: layout, team pseudonyms, synthetic cases (AC17 synthetic
      part).** Files: `git mv backend/evals/presser/v2 backend/evals/presser/v3`,
      `app/presser/evaluation/cases.py`, `app/presser/evaluation/building.py`,
      `app/presser/evaluation/cli.py` (help texts "set v3"),
      `backend/evals/presser/v3/pseudonyms.toml`, `backend/evals/presser/v3/cases.jsonl`,
      `tests/presser/evaluation/{test_eval_set,test_cases,test_building,factories}.py`.
      - Tests first:
        - `test_eval_set.py`:
          - 30 cases, 15 dev / 15 test (5 real and 10 synthetic in each);
          - every case validates;
          - manager, league and team names all come from the pools;
          - the new tags show what they say: `differential_decides_winner` has a winner-pair
            moment of kind `player`; `lost_clean_sheet_moment` has a `clean_sheet_lost`
            moment with ≥ 2 players; `template_blank`; `red_card_own_goal`; `lucky_sub`;
            `hits_judged` has one hit paid off and one not; `rivalry_swap` has streak ≥ 3;
            `podium_new_leader`; `no_chips` lists `chips` in `empty_sections`;
            `ownership_unknown` has every `global_differential` null;
          - the `\d{6,}` test is kept.
        - `test_cases.py`: the counts follow the new limits.
        - `test_building.py`:
          - `test_build_pseudonymises_team_names`: every `team_name` comes from the team pool;
          - `test_real_team_name_left_raises`: monkeypatch `assign_pseudonyms` for the teams
            to return a real team name, and the leak check raises.
      - Code:
        - `cases.py`: paths to `v3`, `MIN_CASES = 28`, `MAX_CASES = 32`,
          `MIN_SYNTHETIC_CASES = 20`, `MIN_TEST_CASES = 15`, `EDGE_TAGS` + the 10 tags above;
        - `building.py`:
          - `Pseudonyms` gets `teams`, and `load_pseudonyms` reads `teams`;
          - `build_real_cases` passes
            `team_names=assign_pseudonyms(entry_ids, pseudonyms.teams, seed, f"{alias}-teams")`;
          - `check_for_leaks` raises "a team name of case … is not a pseudonym" when a row's
            `team_name` is outside the pool (the real full team names are already in
            `_real_names`).
        - `pseudonyms.toml` gets `teams = [...]`: at least 30 made-up Polish FPL team names
          that contain no manager pseudonym word.
      - Cases, written with a scratchpad helper (not committed) that validates each one with
        `FactSheet` and `check_fact_sheet` before writing:
        - convert the 10 v2 synthetic cases to v3:
          - the section split;
          - team names from the pool;
          - hits with `pairs`, `transfer_gain` and `paid_off`;
          - `empty_sections` recomputed;
          - tags kept;
        - add 10 new synthetic cases, 5 dev / 5 test, GW > 1, each with 1–2 previous pressers
          written for the project, under the ids `syn-<tag-with-dashes>`;
        - the 10 real v2 cases cannot validate until step 12, so the helper drops them here,
          and the set briefly has 20 cases.
      - Only `test_cases.py`, `test_building.py` and the synthetic-only assertions run green in
        this step. The composition test needs the real cases.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation/test_cases.py tests/presser/evaluation/test_building.py tests/presser/evaluation/test_cli.py`

- [ ] 12. **Set v3: real cases rebuilt (AC17 real part).** Files:
      `backend/evals/presser/v3/cases.jsonl`, and `cases.py` `REAL_CASES` only if GW6 joins.
      - With `DB=postgresql://presser:presser@localhost:5432/presser`, from `backend/`:
        - Check GW6, printing booleans and counts only:
          `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run python -c "from sqlalchemy import text; from app.db.engine import make_engine; import os; e=make_engine(os.environ['DATABASE_URL']); c=e.connect(); print(c.execute(text('SELECT finished AND data_checked FROM gameweek WHERE fpl_id = 6 ORDER BY season DESC LIMIT 1')).scalar_one(), c.execute(text('SELECT count(*) FROM manager_gameweek WHERE gameweek_fpl_id = 6 AND has_team')).scalar_one())"`.
        - If the database is unreachable, end with `RESULT: ESCALATE`, `KIND: tooling`.
        - If GW6 is finished and collected (true and a count > 0), end with
          `RESULT: ESCALATE`, `KIND: decision`, before building. Including GW6 commits its
          overall ranks while they are current (the spec 013 privacy gate, waived only for
          spec 013). Options:
          - (a) build GW1–6 and accept the current GW6 ranks under pseudonyms;
          - (b) build GW1–5 only (recommended, set v3 stays at 30 cases and #32 adds GW6+
            later);
          - (c) wait until GW7 is finished.
        - Otherwise run
          `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run python -m app.presser.evaluation build-cases --gameweeks 1-5 --force`.
          It must report 10 real cases, 20 synthetic cases kept and no missing history.
        - With GW6 under option (a): use `--gameweeks 1-6`, set `REAL_CASES = 12` and
          `MAX_CASES = 34`, and adjust the split counts in `test_eval_set.py`.
      - Never run `python -m app.presser facts` against the development database: it prints
        real names.
      - `v3/history.jsonl` stays as it is.
      Automatic verification: `cd backend && uv run pytest -q tests/presser/evaluation`

- [ ] 13. **Documents (AC19).** Files: `docs/BACKLOG.md`, `docs/DECISIONS.md`,
      `docs/ROADMAP.md`, `backend/tests/test_docs.py`.
      - Tests first, in `test_docs.py`:
        - `test_backlog_32_after_spec_013_with_inflection` becomes
          `test_backlog_32_after_spec_014`: the row holds `spec 014 is merged`,
          `writer prompt version 5`, `set v3` and `inflection`;
        - `test_backlog_has_match_events_source_item`: a `| 34 | P3 |` row holding
          `match-events`, `VAR` and the trigger text;
        - `test_decisions_cover_match_stories`: DECISIONS holds `Z boiska`, `Chipy`, `2500`,
          `key moment`, `team_name` and `version 3`.
      - BACKLOG:
        - #32 text: "on writer prompt version 5 and set v3 of spec 014", with trigger "spec
          014 is merged";
        - #34, P3: an outside match-events source (goal minutes, stoppage time, VAR,
          substitution times), with trigger "the owner misses timeline stories in the presser
          after #32".
      - DECISIONS, rows dated on the day of implementation:
        - (1) Fact sheet version 3 with eight sections, the 2500 limit and the thresholds.
        - (2) The key-moment rule: swing ≥ margin, the pairs, cap 5.
        - (3) FPL team names and club names in the fact sheet. This amends the 2026-10-09
          "manager display names only, no IDs or team names" row; logs and the repository
          still carry neither, and the evaluation set pseudonymises team names.
        - (4) Set v3: 30 cases.
      - ROADMAP Stage 3 gets a new item before the model comparison, ticked `[x]` (it merges
        with this PR): "Match stories: ⚽ Z boiska, 🃏 Chipy, transfer wins and judged hits,
        key moments, rivalry swaps and podium changes, FPL team names, set v3 (spec: [014](…))".
        The #32 line changes to "after spec 014".
      Automatic verification: `cd backend && uv run pytest -q tests/test_docs.py`

## Risks and traps

- **Interim red set test** (steps 2–11). The step commands ignore
  `tests/presser/evaluation/test_eval_set.py` until step 12 restores the full set. Do not "fix" it by loosening `FactSheet`.
- **Ties.** Table positions use `(-total, display name)` like `build_table`. A rivalry swap
  needs strictly more points before and after, and podium ties follow the table order (AC12),
  so a tie at the top can give a `new_leader` with gap 0. Its key moments then need a swing
  > 0.
- **Double gameweeks.** Events are per fixture. "Started" per fixture is known only when
  `starts` matches the fixtures played; otherwise no `unlucky_sub` is flagged (a design choice
  inside AC7, recorded in DECISIONS row 1).
- **Club changes.** `player.team_fpl_id` is the current club. When it is not a team of the
  fixture, the clean-sheet facts of that fixture are skipped instead of guessed.
- **Ownership.** `selected_by_percent` is a `Decimal`. Compare it with `Decimal("10")`,
  because 9.9 must be true and 10.0 false (AC4). A gameweek without snapshot rows gives null
  for every item.
- **Privacy.**
  - Team names reach the sheet, the stored presser and the LLM. That is allowed by the spec.
  - They never reach logs: the step 8 test covers this.
  - In the repository they appear only as pseudonyms: the step 11 leak check covers this.
  - Never print a real fact sheet in the agent session.
  - `check_for_leaks` matches every real full team name as a whole word. A real team named
    like a club or a player ("Arsenal") would now trip it on a pitch item or key moment in
    step 12. If it does, end with `RESULT: ESCALATE`, `KIND: decision`, naming the case id
    only; never weaken the check or print the name.
  - Run the DB commands with `env -u OPENROUTER_API_KEY`.
- **Prompt size.** The sheet JSON grows (8 pitch items, 5 moments, pairs). Watch the input
  tokens in #32; the caps are one constant each (open question in SPEC).
- **Polish content.** Every Polish word lives in `app/content/` (prompts, examples) or
  `evals/` (cases, pseudonyms). `tests/presser/test_no_polish_literals.py` guards the code, so
  kinds and keys stay English identifiers.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

1. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
   is fully green.
2. Real data, counts only. With `DB=postgresql://presser:presser@localhost:5432/presser`,
   run from `backend/`:
   `env -u OPENROUTER_API_KEY DATABASE_URL=$DB uv run python -c "from sqlmodel import Session; from app.db.engine import make_engine; from app.presser.facts import build_fact_sheet, check_fact_sheet; from app.presser.store import current_season; import os; e=make_engine(os.environ['DATABASE_URL']); s=Session(e); season=current_season(s); from app.fpl.models import League; from sqlmodel import select; ids=[l.fpl_id for l in s.exec(select(League).where(League.season==season))]; print([(n, g, len(f.pitch.items), len(f.key_moments), len(f.table.rivalry_swaps), len(f.table.podium_changes), check_fact_sheet(f)) for n, i in enumerate(ids) for g in range(1,6) for f in [build_fact_sheet(s, season, i, g, {})]])"`.
   Expect:
   - every check list is `[]`;
   - no rivalry swaps at GW1–3;
   - no podium changes at GW1;
   - at least one league-gameweek with pitch items.
   The command prints counts only, never names; `n` is the league ordinal.
3. `uv run python -m app.presser.evaluation --help` and `build-cases --help` name set v3.
4. `uv run pytest -q tests/presser/evaluation/test_eval_set.py` passes on the committed v3 set.

### Manual (performed by the owner)

- Read the three rewritten style examples in `backend/app/content/presser_style_examples.md`.
  Pass when: you approve the text at the final review (eight sections, natural slang, a pitch
  paragraph that ties a key moment to a result).
- Generate one real preview with your `.env`: in `backend/`, run
  `uv run python -m app.presser preview --league <your league ID> --gameweek 5`.
  Pass when: the printed presser has the `⚽ *Z boiska:*` section, has `🃏 *Chipy:*` only if
  someone played a chip in GW5, is at most 2500 characters, and its key moment and team
  names match what you know of the gameweek.

## Definition of Done

- [ ] all steps ticked
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` fully green
- [ ] end-to-end verification (automatic) performed, result recorded here
- [ ] `docs/ROADMAP.md` updated; `docs/DECISIONS.md` and `docs/BACKLOG.md` updated
- [ ] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

## Review log

2026-10-10 — plan review (fresh context, under `/pipeline:ship`).

Findings:

- `major` — Approach, podium changes: a `swap` had no `other`, so a podium swap gave no
  key-moment pair (AC9 "for each podium change") and no gap to check. Now `other` = the
  manager now behind, with its `gap`; stated that `left` is covered by the matching `entered`
  pair; step 6's podium test asserts it.
- `minor` — Approach, key moments: a grouped `clean_sheet_lost` did not say which defenders
  it lists; now only those whose multiplier differs between the two managers.
- `minor` — Step 2: AC13's "`empty_sections` against the sections" rule had no case for the
  new `chips` / `pitch` emptiness; added as the first case of the parametrised test.
- `minor` — Step 8: `tests/content/test_presser_content.py::test_writer_prompt_loads_with_a_version`
  asserts `1500` and `test_writer_prompt_v4_rules` asserts version 4; the step now says both
  change (to `2500`, and replaced by the v5 test).
- `minor` — Risks: `check_for_leaks` matches real full team names as whole words, so a real
  team named like a club or player could trip it on the new pitch / key-moment facts in
  step 12; added the escalation rule (never weaken the check).

Checked and found correct:

- Every AC1–AC19 has steps and a named proving test; the matrix matches the steps.
- Step order has no forward dependency: loaders (1) → schema (2) → transfers (3) → match facts
  (4) → pitch with an empty moment set (5) → table stories (6) → moments wiring the pitch
  ranking (7) → prompts, examples, set, docs.
- Every user of `bench_transfers_chips` (`schema.py`, `gameweek.py`, `build.py`,
  `evaluation/building.py`, `presser_writer.md`, `test_facts_gameweek.py`, `test_writer.py`)
  is in step 2 or step 8; stored presser JSONB is never re-validated against `FactSheet`, so
  version 3 needs no migration.
- `LENGTH_LIMIT` / "1500" appear only in `runner.py`, the writer prompt and the style
  examples, all covered by steps 8 and 10.
- The interim red `test_eval_set.py` (module-level `load_cases`) is the only test reading the
  committed set; `build-cases --force` keeps the synthetic cases, so dropping the real v2
  cases in step 11 lets step 12 rebuild them; the real dev/test split
  (`(gameweek + index) % 2`) gives 5 / 5 for 2 leagues × GW1–5.
- `World` already has club 1 "Synthetic FC" and team names "Synthetic XI {id}"; `Fixture`,
  `Team`, `DeadlineSnapshotPlayer.selected_by_percent` (`Numeric(5,1)`) and `starts` exist
  as the loaders assume; `current_season`, `League` and `preview` exist for the E2E commands.
- No new dependency, no migration, no new environment variable; the owner summary flags
  match. Manual items both have `Pass when:`; nothing automatable is left to the owner.
- Conventions: Polish only in `app/content/` and `evals/`, English kinds and keys, tests
  first in every step, logs checked for manager, team, club and player names.
- DECISIONS: the 2026-10-09 "no team names" row is amended explicitly in step 13 as the SPEC
  decides; net points, chip effects, temperature and the pass rule are untouched.
- The GW6 gate in step 12 escalates rather than committing current overall ranks, which
  matches the spec 013 owner decision.

Approval: the plan covers every AC with tests-first steps and exact commands, needs no new
dependency or migration, and its findings were fixable in place, so it is ready for
implementation.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

- `minor` — Step 2: the team-name pseudonymisation of step 11 (`Pseudonyms.teams`, `team_names` in `build_real_cases`, the leak check, a `teams` pool in the committed `pseudonyms.toml`) was pulled forward, because team names in the sheet otherwise broke `test_building.py` in step 2.
- `minor` — Step 2: `test_nicknames_used_everywhere` no longer forbids `Realni` / `Synthetic XI` in the sheet JSON, since team names are deliberately part of fact sheet version 3 (SPEC Decisions).
- `minor` — Step 2: `_managers_named` in `checks.py` is public (`managers_named`) and `evaluation/building._manager_names` reuses it; `gameweek._points` / `_actual_counted` became public (`player_points`, `actual_counted`) for `chips.py`.

## Final review

_(filled in by /pipeline:final-review — one line per finding: `- **F<n>** `<blocker|worth-fixing|nit>` — …`)_
