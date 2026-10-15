# PLAN 016 — Retrieval evaluation tooling: the production-shaped slice and the session judge

## Owner summary

- **Approach:** First, one small shared module, `app/core/eval_results.py`. It holds the run
  header, the atomic write and the overwrite guard. All four evaluations switch to it, and
  the presser `summary` ranks runs only within a group of the same set and prompt version.
  `scripts/session_judge.sh` then takes the verdict schema as a parameter. Next, the
  retrieval evaluation moves to `python -m app.retrieval.evaluation` and gets set v2: a
  corpus that carries the source-post flag, a `player` slice built from stored extractions
  for a gameweek deadline, and v1's queries carried in as the `free` slice. On top come:
  - the three query variants, shared with corroboration (production stays on `web_name`);
  - recall at the judge's cut per mode, variant and depth;
  - the embedding-cost guard, the `compare` table and pooling;
  - the session judge's export, estimate, import and agreement, and your sample review.
  Last come the documents. Nothing runs against your Compose database in this spec.
- **Main risks:**
  1. The size: 20 steps over all four evaluations. Each step is closed and has its own tests,
     but the final review has a large diff to read.
  2. The source-post rule is copied into Python for the frozen corpus. A container test
     checks the copy against `source_post_sql`, and loading a corpus refuses to go on if the
     two disagree.
  3. The existing presser result files have no header. After this change `summary` shows
     them apart, under "no header", and picks no winner from them. This is what AC17 asks
     for, but the `summary` output changes.
  4. `evaluate` and pooling load the corpus into the `retrieval_eval` schema of whatever
     `DATABASE_URL` points to, as set v1 already does. In spec 017 that should be a scratch
     container, not your Compose database (see Risks).
- **New dependency:** no.
- **Data migration:** no. The evaluation lives in the `retrieval_eval` schema, which it
  recreates itself, and in files. SPEC → "Owner decisions" says the same.
- **Manual scenarios for the owner:** 1. A short interactive `review` on a small hand-made
  set, to check that the prompt and the display are usable before spec 017's real review.

## Approach

What the plan rests on:

- `specs/016-retrieval-evaluation-tooling/SPEC.md`: read in full, `## Owner decisions`
  included.
- `docs/CONVENTIONS.md`: read in full. The rules this plan follows:
  - no docstrings, and line length 100;
  - tests mirror `app/`, and tests first or together with the code;
  - PostgreSQL tests run in the container (`db` / `db_engine` fixtures in
    `backend/tests/conftest.py`);
  - LLM steps run against fakes, and evaluation sets never run inside `pytest`;
  - Polish product content lives in `app/content/` (prompts in `app/content/prompts/`);
  - a growing file is split into a package by subdomain.
- `docs/DECISIONS.md`: searched for "retrieval", "evaluation", "judge", "boundar". The search
  found:
  - the 2026-09-29 set v1 row (frozen corpus, `retrieval_eval` schema, binary relevance,
    recall@5/10 and MRR, en/pl);
  - row 54 (no shared evaluation framework);
  - the 2026-10-12 session-judge row;
  - the 2026-10-15 rows on module boundaries and the Compose database as production;
  - the 2026-10-15 set v2 labelling row (line 106). It is already in the file, committed
    with the SPEC, so AC19 only checks it.
- `docs/ROADMAP.md`: searched for "017" and "retrieval". Line 80 says "planned as 017" for
  extraction v2, and the retrieval item links only spec 016.
- `docs/BACKLOG.md`: searched for #43–#48 and "017". Rows 43, 44, 45 and 48 are closed by this
  spec. Row 46 keeps only the extraction part, now spec 018. Row 47's trigger moves to 018.
  The header says the PR that does an item removes its row.
- `docs/EVALUATION.md`: read in full (the session-judge rules, the presser procedure and
  lessons 1–6).
- `docs/reports/code-audit-2026-10.md`: read sections A1–A4 and N5 (lines 214–312).
- `README.md`: searched for "retrieval". Lines 167–182 list the moved commands `review` and
  `evaluate` under `python -m app.retrieval`. `backend/tests/test_readme.py` checks the
  README and DEPLOYMENT command lists. Of the retrieval commands it checks only `index`,
  `status` and `search`, which do not move.
- Code read in full:
  - `backend/app/retrieval/cli.py`;
  - every file of `backend/app/retrieval/evaluation/`;
  - `backend/app/retrieval/search.py`;
  - `backend/app/corroboration/sources.py`;
  - `backend/app/tweets/classes.py`;
  - `backend/app/fpl/deadlines.py`;
  - `backend/app/presser/evaluation/{runner,summary,cli}.py`;
  - `backend/app/corroboration/evaluation/runner.py`;
  - `backend/app/extraction/evaluation/runner.py`;
  - `scripts/session_judge.sh`;
  - `backend/tests/test_module_boundaries.py`;
  - `backend/tests/test_shared_code.py`.
- Code searched:
  - `extraction/store.py` (`current_extractions`, `EventRecord`);
  - `extraction/linking.py` (`load_aliases`, `PlayerAlias`);
  - `fpl/models/reference.py` (`Gameweek`, `Player`, `Team`, `Fixture.kickoff_at`);
  - `tweets/models.py` (`Tweet.quoted_x_id`, `ListMembership`);
  - `llm/pricing.py`, `llm/models.py` (`load_model_settings`) and `retrieval/config.py`
    (`resolve_embedding`);
  - `corroboration/service.py:173` (the anchor is the newest claim by `(created_at, x_id)`);
  - `extraction/cli.py:327-393` (`evaluate`, a plain write with no guard);
  - `corroboration/evaluation/cli.py:243-276`;
  - the seven `os.replace` copies (`git grep os.replace backend/app`).
- Test helpers to reuse:
  - `backend/tests/corroboration/helpers.py` (`seed_reference`, `add_claim`,
    `add_extraction`);
  - `backend/tests/retrieval/helpers.py` (`add_tweet`, `PRICES`, `FixedClock`);
  - `backend/tests/retrieval/fakes.py` (`FakeEmbedder`, `hash_vector`);
  - the `eval_db` fixture of `backend/tests/retrieval/evaluation/test_runner.py`.

### Design

**1. Shared result module — `backend/app/core/eval_results.py`.**

`app.core` has no import edges, and all four evaluations may already import it, so
`test_module_boundaries.py` stays unchanged. The module holds only these pieces (row 54):

```python
NO_HEADER = "no header"
class ResultExistsError(AppError): ...           # message: "<path> exists; pass --force to overwrite it"
def git_state(cwd: Path = REPO_ROOT) -> tuple[str | None, bool | None]   # rev-parse HEAD, status --porcelain; (None, None) without git
def make_header(*, set_version: str | None, model: str | None, prompt_version: str | None,
                reasoning_effort: str | None, judge: str | None, now: datetime,
                extra: Mapping[str, Any] | None = None) -> dict[str, Any]
def read_header(data: Mapping[str, Any]) -> dict[str, Any] | None   # None = a file without a header
def write_text_atomic(path: Path, text: str) -> None   # tmp next to the target + os.replace; mkdir parents
def write_lines_atomic(path: Path, lines: Sequence[str]) -> None
def write_json_atomic(path: Path, data: Mapping[str, Any]) -> None   # indent=2, ensure_ascii=False, trailing newline
def check_new_result(path: Path, force: bool) -> None   # raises ResultExistsError
```

- The header is the top-level `"header"` key of a result file. Its keys are `set_version`,
  `model`, `prompt_version`, `reasoning_effort`, `judge`, `date` (ISO, UTC), `git_commit` and
  `git_dirty`, plus the module's `extra` keys. A field that does not apply is `null`.
- The set version is the name of the directory that holds the set's file (`v1`, `v7`). For
  retrieval set v2 it is the `version` in `set.json`.
- The reasoning effort is the model's row in `app/llm/models.load_model_settings()`, or
  `null` when the model has no row.
- The guard applies to *creating* a run file (`evaluate`). The commands that update a run
  in place (presser `review`, `judge-review`, `import-judge`) write atomically without the
  guard, because the file is theirs to update.

**2. Set v2 — `backend/evals/retrieval/v2/` (created by spec 017; this spec writes only the
code and its tests).**

- `set.json`: `{"version": "v2", "list_handles": [...] | null, "corpus_since": ... | null,
  "corpus_until": ..., "exported_at": ...}`.
  - `list_handles` is the `handles` of the latest `list_membership` row at export time
    (`ORDER BY fetched_at DESC, id DESC`, as `source_post_sql` reads it).
  - `null` means that no row exists or that its handles are null. Both mean "every author
    counts as a list author" in `list_post_sql`.
- `load_set(dir)` reads `set.json` when it exists. Otherwise the version is the directory
  name, so `v1` still loads.
- `corpus.jsonl`: `CorpusPost` gains `quoted_x_id: int | None = None` and
  `is_source: bool | None = None`. Set v1's lines lack both and load unchanged.
- `queries.jsonl` holds a discriminated union on `kind`:
  - **`FreeQuery`** is v1's `Query` plus `kind: Literal["free"] = "free"`. A v1 line has no
    `kind` and parses as a free query. A plain `Field(discriminator="kind")` rejects a line
    without the tag, so the union uses a callable `Discriminator` that returns `"free"` when
    `kind` is missing (or a `TypeAdapter` with a before-validator that sets it). The class
    keeps the name `Query` as an alias, so the existing imports work.
  - **`PlayerQuery`** has these fields:
    - `kind="player"`, `id` (`p-gw{N}-{fpl_id}`) and `split`;
    - `player`: `season`, `fpl_id`, `web_name`, `first_name`, `second_name`, `team`, and
      `aliases: list[str]`;
    - `gameweek`, `deadline_at`, `window_start`, `window_end` (= `deadline_at`), and
      `window_rule: Literal["alerts", "after-last-match"]`;
    - `previous_last_match_end: datetime | None`;
    - `claimed: list[ClaimedPost]` (`x_id`, `created_at`, `stale`);
    - `judgements`.
  - `split` reuses v1's `DEV_SHARE` (0.3), seeded. `--split` therefore means the same thing
    for both slices, and spec 017 can tune on dev.
- **Judgements.** The `Judgement` model stays `{x_id, relevant, reviewed, labelled_by}`. Set
  v2 allows several entries per post, one per labeller:
  - a judge's entry has `labelled_by=<judge name>` and `reviewed=False`;
  - the owner's entry has `labelled_by="owner"` and `reviewed=True`.
  The **reference label** of a pair is the `reviewed=True` entry if one exists. Otherwise it
  is the entry of `--reference-judge`. Otherwise the pair is unjudged. Set v1's reviewed
  entries, if any, count as the owner's under the same rule.
- **Source posts.** `is_source` is computed at export over the exported posts and the list
  snapshot, by `source_flags(posts, list_handles)`. This is a Python copy of `source_post_sql`:
  - `handles is None` → every post is a source;
  - otherwise a post is a source if `author_handle.lower() in handles`, or if a corpus post
    by a listed author quotes it.
  The copy is computed over the corpus rather than with SQL over the live tables for two
  reasons:
  - The export stays read-only. The live SQL would need the evaluation schema in the Compose
    database.
  - A quote posted after `--until` did not exist when the alert ran.
  Two checks hold the copy to the SQL rule:
  - `load_eval_corpus` creates `list_membership` in `retrieval_eval` (one row with the
    snapshot, or none when `null`) and loads `quoted_x_id`. It then compares
    `SELECT x_id FROM tweet t WHERE source_post_sql('t')` with the flags, and an
    `EvaluationError` names the first mismatching IDs.
  - The AC1 container test checks the same with search.
  Creating `list_membership` in the evaluation schema also ends an existing hazard: until
  now, an unqualified `list_membership` resolved through `search_path` to `public`.

**3. Query variants — `backend/app/retrieval/variants.py` (production code, so corroboration
can import it; retrieval may not import corroboration).**

```python
Variant = Literal["web_name", "full_name", "names"]
VARIANTS: tuple[Variant, ...] = ("web_name", "full_name", "names")
@dataclass(frozen=True)
class PlayerNames: web_name: str; first_name: str = ""; second_name: str = ""; aliases: tuple[str, ...] = ()
def has_initial(web_name: str) -> bool            # re: one letter then "." at the start, e.g. "B.Fernandes"
def variant_text(names: PlayerNames, variant: Variant) -> str
def load_player_names(session: Session, season: str, fpl_id: int,
                      aliases: list[PlayerAlias] | None = None) -> PlayerNames | None
```

- `web_name` → the `web_name` as it is.
- `full_name` → `"{first_name} {second_name}"`.
- `names` → a space-joined, case-insensitively de-duplicated list of three parts:
  - the `web_name` with a leading initial dropped;
  - the last token of `second_name`;
  - the aliases.
  The full-text leg already ORs every lexeme (`string_agg(... ' | ')` in `search.py`), so a
  space is the OR.
- For `B.Fernandes` (Bruno / Borges Fernandes):
  - `names` = `"Fernandes"`;
  - `full_name` = `"Bruno Borges Fernandes"`;
  - `web_name` stays the one host token `b.fernandes`.
- A missing first or second name falls back to the `web_name`. The text is never empty.

**4. Corroboration (AC6).** `sources.py` gets `QUERY_VARIANT: Variant = "web_name"`.
`retrieval_candidates(..., variant: Variant | None = None)` reads
`variant or QUERY_VARIANT` at call time, so a test can monkeypatch the constant.
- With `web_name` it uses `player.web_name` with no lookup, so the code path is the same as
  today.
- Any other variant calls `load_player_names(session, player.season, player.fpl_id)`. It
  falls back to the `web_name` when the player row is missing.

**5. The player slice builder — `backend/app/retrieval/evaluation/players.py`.** It is
read-only on the database and imports only `fpl`, `extraction` and `core`, edges retrieval
already has.

- **Window end:** the deadline of `Gameweek(season, N)`.
- **Previous deadline:** the latest deadline before it.
- **The end of N−1's last match:** `max(Fixture.kickoff_at for gameweek N−1) + 2 h`. It is
  `None` when N−1 has no fixture with a kickoff.
- **Window start:**
  - `alerts`: `window_floor(previous_deadline, deadline_N, DEFAULT_MAX_LOOKBACK,
    deadline_N)`;
  - `after-last-match`: the end of N−1's last match. The builder fails with a message when
    it is `None`.
- **Claims:** `current_extractions(session, created_from=start, created_until=end,
  sources_only=True)`, grouped by the events' `(player_season, player_fpl_id)`. Events with
  no player or another season are skipped. A post counts once per player.
  - A claim is `stale` when `created_at < previous_last_match_end`. Under `after-last-match`
    none can be stale.
  - The claimed IDs equal what `sql_claims` returns. Both use `current_extractions` with the
    same window and `sources_only=True`, and the test compares the two directly.
- **Names and aliases:** `load_player_names`, with the aliases from `load_aliases()` filtered
  by `(season, fpl_id)`.
- **Club:** the `Team.name`.
- **Constants:** retrieval may not import corroboration's `SEARCH_LIMIT` and `MAX_JUDGED`.
  The evaluation defines `JUDGE_CUT = 10` and `DEFAULT_DEPTHS = (20, 30, 50)`, and a test
  asserts `JUDGE_CUT == sources.MAX_JUDGED` and `SEARCH_LIMIT in DEFAULT_DEPTHS`.

**6. Metrics — `backend/app/retrieval/evaluation/player_metrics.py`.** Pure functions over
ranked IDs, claimed IDs and reference labels:

- **The cut:** `[x for x in ranked[:N] if x not in claimed][:JUDGE_CUT]`.
- **Per query, configuration and depth:**
  - `recall_at_cut` = |cut ∩ R_u| / |R_u|, where R_u = relevant − claimed. It is undefined
    when R_u is empty.
  - `recall_at_10` = |ranked[:10] ∩ R| / |R| over all relevant posts R. It is undefined when
    R is empty.
  - `empty_cut_with_miss` = the cut is empty and R_u is not.
  - `unjudged` = the posts of the cut with no reference label.
- **Aggregates:**
  - the mean of each recall over the queries where it is defined;
  - the count of `empty_cut_with_miss`;
  - the unjudged share, as the sum of unjudged posts over the sum of cut sizes (pooled, so
    an empty cut does not count as zero).
- **Per-run counts:**
  - queries with no unclaimed relevant post;
  - queries with no relevant post;
  - players with a `web_name` initial;
  - stale claimed posts;
  - queries whose newest claimed post by `(created_at, x_id)`, corroboration's anchor rule,
    is stale.
- The free slice keeps `metrics.aggregate` and adds the unjudged share of each mode's
  top 10.

**7. Runner — `backend/app/retrieval/evaluation/runner.py` (rewritten).** One run covers one
embedding model:

1. Load the set into `retrieval_eval` (with the source check).
2. Estimate the embedding tokens before any embedding and refuse above the cost cap
   (`--max-cost-usd`, default 1.00). The estimate is the characters / 4 of every corpus
   text plus every query text to embed, times `prices[model].input_per_million`.
3. Embed the corpus with `index_missing`.
4. Embed each distinct query text once, as the existing `_CachedQueryEmbedder` does.
5. Run the searches:
   - **Player queries:** one search per mode × variant × leg-depth group with
     `SearchFilters(since=window_start, until=window_end, sources_only=True)`. The depths
     are grouped by their leg depth `max(50, N)`; each group runs one search with
     `limit=max(group)` and that leg depth. `fuse` sorts and then slices, so within a group
     the top N for every smaller N is a prefix of that ranking, the same as a separate
     search with `limit=N`. With the default depths there is one group with leg depth 50,
     production's. A depth above 50 gets its own search, so it never changes the ranking
     the smaller depths are measured on (see Risks, "Fusion prefix").
   - **Free queries:** the v1 search, with top 10 and no filters.

The result file holds:

- the header;
- the `player` metrics per (mode, variant, depth);
- the `free` metrics per mode and slice, with the unjudged share;
- the per-run counts;
- per query, for each configuration: the cut's IDs and labels (`true`/`false`/`null`) and
  the raw top 10 with labels.

The header's `extra` holds:

- `split` and `k`;
- `depths`;
- `reference_judge`;
- `owner_reviewed_labels`, `judge_only_labels` and `judge_owner_agreement`;
- `relevance_prompts`, the prompt versions of the export.

The header's `judge` is the reference judge. A split with no pair labelled by the reference
judge or the owner raises `EvaluationError` before anything is embedded.

**8. Pooling — `backend/app/retrieval/evaluation/pooling.py`.**
- `pool_ids(query, rankings_by_config, depth)`:
  - for a player query, the union of the judge's cuts at the largest depth over every mode,
    variant and model;
  - for a free query, the union of each mode's top 10 per model.
- `new_posts(query, pooled)` = the pooled posts with no judgement entry of any labeller.
  With `for_judge` set, it is the pooled posts with no entry from that judge, so a second
  judge sees the first judge's pairs.
- Pooling runs inside `export-judge-inputs`. It loads the set once, embeds it for each
  `--embedding-model` under the same cost guard (summed over the models) and searches. A
  stored pool would need its own command and file, and AC18's command list has no `pool`.
  The pool is a pure function of the set, the models and the depths, so a re-export
  recomputes it.

**9. Session judge — `backend/app/retrieval/evaluation/judging.py`, `review.py` and two new
prompts.**

- Prompts:
  - `backend/app/content/prompts/retrieval_relevance_player.md` (version 1). Relevant means
    the post carries information on this player's availability for his gameweek N Premier
    League match (injury, fitness, line-up, rotation, suspension), denials included. An
    earlier gameweek's line-up or team news, or a mention without such information, is not
    relevant.
  - `retrieval_relevance_free.md` (version 1) has v1's relevance rules, rewritten for a
    list of posts.
  - Both end with the reply rule: one boolean per post ID, each post judged on its own.
- **Export layout:** `<out>/<slice>/inputs/judge-prompt.md` (the slice's prompt) plus one
  `<input_id>.md` per query, split into chunks of `--max-posts` (default 30) with the IDs
  `<query_id>`, `<query_id>.2`, …. `<out>/mapping.json` maps each input to its query, its
  slice, `{neutral ID: x_id}`, the set directory, the prompt versions and `for_judge`. It
  sits outside every `inputs/` directory, as AC12 requires.
  - The script's directory argument is `<out>/<slice>`, so each input is judged with its
    slice's prompt as the system prompt. That is how "the input holds the versioned prompt
    for its slice" is realised: the pattern in `docs/EVALUATION.md`, where the judge reads
    the prompt file plus exactly the input. It keeps one prompt file per directory, so the
    script stays as it is.
- **Input text:**
  - For a player query, a header line: the player (full name and `web_name`), the club,
    gameweek N and its deadline, and the window (Europe/Warsaw).
  - For a free query, the query text.
  - Then `[P1] @author · <created_at Europe/Warsaw>` and the post text, per post, in the
    order `random.Random(f"{seed}:{input_id}")` gives.
  - Each post also carries its time. Relevance depends on whether team news is for
    gameweek N or an earlier one, and the judge cannot tell that without the time.
- **Query sample:** `--query-sample N --seed S` exports a seeded subset of queries, for spec
  017's 20-query pilot.
- **Estimate:** `estimate --export DIR` sums, per input, (prompt characters + input
  characters + reply-format characters) / 4 + `CALL_OVERHEAD_TOKENS = 3000`. It prints
  calls and tokens per slice and in total. It reads files only.
- **Import:** `import-judge --export DIR --judge-model NAME` reads
  `<out>/<slice>/verdicts/<input_id>.json`, shaped `{"labels": {"P1": true, ...}}`.
  - An input fails, and its message names the input and the IDs, when its labels miss an ID
    of the mapping, carry an extra or unknown ID, or carry a non-boolean value. The other
    inputs are stored.
  - The command exits 1 if any input failed or had no verdict.
  - Labels are added as `Judgement(labelled_by=NAME, reviewed=False)` beside other
    labellers' entries.
  - The same judge with the same label is a no-op, so a re-import is idempotent. The same
    judge with a different label fails that pair unless `--force` is given.
- **`agreement --judge A --judge B`:** over the pairs both judges labelled, it prints the
  agreement share and n, then one line per disagreement (query, X ID, A, B).
- **`review --sample N --seed S`:**
  - It shows first every pair whose judges disagree and that has no owner entry, then a
    seeded sample of N of the other judge-labelled pairs. The sample is drawn over all of
    them, reviewed ones included, and the reviewed ones are skipped, so a resumed review
    shows the same sample.
  - Each pair shows the player or query and the post, and the owner answers
    `[r]elevant [n]ot relevant [s]kip [q]uit`. The answer is stored as
    `Judgement(labelled_by="owner", reviewed=True)` and written after every answer. The
    judges' entries stay.
  - The review is **blind**: the judges' labels are not shown, so the measured agreement is
    not anchored. The old v1 review showed the model's label. Blind is the stricter choice
    for the agreement that AC14 reports.
  - An interrupt exits 130 with everything already saved.

**10. Script — `scripts/session_judge.sh DIR [PARALLEL] [MODEL] [SCHEMA] [EFFORT]`.**

- `SCHEMA` defaults to `presser` and selects two things: the reply-format sentence appended
  to the system prompt, and the Python validation snippet.
  - `presser`: today's claims schema.
  - `retrieval`: the labels object. Its keys must equal the `[P\d+]` IDs listed in the
    input, and every value must be a boolean.
- `EFFORT`, when given, adds `--effort EFFORT` to `claude -p`. Spec 017's Sonnet pilot needs
  medium effort, and the flag exists in the installed CLI (`claude --help`).
- An invalid reply moves `raw/<id>.json` to `raw/<id>.invalid.json`, so the next run calls
  the judge again for that input only. The invalid reply is kept, and its tokens are still
  counted. Until now an invalid raw reply was re-parsed forever.
- The presser command line in `docs/EVALUATION.md` stays valid as it is.

**11. Entry point.**
- `backend/app/retrieval/evaluation/cli.py` and `__main__.py` (`prog_name="python -m
  app.retrieval.evaluation"`) reuse `RetrievalCliDeps`, `get_deps`, `fail`, `db_engine` and
  `_cost` from `app/retrieval/cli.py`, which keeps `index`, `status` and `search`.
- If the evaluation CLI grows past about 500 lines, the judge and review commands move to
  `backend/app/retrieval/evaluation/judge_cli.py`. That module registers on the same Typer
  `app` and is imported by `cli.py`.
- Every evaluation command takes `--set DIR` (default `evals/retrieval/v2`) instead of
  `--corpus` / `--queries`.
- Result path: `results/[dev/]<split>-<set_version>-<model-slug>.json`, behind the guard.

**12. Compare (AC10).** `compare FILE...` reads each header:

- A file without a header is refused as "no header". A file whose `set_version` or `split`
  differs from the first file's is refused too, and the message names that file.
- It prints the player table: model, mode, variant, depth, recall at cut, recall@10, empty
  cuts, unjudged share.
- It prints the free table: model, mode, slice, recall@5, recall@10, MRR, unjudged share.

**Alternatives considered:**

- *A stored pool with its own command*, against pooling at export. Rejected: AC18 fixes the
  command list, and pooling is reproducible from its inputs.
- *`is_source` from live SQL at export*, against a Python copy over the corpus. Rejected:
  the live SQL would make the export write the Compose database or count quotes posted
  after the deadline.
- *The prompt inlined into every input*, against a per-slice `judge-prompt.md`. Rejected:
  the system-prompt pattern of `docs/EVALUATION.md` keeps the script unchanged and the
  prompt identical across inputs.
- *The shared module in a new top-level `app/evaluation/`*, against `app/core`. Rejected: a
  new top-level module needs new edges in `ALLOWED`, and `core` needs none.

## AC → steps matrix

| AC | Steps | Proving test |
|----|-------|--------------|
| AC1 | 7 | `tests/retrieval/evaluation/test_sources.py::test_sources_only_search_returns_exactly_the_marked_posts`, `::test_load_refuses_a_corpus_whose_flags_disagree_with_the_sql_rule` |
| AC2 | 6 | `tests/retrieval/evaluation/test_dataset_v2.py::test_player_query_round_trips_every_field`, `::test_v1_files_load_unchanged`, `::test_carry_v1_keeps_judgements_and_labelled_by` |
| AC3 | 10, 11 | `tests/retrieval/evaluation/test_players.py` (the six listed cases + `test_claimed_ids_equal_sql_claims` + `test_builder_runs_on_a_read_only_connection`) |
| AC4 | 7 | `tests/retrieval/evaluation/test_export.py::test_export_bounds_by_since_and_until_and_skips_the_test_account`, `::test_export_runs_on_a_read_only_connection` |
| AC5 | 8 | `tests/retrieval/test_variants.py::test_initial_player_found_by_full_name_and_names_not_by_web_name` |
| AC6 | 9 | `tests/corroboration/test_sources.py::test_query_variant_constant_defaults_to_web_name`, `::test_another_variant_reaches_the_search`; `tests/corroboration tests/alerts` unchanged and green |
| AC7 | 12, 13 | `tests/retrieval/evaluation/test_player_metrics.py` (every number, incl. `test_more_than_twenty_claimed_posts_empty_the_cut`); `tests/retrieval/evaluation/test_runner_v2.py::test_player_slice_searches_window_and_sources` |
| AC8 | 13 | `tests/retrieval/evaluation/test_runner_v2.py::test_run_reports_every_mode_variant_and_depth_and_free_slices`, `::test_result_holds_cut_ids_and_labels_per_configuration` |
| AC9 | 13 | `tests/retrieval/evaluation/test_runner_v2.py::test_estimate_printed_and_cap_refuses_before_embedding`, `::test_model_without_price_row_refused` |
| AC10 | 14 | `tests/retrieval/evaluation/test_compare.py` |
| AC11 | 15 | `tests/retrieval/evaluation/test_pooling.py::test_extension_adds_exactly_the_new_models_unseen_posts`, `::test_second_extension_with_same_models_adds_nothing` |
| AC12 | 16 | `tests/retrieval/evaluation/test_judging.py::test_export_writes_one_shuffled_neutral_input_per_query`, `::test_mapping_outside_inputs`, `::test_long_query_split_by_max_posts`, `::test_player_prompt_and_input_name_gameweek_and_deadline` |
| AC13 | 16, 17 | `tests/retrieval/evaluation/test_judging.py::test_estimate_counts_calls_and_tokens`; `tests/retrieval/evaluation/test_import.py` (validation, storage, second judge, agreement) |
| AC14 | 13, 18 | `tests/retrieval/evaluation/test_review.py`; `tests/retrieval/evaluation/test_runner_v2.py::test_header_records_reference_counts_and_agreement`, `::test_no_reference_label_refuses` |
| AC15 | 4 | `tests/test_session_judge.py` |
| AC16 | 1, 2, 3, 13 | `tests/core/test_eval_results.py`; `tests/test_shared_code.py::test_atomic_write_lives_once`; per evaluation: `tests/presser/evaluation/test_cli.py::test_evaluate_writes_header_and_guard_refuses`, `tests/corroboration/evaluation/test_runner.py::test_evaluate_writes_header_and_guard_refuses`, `tests/extraction/test_cli.py::test_evaluate_writes_header_and_guard_refuses`, `tests/retrieval/evaluation/test_runner_v2.py::test_evaluate_writes_header_and_guard_refuses`; old files: `tests/core/test_eval_results.py::test_repository_result_files_load_as_no_header` |
| AC17 | 3 | `tests/presser/evaluation/test_summary.py::test_runs_grouped_by_set_and_prompt_version`, `::test_headerless_runs_shown_apart_never_ranked` |
| AC18 | 5, 19 | `tests/retrieval/evaluation/test_cli_entry.py::test_main_cli_keeps_index_status_search_only`, `::test_evaluation_cli_has_exactly_the_commands`; `tests/test_module_boundaries.py` unchanged and green |
| AC19 | 19 | `tests/test_docs.py::test_evaluation_md_describes_the_retrieval_procedure`, `::test_backlog_window_row_and_017_018_renumbering`, `::test_decisions_cover_retrieval_set_v2_and_results_header` |

## Steps

Every step runs from `backend/`. "Lint" means
`uv run ruff check . && uv run ruff format --check .`. Each step writes its tests first,
watches them fail for the expected reason, and then writes the code.

- [x] 1. **The shared result module (AC16, the module).** Files: new
      `backend/app/core/eval_results.py` and `backend/tests/core/test_eval_results.py`.
      - Tests:
        - `make_header` holds exactly the eight keys plus the `extra` keys, with `null` for
          fields that do not apply;
        - `git_state` returns the HEAD commit and a dirty flag, tested in a `tmp_path` git
          repository made with `git init`, one commit, then one modified file. The test's
          own git calls pass `-c user.name=test -c user.email=test@example.com
          -c commit.gpgsign=false`, because CI has no git identity and a developer's
          signing config must not reach the test. It returns `(None, None)` outside a
          repository;
        - `read_header` returns `None` for a dict without `"header"`;
        - `write_json_atomic` leaves no `.tmp` file when the serialisation raises (pass an
          unserialisable value), and keeps the old content;
        - `check_new_result` raises `ResultExistsError` naming the path for an existing file
          and passes with `force=True`;
        - `test_repository_result_files_load_as_no_header`: every `*.json` under
          `backend/evals/*/results/` (committed) loads with `json.loads`, and `read_header`
          gives `None`.
      - Code as in Design §1. `REPO_ROOT = Path(__file__).resolve().parents[3]`. `git` runs
        through `subprocess.run([...], capture_output=True, text=True, check=False)`, and
        an `OSError` gives `(None, None)`.
      Automatic verification: `uv run pytest -q tests/core/test_eval_results.py` and lint.
      `iterations: 0`

- [x] 2. **All seven atomic writes go through the module, and corroboration and extraction
      get the header and the guard (AC16).** Files:
      - `backend/app/presser/evaluation/cases.py` and `runner.py`;
      - `backend/app/corroboration/evaluation/cases.py`, `runner.py` and `cli.py`;
      - `backend/app/extraction/evaluation/cases.py`, `runner.py` and `backend/app/extraction/cli.py`;
      - `backend/app/retrieval/evaluation/dataset.py` and `runner.py` (the write only);
      - tests: `backend/tests/test_shared_code.py`,
        `backend/tests/corroboration/evaluation/test_runner.py` and
        `backend/tests/extraction/test_cli.py`.

      Tests:
      - `test_atomic_write_lives_once`: `os.replace` appears in `app/` only in
        `core/eval_results.py`, in the same style as
        `test_current_extraction_sql_lives_once`;
      - corroboration `test_evaluate_writes_header_and_guard_refuses`: through the CLI with
        fakes, the run file has `header.set_version == "v1"`, the model, the
        `prompt_version` and a `date`. A second `evaluate` to the same `--output` exits 1
        with the path in stderr, and `--force` overwrites;
      - extraction `test_evaluate_writes_header_and_guard_refuses`: the same, through
        `python -m app.extraction evaluate` with the existing fakes of
        `tests/extraction/test_cli.py`. `reasoning_effort` comes from the spec's settings.

      Code:
      - `_write_lines` and `write_cases` → `write_lines_atomic`;
      - every `write_result` → `write_json_atomic` (the module-level names stay as thin
        wrappers or are replaced at the call sites);
      - the corroboration and extraction `evaluate` get `--force`, call `check_new_result`
        before the model runs, and add `"header": make_header(...)` to the data;
      - the extraction result is written with `write_json_atomic`, which changes its indent
        from 1 to 2. That is harmless: readers parse JSON.

      Automatic verification:
      `uv run pytest -q tests/test_shared_code.py tests/corroboration tests/extraction tests/presser/evaluation tests/retrieval/evaluation`
      and lint.
      `iterations: 0`

- [x] 3. **Presser header, and `summary` grouped by set and prompt version (AC16, AC17).**
      Files: `backend/app/presser/evaluation/runner.py`, `summary.py` and `cli.py`, plus
      `backend/tests/presser/evaluation/test_summary.py` and `test_cli.py`.

      Tests:
      - `test_evaluate_writes_header_and_guard_refuses`: the header carries `set_version`
        `v7` and the writer prompt version. With `--no-judge` the judge is `null`.
        `import-judge` sets `header.judge`. The existing "exists" refusal now comes from
        `check_new_result`;
      - `test_runs_grouped_by_set_and_prompt_version`: two runs on `v7`/prompt A and one on
        `v7`/prompt B give two groups, each with its own winner line. A cheaper run of
        group B never beats group A's runs;
      - `test_headerless_runs_shown_apart_never_ranked`: a file without a header is listed
        under `no header` with no winner line, even when it would pass.

      Code:
      - `run_evaluation` adds the header (reasoning effort from `load_model_settings()`);
      - `import_verdicts` sets `header["judge"]` when a header exists;
      - `summarise` returns runs with a `group: tuple[str, str] | None` built from
        `read_header`;
      - the `summary` command prints one block per group (`set v7 · presser_writer@N`) and
        calls `choose` within a group only. It prints the `no header` block last, without
        `choose`.

      Automatic verification: `uv run pytest -q tests/presser/evaluation` and lint.
      `iterations: 0`

- [x] 4. **`session_judge.sh` takes the verdict schema (AC15, closes BACKLOG #48).** Files:
      `scripts/session_judge.sh` and new `backend/tests/test_session_judge.py`.

      Tests, which run the script through `subprocess` with a stub `claude` placed first on
      `PATH`. The stub is a `tmp_path` shell script: it appends its arguments to a log,
      reads the reply from `$STUB_REPLY_DIR/<n>.txt` or a fixed reply, and prints
      `{"result": "<reply>", "usage": {...}}`.
      - The presser default (three arguments): a valid claims reply lands in `verdicts/`.
      - `retrieval`: of two inputs listing `[P1]` and `[P2]`, the one whose reply misses
        `P2` gets no verdict and its raw reply moves to `raw/<id>.invalid.json`. A second
        run, with the stub now replying correctly, calls the stub once (the log grows by
        one line) and writes the missing verdict.
      - `retrieval` rejects a non-boolean value and an extra ID.
      - The fifth argument adds `--effort medium` to the stub's arguments.
      - The test is skipped when `bash` or `python3` is missing (CI has both).

      Code: Design §10. The `retrieval` validator parses the IDs with
      `re.findall(r"^\[(P\d+)\]", input_text, re.M)`, checks
      `set(verdict["labels"]) == set(ids)` and checks that every value is `bool`. The usage
      line in the header comment gains the two new arguments.

      Automatic verification: `uv run pytest -q tests/test_session_judge.py`, lint, and
      `bash -n ../scripts/session_judge.sh`.
      `iterations: 0`

- [x] 5. **The evaluation's own entry point, as a move with no behaviour change (AC18, the
      move).** Files:
      - new `backend/app/retrieval/evaluation/cli.py` and `__main__.py`;
      - `backend/app/retrieval/cli.py`, where `export-corpus`, `build-queries`, `prelabel`,
        `evaluate`, `review` and their helpers are removed;
      - the tests: `backend/tests/retrieval/test_cli.py` (the review tests move out),
        `tests/retrieval/evaluation/test_dataset.py`, `test_labelling.py`,
        `test_queries.py` and `test_runner.py` import `app.retrieval.evaluation.cli`;
      - new `backend/tests/retrieval/evaluation/test_cli_entry.py` and
        `backend/tests/retrieval/evaluation/test_review_v1.py` (the moved review tests,
        replaced in step 18);
      - `README.md`, where the two retrieval evaluation lines name the new entry point.

      Tests in `test_cli_entry.py`:
      - `test_main_cli_keeps_index_status_search_only`: the registered command names of
        `app.retrieval.cli.app` are exactly `{index, status, search}`;
      - `python -m app.retrieval.evaluation --help` exits 0 (through `CliRunner` on the
        evaluation `app`);
      - `python -m app.retrieval evaluate` fails as an unknown command, so the old
        commands are not aliased.

      Code: a move. `RetrievalCliDeps`, `get_deps`, `fail`, `db_engine` and `_cost` stay
      in `app/retrieval/cli.py` and are imported.

      Automatic verification:
      `uv run pytest -q tests/retrieval tests/test_module_boundaries.py tests/test_readme.py`
      and lint.
      `iterations: 0`

- [x] 6. **Set v2 format and `carry-v1` (AC2).** Files:
      - `backend/app/retrieval/evaluation/dataset.py`: the models of Design §2,
        `SetManifest`, `load_set(dir) -> EvalSet(version, manifest, corpus, queries)`,
        `write_queries`, `write_manifest`, `DEFAULT_SET_DIR = EVALS_DIR / "v2"` and
        `carry_v1(v1_queries, v2_queries, corpus_ids) -> list[Query]`;
      - `backend/app/retrieval/evaluation/cli.py`: the `carry-v1` command, and `--set` on
        the moved commands;
      - new `backend/tests/retrieval/evaluation/test_dataset_v2.py`.

      Tests:
      - `test_player_query_round_trips_every_field`: every AC2 field survives a write and a
        load, including `claimed[].stale` and `window_rule`;
      - `test_v1_files_load_unchanged`: the committed `evals/retrieval/v1/*.jsonl` load
        with `kind == "free"`, and the version is `v1` from the directory name;
      - `test_carry_v1_keeps_judgements_and_labelled_by`: free queries are appended with
        their IDs, judgements and `labelled_by`. Existing free queries make the command
        fail without `--force`. A judged post missing from the v2 corpus fails the command
        and names how many posts are missing;
      - `test_unknown_kind_is_rejected`;
      - the existing `tests/retrieval/evaluation/test_eval_set.py` and `test_dataset.py`
        stay green.

      Automatic verification: `uv run pytest -q tests/retrieval/evaluation` and lint.
      `iterations: 0`

- [x] 7. **Source posts in the corpus, export bounds and the eval schema (AC1, AC4).**
      Files:
      - `backend/app/retrieval/evaluation/dataset.py`: `export_corpus(engine, since,
        until)` returning posts and the list snapshot, and `source_flags`;
      - `backend/app/retrieval/evaluation/schema.py`: `ListMembership` created in the
        schema, the snapshot row, `quoted_x_id`, and the consistency check;
      - `backend/app/retrieval/evaluation/cli.py`: `export-corpus --since --until` in
        Europe/Warsaw (`parse_local`), writing `corpus.jsonl` and `set.json`;
      - new `backend/tests/retrieval/evaluation/test_sources.py` and `test_export.py`;
      - new `backend/tests/retrieval/evaluation/conftest.py`: the `eval_db` fixture moves
        there, and its two identical copies in `test_runner.py` and `test_schema.py` are
        removed, so every new container test can use it.

      Tests:
      - `test_sources_only_search_returns_exactly_the_marked_posts`. The corpus has a list
        author's post, an off-list post, an off-list post quoted by a list author's post,
        and an off-list post quoted by another off-list post. The snapshot is
        `["listed"]`. Load it with `load_eval_corpus`, then run a full-text search for a
        word all four posts share with `sources_only=True`. It returns exactly the posts
        with `is_source=True`, which are the first three.
      - The same with `list_handles=None`: every post is a source.
      - `test_load_refuses_a_corpus_whose_flags_disagree_with_the_sql_rule`: a flipped flag
        raises an `EvaluationError` that names the X ID.
      - `test_export_bounds_by_since_and_until_and_skips_the_test_account`: `until` is
        exclusive, `since` inclusive, and `gafferpresser` is left out.
      - `test_export_runs_on_a_read_only_connection`: an engine with
        `connect_args={"options": "-c default_transaction_read_only=on"}` exports without
        an error.
      - The CLI writes `set.json` with `version`, `list_handles` and the bounds, and refuses
        an existing corpus without `--force`.

      Automatic verification:
      `uv run pytest -q tests/retrieval/evaluation tests/retrieval/test_search.py` and lint.
      `iterations: 0`

- [x] 8. **Query variants (AC5).** Files: new `backend/app/retrieval/variants.py` and new
      `backend/tests/retrieval/test_variants.py`.

      Tests:
      - unit tests of `variant_text` for `B.Fernandes`, for `Saka` (no initial: `names` =
        `"Saka"`), for an alias, and for de-duplication;
      - `has_initial` over `B.Fernandes`, `J.Timber`, `Saka` and `Gabriel`;
      - `test_initial_player_found_by_full_name_and_names_not_by_web_name`, a container
        test. Seed the posts "Bruno Fernandes is fit" (1) and "Fernandes trained today"
        (2) with `add_tweet`. In `fulltext` mode, the `full_name` text and the `names`
        text each return {1, 2}, and the `web_name` text `B.Fernandes` returns neither;
      - `load_player_names` reads the `Player` row and the aliases filtered by
        `(season, fpl_id)`, and gives `None` for an unknown player.

      Automatic verification: `uv run pytest -q tests/retrieval/test_variants.py` and lint.
      `iterations: 0`

- [x] 9. **Corroboration builds its query through the variant constant (AC6).** Files:
      `backend/app/corroboration/sources.py` and
      `backend/tests/corroboration/test_sources.py`.

      Tests:
      - `test_query_variant_constant_defaults_to_web_name`;
      - `test_another_variant_reaches_the_search`: monkeypatch
        `app.corroboration.sources.search` with a recorder and
        `sources.QUERY_VARIANT = "full_name"`, and seed the player with `seed_reference`.
        The recorded query is `"Gabriel dos Santos Magalhaes"` for `GABRIEL_MAGALHAES`.
        With the default, it is the `web_name`, and no player lookup runs;
      - `test_judge_cut_constants_match_the_evaluation`:
        `JUDGE_CUT == MAX_JUDGED` and `SEARCH_LIMIT in DEFAULT_DEPTHS`. It imports
        `app.retrieval.evaluation.player_metrics`, so this test is written in step 12; here
        only the first two tests are written.

      Code: Design §4. No existing test file other than the two new tests changes.

      Automatic verification: `uv run pytest -q tests/corroboration tests/alerts` and lint.
      Then `git diff --stat -- tests/alerts` must be empty, and `tests/corroboration` may
      differ only by the added tests.
      `iterations: 0`

- [x] 10. **Player-slice windows (AC3, the window rules).** Files: new
      `backend/app/retrieval/evaluation/players.py` (`GameweekWindow`,
      `gameweek_window(session, season, gameweek, rule)`) and new
      `backend/tests/retrieval/evaluation/test_players.py`.

      Tests, on a container seeded with gameweeks and fixtures. Fixtures are added through
      `Fixture` rows; the `Team` rows come from `seed_reference`.
      - `alerts`: the start is the previous deadline, and the end is deadline N.
      - The international break: the previous deadline lies 14 days back, so the start is
        `deadline_N − 7 days`.
      - `after-last-match`: the start is the latest kickoff of gameweek N−1 plus 2 hours.
        A null kickoff is ignored.
      - `after-last-match` with no fixture in N−1 raises an `EvaluationError` naming N−1.
      - An unknown gameweek N raises an error.

      Automatic verification:
      `uv run pytest -q tests/retrieval/evaluation/test_players.py` and lint.
      `iterations: 0`

- [x] 11. **The player-slice builder and `build-player-slice` (AC3).** Files:
      `backend/app/retrieval/evaluation/players.py` (`build_player_slice(engine, season,
      gameweek, rule, seed) -> list[PlayerQuery]`), `backend/app/retrieval/evaluation/cli.py`
      (`build-player-slice --gameweek N [--season] [--window alerts|after-last-match]
      [--seed] [--set] [--force]`) and `backend/tests/retrieval/evaluation/test_players.py`.

      Tests: the six AC3 cases on one seeded world, each as its own test:
      - a player claimed twice gives one query with two claimed IDs;
      - a post outside the window is not claimed;
      - an off-list post is not a source: it is not claimed, with a `list_membership` row
        `["listed"]` and an extraction on an off-list author's post;
      - a player with no event gets no query;
      - a line-up post published between the previous deadline and N−1's last match end is
        claimed and `stale` under `alerts`, and is outside the window under
        `after-last-match`;
      - a window longer than the lookback starts at the deadline minus the lookback under
        `alerts`.

      Plus:
      - `test_claimed_ids_equal_sql_claims`: per query, the claimed IDs equal
        `{c.post.x_id for c in sql_claims(session, PlayerRef(...), start, end)}`. The test
        imports corroboration, which tests may do;
      - `test_builder_runs_on_a_read_only_connection`;
      - the CLI test: the command writes player queries, keeps the free queries already in
        the file, and refuses existing player queries without `--force`. It prints the
        counts (queries, claimed, stale, players with an initial).

      Automatic verification:
      `uv run pytest -q tests/retrieval/evaluation/test_players.py` and lint.
      `iterations: 0`

- [x] 12. **Player-slice and reference-label metrics (AC7, the numbers; the AC14 label
      rule).** Files: new `backend/app/retrieval/evaluation/player_metrics.py` (the cut,
      per-query numbers, aggregates, the five counts, `JUDGE_CUT` and `DEFAULT_DEPTHS`),
      new `backend/app/retrieval/evaluation/labels.py` (`reference_labels(query,
      reference_judge) -> dict[int, bool]`, `label_counts`, `judge_owner_agreement`), new
      `backend/tests/retrieval/evaluation/test_player_metrics.py`, new `test_labels.py`,
      and `backend/tests/corroboration/test_sources.py`
      (`test_judge_cut_constants_match_the_evaluation`).

      Tests, with hand-built ranked lists, claims and labels. Every number is asserted:
      - `recall_at_cut`, `recall_at_10`, `empty_cut_with_miss` and the unjudged share at
        depths 20 and 50;
      - `test_more_than_twenty_claimed_posts_empty_the_cut`: 22 claimed posts at the top of
        the ranking and one unclaimed relevant post at rank 23 give an empty cut at
        depth 20 (`empty_cut_with_miss` true, recall 0) and the post in the cut at
        depth 30;
      - a query with no unclaimed relevant post and one with no relevant post are left out
        of the means and counted;
      - the counts of initials, stale claims and stale anchors (the newest claim by
        `(created_at, x_id)`);
      - labels: an owner entry beats the judge's, the reference judge's entry is used
        otherwise, another judge's entry alone is unjudged, and `label_counts` and the
        agreement match by hand.

      Automatic verification:
      `uv run pytest -q tests/retrieval/evaluation/test_player_metrics.py tests/retrieval/evaluation/test_labels.py tests/corroboration/test_sources.py`
      and lint.
      `iterations: 0`

- [x] 13. **The v2 runner and `evaluate` (AC7 searches, AC8, AC9, the AC14 header, the AC16
      retrieval header).** Files: `backend/app/retrieval/evaluation/runner.py` (rewritten
      on Design §7), `metrics.py` (the unjudged share of the free top 10),
      `backend/app/retrieval/evaluation/cli.py` (`evaluate --split --embedding-model
      --reference-judge --depths 20,30,50 --k 60 --max-cost-usd 1.00 --set --output
      --force`), and new `backend/tests/retrieval/evaluation/test_runner_v2.py`.
      `test_runner.py` is replaced where it tested v1 behaviour that no longer exists:
      `include_unreviewed` gives way to `--reference-judge`.

      Tests, in the `eval_db` container with `FakeEmbedder` and a hand-built v2 set of
      three player queries and two free queries:
      - `test_player_slice_searches_window_and_sources`: a relevant post outside the window
        and a non-source post never appear in a cut;
      - `test_run_reports_every_mode_variant_and_depth_and_free_slices`: 3 modes × 3
        variants × 3 depths player rows, and free rows per mode × {all, en, pl};
      - `test_result_holds_cut_ids_and_labels_per_configuration`;
      - `test_estimate_printed_and_cap_refuses_before_embedding`: the CLI prints the
        estimate. `--max-cost-usd 0.000001` exits 1, and the `FakeEmbedder` has no calls;
      - `test_model_without_price_row_refused`: through `deps.make_embedder` and
        `resolve_embedding`;
      - `test_header_records_reference_counts_and_agreement`;
      - `test_no_reference_label_refuses`: a split labelled only by another judge refuses
        before any embedding;
      - `test_evaluate_writes_header_and_guard_refuses`.

      Automatic verification: `uv run pytest -q tests/retrieval/evaluation` and lint.
      `iterations: 0`

- [x] 14. **`compare` (AC10).** Files: new `backend/app/retrieval/evaluation/compare.py`,
      `cli.py` (`compare FILE...`), and new
      `backend/tests/retrieval/evaluation/test_compare.py`.

      Tests:
      - two result files of different models on the same set and split print one table
        with a row per model × mode × variant × depth, with the unjudged share, and the
        free table;
      - a file of another set version exits 1 naming it, and so does a file of another
        split;
      - a file without a header exits 1 with "no header" and its name;
      - nothing is printed before a refusal, so results are never mixed.

      Automatic verification:
      `uv run pytest -q tests/retrieval/evaluation/test_compare.py` and lint.
      `iterations: 0`

- [x] 15. **Pooling (AC11).** Files: new `backend/app/retrieval/evaluation/pooling.py`
      (`pool_ids`, `new_posts`, and `pool_set(engine, eval_set, embedders, depths,
      variants, ...) -> dict[str, list[int]]` with the cost guard) and new
      `backend/tests/retrieval/evaluation/test_pooling.py`.

      Tests:
      - `test_extension_adds_exactly_the_new_models_unseen_posts`: a judged query, pooled
        with model A, then with models A and B (two `FakeEmbedder`s with different vector
        maps), gains exactly B's unseen cut posts. Its existing judgements are untouched;
      - `test_second_extension_with_same_models_adds_nothing`: after the new posts get
        judgements;
      - player pool = the union of the cuts at the largest depth over modes × variants ×
        models; free pool = each mode's top 10;
      - `for_judge` returns the posts that judge lacks, other judges' posts included.

      Automatic verification:
      `uv run pytest -q tests/retrieval/evaluation/test_pooling.py` and lint.
      `iterations: 0`

- [x] 16. **Prompts, `export-judge-inputs` and `estimate` (AC12, the AC13 estimate).** Files:
      - new `backend/app/content/prompts/retrieval_relevance_player.md` and
        `retrieval_relevance_free.md`;
      - new `backend/app/retrieval/evaluation/judging.py` (`render_input`, `export_inputs`,
        `estimate_export`, `CALL_OVERHEAD_TOKENS`);
      - `cli.py` (`export-judge-inputs --out --embedding-model ... --depths --max-posts 30
        --seed --query-sample --for-judge --max-cost-usd --set`, and `estimate --export`);
      - new `backend/tests/retrieval/evaluation/test_judging.py`.

      Tests:
      - `test_export_writes_one_shuffled_neutral_input_per_query`: one `.md` per query,
        IDs `P1..Pn`, no X ID anywhere under `inputs/`, a seeded order that differs from
        the corpus order and repeats with the same seed, and author, time and text present;
      - `test_mapping_outside_inputs`: `mapping.json` sits in `<out>` and maps every
        neutral ID back to an X ID;
      - `test_long_query_split_by_max_posts`: 35 posts with `--max-posts 30` give inputs of
        30 and 5;
      - `test_player_prompt_and_input_name_gameweek_and_deadline`: the per-slice
        `judge-prompt.md` is the player prompt, and the input names the player, the club,
        gameweek N, its deadline and the window. The prompt loads through `load_prompt`
        with version 1;
      - `test_estimate_counts_calls_and_tokens`: calls = inputs, and tokens = the
        characters / 4 + 3000 per call, by hand;
      - the export refuses a non-empty `--out`.

      Automatic verification: `uv run pytest -q tests/retrieval/evaluation/test_judging.py tests/content`
      and lint.
      `iterations: 0`

- [x] 17. **`import-judge` and `agreement` (AC13).** Files: `judging.py` (`import_verdicts`,
      `agreement`), `cli.py` and new `backend/tests/retrieval/evaluation/test_import.py`.

      Tests:
      - a valid verdict stores one `Judgement` per pair with `labelled_by` set to the
        judge's name;
      - a missing ID, an extra ID, an unknown ID and a non-boolean value each fail that
        input. The message names the input and the ID, the other inputs are stored, and
        the exit code is 1;
      - a second judge on the same pairs is stored beside the first, so both entries exist;
      - a re-import of the same judge is a no-op. The same judge with a changed label fails
        without `--force`;
      - an input with no verdict file is reported as missing;
      - `agreement` prints the share, the n and one line per disagreement.

      Automatic verification:
      `uv run pytest -q tests/retrieval/evaluation/test_import.py` and lint.
      `iterations: 0`

- [x] 18. **The owner's `review` (AC14).** Files: new
      `backend/app/retrieval/evaluation/review.py` (`review_order(queries, sample, seed)`),
      `cli.py` (`review --sample N --seed S [--split] [--set]`, replacing the moved v1
      review), and new `backend/tests/retrieval/evaluation/test_review.py`;
      `test_review_v1.py` is deleted because its behaviour is replaced.

      Tests:
      - the order is every disagreement first, then a seeded sample of N of the other
        judged pairs, the same across runs, with reviewed pairs skipped and not redrawn;
      - the answers `r`, `n` and `s` through `CliRunner(input=...)` store
        `Judgement(labelled_by="owner", reviewed=True)` beside the judges' entries and
        write after every answer, so the file is complete after an interrupt (exit 130);
      - the display shows the player or query and the post, and no judge label (blind);
      - the command needs no database and no key.

      Automatic verification:
      `uv run pytest -q tests/retrieval/evaluation/test_review.py tests/retrieval/evaluation/test_runner_v2.py`
      and lint.
      `iterations: 0`

- [x] 19. **The command list and the documents (AC18, AC19).** Files:
      - `backend/tests/retrieval/evaluation/test_cli_entry.py`;
      - `docs/EVALUATION.md`, a new section "The retrieval procedure". It covers:
        - the command sequence: build (`export-corpus`, `build-player-slice`, `carry-v1`),
          pool and export, estimate, judge (`session_judge.sh <out>/player 6 sonnet
          retrieval medium`), import, agreement, review, evaluate and compare;
        - the per-query batching choice (lesson 5), with posts shuffled and labelled one
          by one;
        - the second judge for agreement (`--for-judge`);
        - the pilot (`--query-sample`);
        - the DATABASE_URL warning (a scratch container, not the Compose database);
        - the corpus bound: `export-corpus` without `--since` (or one no later than v1's
          earliest post), so `carry-v1` finds v1's judged posts.
        The "Other evaluations" paragraph gets one line: the script takes a schema.
      - `docs/DECISIONS.md`: check that the set v2 labelling row (2026-10-15) is present.
        Add a row: one shared result module in `app/core/eval_results.py` (the header, the
        atomic write and the guard), the retrieval evaluation's own entry point, and
        `summary` ranking within a set and prompt group. The rejected alternatives are a
        shared framework (row 54) and a new top-level module.
      - `docs/BACKLOG.md`:
        - remove rows 43, 44, 45 and 48;
        - row 46 keeps only the extraction entry point, with the trigger "spec 018";
        - row 47's trigger becomes "spec 018";
        - row 49's "a refactor during specs 016 and 017" becomes "specs 016–018", since
          extraction v2 also measures a baseline;
        - add a new P1 row: the alert window starts after the previous gameweek's last
          match instead of at the previous deadline (`window_floor`, shared by the alerts,
          their corroboration and the ingest catch-up). Its trigger is spec 017's report
          showing stale claims or stale anchors in the GW6 window.
      - `docs/ROADMAP.md`: the retrieval item names spec 016 (tooling) and 017 (the run).
        The extraction v2 item says "planned as 018" and drops #46/#48 from its list where
        016 closes them.
      - `README.md`: the retrieval paragraph names set v2, the player slice and
        `python -m app.retrieval.evaluation`, with its main commands.
      - `backend/tests/test_docs.py`.

      Tests:
      - `test_evaluation_cli_has_exactly_the_commands`: the evaluation app's commands equal
        the 12 of AC18;
      - `test_main_cli_keeps_index_status_search_only` is kept;
      - `test_evaluation_md_describes_the_retrieval_procedure`: each of `build-player-slice`,
        `export-judge-inputs`, `estimate`, `import-judge`, `agreement`, `review`, `evaluate`,
        `compare`, `session_judge.sh` and "per query" appears in `docs/EVALUATION.md`;
      - `test_backlog_window_row_and_017_018_renumbering`: the window row is P1 and names
        spec 017. No `| 43 |`, `| 44 |`, `| 45 |` or `| 48 |` row is left. Rows 46 and 47
        name spec 018 and no longer "spec 017". The ROADMAP contains "018" and no longer
        "planned as 017";
      - `test_decisions_cover_retrieval_set_v2_and_results_header`.

      Automatic verification:
      `uv run pytest -q tests/test_docs.py tests/test_readme.py tests/retrieval/evaluation/test_cli_entry.py tests/test_module_boundaries.py`
      and lint. Then `git diff main -- backend/tests/test_module_boundaries.py` must be
      empty, so AC18 adds no new edge.
      `iterations: 0`

- [x] 20. **Full verification and the roadmap tick.** Files: `docs/ROADMAP.md`. Spec 016's
      tooling part is noted as done in the retrieval item, and the item stays open because
      spec 017 delivers the report. Run the end-to-end checks below. Then prepare the
      owner's manual scenario: in a scratch directory outside the repository
      (`$SCRATCH/review-set`), copy `evals/retrieval/v1/corpus.jsonl`, run `carry-v1` for
      the first two v1 queries into it, and record the exact `review` command and the
      directory path under "Manual (performed by the owner)" in the final report.
      Automatic verification:
      `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`.
      `iterations: 1`

## Risks and traps

- **`DATABASE_URL` and the `retrieval_eval` schema.** `evaluate` and `export-judge-inputs`
  drop and recreate `retrieval_eval` on the database `DATABASE_URL` names, as set v1 already
  does. Only `export-corpus` and `build-player-slice` need the Compose data, and both are
  read-only (they are tested on a read-only connection). `docs/EVALUATION.md` tells spec 017
  to run the other commands against a scratch container. No step of this spec touches the
  Compose database.
- **The `search_path` fallback.** Before step 7, `source_post_sql` inside `retrieval_eval`
  read `public.list_membership`. Step 7 creates the table in the evaluation schema, so the
  test container's public table can no longer leak into a run. Test isolation: the `db`
  fixture truncates the public tables, and `load_eval_corpus` drops the schema.
- **A claimed post that the corpus does not mark as a source.** The builder reads the live
  `source_post_sql`. The corpus flag counts only quotes inside the corpus. A post quoted
  only after the deadline would be claimed but never searched. Such a post is only dropped
  from the cut anyway, so no metric changes. `build-player-slice` prints the count of
  claimed IDs missing from the corpus or not marked as sources, as a warning.
- **`carry-v1` needs v1's posts in the v2 corpus.** `carry-v1` fails when a judged v1 post
  is missing from the v2 corpus, and the free slice is searched with no filters over the
  whole corpus. Spec 017 must therefore export the GW6 corpus with no `--since` (or one no
  later than v1's earliest post), not only the alert window. `docs/EVALUATION.md` (step 19)
  says so next to the `export-corpus` command.
- **Time zones.** The CLI options are Europe/Warsaw (`parse_local`), and storage and
  comparisons are in UTC. The `+2 h` match end is added in UTC.
- **Fusion prefix.** `fuse` sorts by score with a deterministic tie-break, then slices. The
  prefix equivalence of Design §7 holds only when every depth shares the same leg depth. The
  runner groups depths by leg depth (`max(50, N)`), so depths above 50 get their own search.
- **Existing tests that assume v1 behaviour.** These are `include_unreviewed`, the v1
  `review` UI and the `--corpus` / `--queries` options. They are replaced in the step that
  changes the behaviour, never deleted without a replacement test.
- **The presser `summary` output changes.** Today's nine committed files have no header, so
  `summary` prints them under "no header" with no winner. AC17 requires this. The final
  review report says it in one line.
- **The session-judge script under test.** The stub `claude` must come first on `PATH`, and
  the script runs `cd /tmp`, so the stub must not depend on the working directory.
- **Product content.** The two new prompts are English judge prompts, like
  `retrieval_relevance.md`. They live in `app/content/prompts/` with a `version:` header.

## End-to-end verification

### Automatic (performed by /pipeline:implement)

No step reaches the Compose database or the network. Run from `backend/`:

1. `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`: green.
2. `uv run python -m app.retrieval --help`: lists exactly `index`, `status` and `search`.
3. `uv run python -m app.retrieval.evaluation --help`: lists the 12 commands of AC18.
4. No end-to-end run against a database outside `pytest`: every command that touches
   PostgreSQL is driven through its CLI in a container test (steps 7, 11, 13, 15–18), and
   running one by hand would need either the Compose database (forbidden) or a second
   container with a hand-seeded world that the tests already build.
5. `bash -n ../scripts/session_judge.sh`, then
   `uv run pytest -q tests/test_session_judge.py`.
6. `git diff main --stat -- backend/tests/test_module_boundaries.py backend/tests/alerts`:
   empty.

**Result (implement, 2026-10-15):**

1. `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`: green, 2109 passed. The first full run had one red,
   `tests/core/test_settings.py::test_database_url_not_read_by_tests` (a new test of mine spelled the
   environment variable `DATABASE_URL`); the test now asserts that `review` never builds the
   dependencies, and the second run was green (step 20, one iteration).
2. `python -m app.retrieval --help` lists `index`, `status` and `search`.
3. `python -m app.retrieval.evaluation --help` lists the 12 commands: `export-corpus`,
   `build-queries`, `build-player-slice`, `carry-v1`, `prelabel`, `export-judge-inputs`,
   `import-judge`, `agreement`, `estimate`, `review`, `evaluate`, `compare`.
4. No run against a database outside `pytest`, as planned.
5. `bash -n scripts/session_judge.sh` is clean and `tests/test_session_judge.py` is green.
6. `git diff main --stat -- backend/tests/test_module_boundaries.py backend/tests/alerts`: empty.

The review scenario below was prepared in the session scratchpad,
`/tmp/claude-1000/-home-czarny-Projects-gaffers-presser/baa65aa3-debd-4205-9c11-a51d92b6a387/scratchpad/review-set`
(to recreate it: `mkdir DIR && cp backend/evals/retrieval/v1/corpus.jsonl DIR/`, then
`cd backend && uv run python -m app.retrieval.evaluation carry-v1 --set DIR --first 2`), and tried on a copy (`review` with `r`, `n`, `s` stored
two owner entries beside the model's and showed no judge label).

### Manual (performed by the owner)

- Try the blind review on a hand-made set of two queries, prepared by the implementer in
  step 20 (`$SCRATCH/review-set`: the v1 corpus plus the first two v1 queries carried in).
  Run `uv run python -m app.retrieval.evaluation review --set
  $SCRATCH/review-set --sample 3 --seed 1`, answer three pairs, and quit with `q`.
  Pass when: each pair shows the query and the post's author, time and text without any
  judge's label, and `queries.jsonl` in that directory then holds three
  `"labelled_by": "owner"` entries with `"reviewed": true` beside the model's entries.

## Definition of Done

- [x] all steps ticked
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto` fully green
- [x] end-to-end verification (automatic) performed, result recorded here
- [x] `docs/ROADMAP.md` updated; `docs/DECISIONS.md`, `docs/BACKLOG.md`, `docs/EVALUATION.md`
      and `README.md` updated
- [x] spec status: `implemented`

## Owner decisions

_(appended by /pipeline:ship or a stage on escalation, one entry per line: `- YYYY-MM-DD — <stage> — `<kind>` — <question> — <decision>`, with the kind `decision`, `permission` or `tooling`; a final-review gate entry has the kind `gate` and ends with `accepted`: F1, F2; `rejected`: F3, `none` for an empty list)_

## Review log

2026-10-15 — plan review (fresh context, under `/pipeline:ship`).

Findings:

- `minor` — Design §7 ran one search per mode × variant with leg depth `max(50, max(depths))`, while Risks ("Fusion prefix") grouped depths by leg depth; a depth above 50 would have changed the ranking the smaller depths are measured on. §7 now groups depths by leg depth `max(50, N)`, as Risks says.
- `minor` — Design §2 said a v1 line without `kind` parses as a free query, but a plain pydantic `Field(discriminator="kind")` rejects a missing tag. §2 now names a callable `Discriminator` (or a before-validator) that defaults to `"free"`.
- `minor` — Step 1's `git_state` test makes a commit in a `tmp_path` repository; CI has no git identity and a local signing config could block it. The test's git calls now pass a user, an e-mail and `commit.gpgsign=false`.
- `minor` — Step 7 moved the `eval_db` fixture "from `test_runner.py`", but `test_schema.py` holds a second identical copy. Both copies are now removed in favour of `conftest.py`.
- `minor` — Step 19's renumbering test only checked that the ROADMAP contains "018", which a stale "planned as 017" beside it would still pass; and BACKLOG row 49 ("specs 016 and 017") was not covered. The test now checks rows 46/47 name 018 and the ROADMAP no longer says "planned as 017"; row 49 is reworded to "specs 016–018".
- `minor` — The manual scenario had the implementer prepare the review set, outside any step, and without the corpus `review` needs to show posts. Preparation (v1 corpus + `carry-v1` of two queries into a scratch directory) moved into step 20; the manual item now only runs the review.
- `minor` — `carry-v1` fails when v1's judged posts are missing from the v2 corpus, so spec 017 cannot export only the GW6 alert window. Added to Risks and to step 19's `docs/EVALUATION.md` content.

Checked and found correct (later stages need not repeat):

- Coverage: every AC1–AC19 has steps and a named proving test; the matrix matches the steps; every step writes its tests first and has an `Automatic verification:` block with exact test paths.
- `source_post_sql` / `list_post_sql` (`app/tweets/classes.py`): the Python copy in Design §2 (null handles → all sources; lower-cased author in handles; quoted by a listed author) matches the SQL, and the load-time consistency check plus creating `list_membership` in `retrieval_eval` closes the `search_path` fallback to `public`.
- `current_extractions(created_from, created_until, sources_only=True)` is what `sql_claims` calls, so the claimed-ID equality of AC3 holds by construction and is still tested directly.
- `window_floor(previous, next_, DEFAULT_MAX_LOOKBACK, now)` in `app/fpl/deadlines.py` gives the `alerts` rule as planned, including the international-break case.
- Module boundaries: `retrieval` may import `core`, `extraction`, `fpl`; `corroboration` may import `retrieval`; `core` has no edges and `eval_results.py` takes the reasoning effort as an argument, so `ALLOWED` stays unchanged (AC18).
- Exactly seven `os.replace` copies exist under `app/` (presser, corroboration, extraction, retrieval `cases`/`dataset`/`runner`), matching AC16 and `test_atomic_write_lives_once`.
- `fuse` sorts the full candidate set before `[:limit]`, so the prefix argument is valid within one leg depth.
- `claude --effort` exists in the installed CLI; the script's `raw/*.json` token glob also counts the kept `.invalid.json` replies, as Design §10 intends.
- Every reference to the moved commands (README lines 180–181, five test modules, `app/retrieval/__main__.py`) is covered by step 5; no worker or deployment file references them.
- DECISIONS row 106 (set v2 labelling) already exists; step 19 adds the results-module row. Owner summary: no new dependency and no data migration, consistent with SPEC → Owner decisions (`retrieval_eval` is recreated by code, not by Alembic). PLAN is in English (`language: en`).
- AC12's "the input holds the versioned prompt" realised as a per-slice `judge-prompt.md` system prompt next to the inputs is a sound reading of the AC, recorded in Design §9.

Decision: the plan is ready for implementation — it covers every AC with test-first steps and exact commands, breaks no decision, needs no new dependency or migration, and the findings were all minor and are fixed in place.

## Deviations

_(filled in by /pipeline:implement — one entry per deviation, with its rationale: `- `minor` — …` or `- `major` — …`)_

- `minor` — step 2: three existing tests monkeypatched `os.replace` in the module that held its own copy (`...cases.os.replace`, `...dataset.os.replace`); they now patch `app.core.eval_results.os.replace`, the same atomic-write behaviour tested at its new home. Also added `reasoning_effort_of(model)` to `app/llm/models.py` so the four evaluations read a model's effort the same way.
- `minor` — step 3: two existing CLI tests of `summary` (`test_summary_reports_and_applies_thresholds`, `test_summary_without_runs_or_winner`) wrote header-less run files and expected a winner; AC17 says a header-less run is never ranked, so their fixtures now carry a header (`headed(...)`). The runs' header is added in the CLI, beside the other three evaluations, not inside `run_evaluation`.
- `minor` — step 6: `carry-v1` has an extra `--first N` option (step 20 needs a set of two carried queries for the owner's review scenario). `build-queries` and `prelabel` took `--set DIR` already here, in place of `--corpus`/`--queries`/`--output`, with their existing tests switched to `--set <tmp dir>` (same file names); `prelabel` skips player queries. Corpus lines are written without null fields, so v1 corpus files rewrite byte-compatible.
- `minor` — step 13: `tests/retrieval/evaluation/test_runner.py` (v1 runner behaviour: `include_unreviewed`, `--queries`/`--corpus`/`--output-dir`, `--depth`/`--limit`/`--run-name`) is deleted and its still-valid cases (corpus not embeddable, query not embeddable, tracing, default run name, the CLI smoke test) are re-written in `test_runner_v2.py` against the v2 runner. `evaluate` lost `--include-unreviewed`, `--depth`, `--limit`, `--run-name`, `--output-dir` (replaced by `--reference-judge`, `--depths`, `--output`, `--force`, per Design §7 and §11). `EvaluationError` moved to `dataset.py` (re-exported by `runner.py`) to avoid an import cycle with `schema.py`.

## Final review

2026-10-15 — final review (report mode, under `/pipeline:ship`). Three independent perspectives
(SPEC/PLAN compliance, quality, tests) ran as separate subagents; every finding below was checked
in the code. The full suites of the touched modules are green (compliance: 588 + 428 passed;
tests: 504 passed, 31 of 40 mutations killed). As planned (Risks), the presser `summary` now
lists the nine committed header-less runs under "no header" with no winner.

**AC → evidence**

| AC | Evidence (code → test) |
|----|------------------------|
| AC1 | `dataset.py` `source_flags`, `schema.py` `load_eval_corpus`/`check_source_flags` → `test_sources.py::test_sources_only_search_returns_exactly_the_marked_posts`, `::test_load_refuses_a_corpus_whose_flags_disagree_with_the_sql_rule` |
| AC2 | `dataset.py` `PlayerQuery`, `ClaimedPost`, free-default discriminator, `carry_v1` → `test_dataset_v2.py` (round trip, v1 unchanged, carry-v1) |
| AC3 | `players.py` (both window rules, stale flag) + `cli.py` `build-player-slice` → `test_players.py` (six AC cases, `test_claimed_ids_equal_sql_claims`, read-only connection) |
| AC4 | `dataset.py` `export_corpus`, `cli.py` `export-corpus` → `test_export.py` |
| AC5 | `retrieval/variants.py` → `test_variants.py::test_initial_player_found_by_full_name_and_names_not_by_web_name` |
| AC6 | `corroboration/sources.py` `QUERY_VARIANT` → `tests/corroboration/test_sources.py` (default, other variant, no lookup by default); `tests/alerts` unchanged |
| AC7 | `player_metrics.py`, `runner.py` player searches → `test_player_metrics.py` (incl. more than 20 claimed), `test_runner_v2.py::test_player_slice_searches_window_and_sources` |
| AC8 | `runner.py` rows and per-query details → `test_runner_v2.py::test_run_reports_every_mode_variant_and_depth_and_free_slices`, `::test_result_holds_cut_ids_and_labels_per_configuration` |
| AC9 | `runner.py` `estimate_embedding`/`check_cost` → `test_runner_v2.py` (estimate and cap before embedding, no price row, CLI cap) |
| AC10 | `compare.py` → `test_compare.py` (see F3) |
| AC11 | `pooling.py` → `test_pooling.py` (see F2, F6) |
| AC12 | `judging.py` `render_input`/`export_inputs`, both prompts in `app/content/prompts/` → `test_judging.py` (see F5) |
| AC13 | `judging.py` `import_verdicts`/`agreement`/`estimate_export` → `test_import.py`, `test_judging.py::test_estimate_counts_calls_and_tokens` |
| AC14 | `review.py`, `labels.py`, runner header extras and refusal → `test_review.py`, `test_labels.py`, `test_runner_v2.py` header and refusal tests |
| AC15 | `scripts/session_judge.sh` → `tests/test_session_judge.py` (see F11) |
| AC16 | `core/eval_results.py` → `test_eval_results.py`, `test_shared_code.py::test_atomic_write_lives_once`, the guard test in each of the four evaluations (see F4) |
| AC17 | `presser/evaluation/summary.py` `group_runs` + `summary` command → `test_summary.py` (grouping, header-less apart) |
| AC18 | `retrieval/evaluation/cli.py`, `__main__.py` → `test_cli_entry.py`; `test_module_boundaries.py` unchanged (but see F1: `prelabel` fails on a flagged v2 set) |
| AC19 | `docs/EVALUATION.md`, DECISIONS rows 106–107, BACKLOG #53 and renumbering, ROADMAP → `tests/test_docs.py` |

All 20 plan steps are ticked with good reason; the four `minor` deviations are justified and
change no scope, architecture or schema; nothing outside the scope is in the branch.

**Findings**

- **F1** `worth-fixing` — `backend/app/retrieval/evaluation/labelling.py:107`, `cli.py:272` — `prelabel --set <v2 set>` with a non-null list snapshot loads the corpus with `load_corpus` and calls `load_eval_corpus(engine, corpus)` without `list_handles`, so the SQL rule marks every post a source while the corpus flags mark only some → `check_source_flags` raises before any labelling; an AC18 command cannot run on the sets it serves — load with `load_set` and pass `manifest.list_handles`, with a test on a flagged corpus.
- **F2** `worth-fixing` — `backend/app/retrieval/evaluation/pooling.py:81-92,113` — with `--depths 20,30,100` pooling searches once at leg depth 100 and pools the cut at 100, while `evaluate` measures 20 and 30 on a separate leg-depth-50 ranking (`runner.leg_groups`) → their cuts can hold posts never pooled, shown as unjudged; AC11's "union of what the configured runs would show" fails for depths above 50 — pool per `leg_groups(depths)` group, taking each group's largest-depth cut.
- **F3** `worth-fixing` — `backend/app/retrieval/evaluation/compare.py:25-31` — `load_runs` checks only `set_version` and `split` → two runs with a different `reference_judge`, `k` or `depths` land in one table although their recall was measured against different labels or settings — also refuse a mismatch of `reference_judge` and `k` (and print the depths per file).
- **F4** `worth-fixing` — `backend/tests/core/test_eval_results.py:97-102` — `test_repository_result_files_load_as_no_header` globs `evals/*/results/**/*.json`, which includes the gitignored `results/dev/`, and asserts no header → the first local `evaluate --split dev` in spec 017, or any newly committed headed result, breaks the test for every developer or in CI — check only the legacy files (an explicit list or `git ls-files`), or assert that each file loads and `read_header` gives `None` or a dict.
- **F5** `worth-fixing` — `backend/tests/retrieval/evaluation/test_judging.py:120` — the blind-input check searches for `str(BASE)` = `9000000000`, which no post ID (`9000000001…`) contains → printing every X ID in the inputs (mutation) stays green, so AC12's blind inputs are unproven (the code itself prints no X ID) — assert that no corpus or mapping `str(x_id)` appears in any input file.
- **F6** `worth-fixing` — `backend/tests/retrieval/evaluation/test_pooling.py:150` — no test has claimed posts push an unclaimed post past the smaller depth → pooling at `min(depths)` (mutation) stays green — claim ~15 top posts, pool with `[20, 30]`, assert a post at rank 21–30 is pooled.
- **F7** `worth-fixing` — `backend/tests/retrieval/evaluation/test_runner_v2.py:237` — `test_depths_above_fifty_run_their_own_search` checks only the rows' depths → collapsing every depth into one leg group (mutation) stays green — assert the number of searches and the leg depth passed to each.
- **F8** `worth-fixing` — `backend/tests/retrieval/evaluation/test_runner_v2.py:185` — nothing proves the free slice is searched without filters → adding `sources_only` to the free search (mutation) stays green — give a free query a relevant off-list post and assert it reaches the free top 10.
- **F9** `worth-fixing` — `backend/tests/retrieval/evaluation/test_players.py:203` — `test_splits_are_seeded_and_cover_both_slices` builds one player, so `round(0.3 × 1) = 0` and it is always `test` → an unseeded split (mutation) stays green and both slices are never shown — seed ~10 players, assert a fixed assignment for the seed and both splits present.
- **F10** `worth-fixing` — `backend/tests/retrieval/evaluation/test_judging.py:307` — the `--query-sample` test checks only the count → an unseeded sample (mutation) stays green, though spec 017's pilot needs a reproducible sample — export twice with one seed and compare the sampled IDs, once with another seed.
- **F11** `worth-fixing` — `backend/tests/test_session_judge.py:100` — the retrieval tests never check the system prompt's reply format → a retrieval run sent the presser format (mutation) stays green — assert the retrieval `REPLY_FORMAT` (`labels`) is in the `--system-prompt` argument and `claims` is not.
- **F12** `nit` — `backend/app/retrieval/evaluation/runner.py:424-426` — the header's `relevance_prompts` reads the current prompt files, not the versions the labels were made with (recorded only in `mapping.json`) → a prompt bumped between `import-judge` and `evaluate` misstates what produced the labels — record the export's prompt versions with the imported labels and copy them into the header, or rename the field.
- **F13** `nit` — `backend/app/retrieval/evaluation/judging.py:70-71` — only continuation lines of a post body are indented → a post whose text starts with `[P31]` matches the script's `^\[(P\d+)\]` and adds a phantom ID, so the verdict is rejected on every run — indent the whole body.
- **F14** `nit` — `backend/app/retrieval/evaluation/cli.py:512-527` — `import-judge` ignores `mapping.json`'s `for_judge` and accepts `--judge-model owner` → labels stored under the wrong judge, or a judge named `owner` whose entries a later `review` answer drops — refuse a `for_judge` mismatch and the name `owner`.
- **F15** `nit` — `backend/app/retrieval/evaluation/dataset.py:189` — `load_manifest` runs outside the `try` that turns errors into `SetError` → a malformed `set.json` crashes `review`/`carry-v1` with a traceback — move it inside the `try`.
- **F16** `nit` — `backend/app/retrieval/evaluation/compare.py:36-55` — a headed result file of another evaluation (presser, corroboration) passes `load_runs` → `KeyError` traceback instead of `error:` — refuse a header without `split` or a file without the `free` key, naming the file.

Rejected:

- "AC6 says existing corroboration tests pass unchanged, but `evaluation/test_cases.py` changed its monkeypatch target" — it is recorded in the step 2 deviation and changes no behaviour; it is not a defect.

Left out: 37 nit findings
