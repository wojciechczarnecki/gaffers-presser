from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKLOG = ROOT / "docs" / "BACKLOG.md"
REPORT = ROOT / "docs" / "reports" / "twscrape-list-replies-2026-10.md"
LATENCY_REPORT = ROOT / "docs" / "reports" / "tweet-source-latency-2026-09.md"
RETRIEVAL_REPORT = ROOT / "docs" / "reports" / "retrieval-eval-v2.md"


def _rows() -> list[str]:
    return [line for line in BACKLOG.read_text(encoding="utf-8").splitlines() if line[:1] == "|"]


def test_backlog_has_reranking_and_ann_entries():
    rows = _rows()
    reranking = [row for row in rows if "rerank" in row.lower()]
    ann = [row for row in rows if "HNSW" in row]
    assert len(reranking) == 1 and " P3 " in reranking[0]
    assert "a later retrieval evaluation" in reranking[0]
    assert len(ann) == 1 and " P3 " in ann[0]
    for row in (*reranking, *ann):
        assert row.rstrip().rstrip("|").rsplit("|", 1)[-1].strip(), "an entry needs a trigger"


def test_backlog_18_closed_and_corroboration_entries_kept():
    rows = _rows()
    assert not [row for row in rows if "query-embedding timeout" in row.lower()]
    assert not [row for row in rows if row.startswith("| 18 |")]
    for number, priority in (("19", "P2"), ("20", "P3")):
        (row,) = [row for row in rows if row.startswith(f"| {number} |")]
        assert f" {priority} " in row
        assert row.rstrip().rstrip("|").rsplit("|", 1)[-1].strip(), "an entry needs a trigger"


def test_backlog_has_delivery_webhooks_entry():
    (row,) = [row for row in _rows() if "webhook" in row.lower()]
    assert " P3 " in row
    assert row.rstrip().rstrip("|").rsplit("|", 1)[-1].strip(), "an entry needs a trigger"


def test_backlog_11_closed_and_off_list_entries_added():
    rows = _rows()
    assert not [row for row in rows if row.startswith("| 11 |")]
    (membership,) = [row for row in rows if "List membership in the `twitterapi_io`" in row]
    (quoted_text,) = [row for row in rows if "quoted post's text" in row]
    assert " P3 " in membership and " P2 " in quoted_text
    for row in (membership, quoted_text):
        assert row.rstrip().rstrip("|").rsplit("|", 1)[-1].strip(), "an entry needs a trigger"


def test_list_replies_report_exists_and_is_linked():
    report = REPORT.read_text(encoding="utf-8")
    for needle in ("2104662407054266709", "2104663688330486065", "FPL_TomHadley", "Teamnewsandtix"):
        assert needle in report
    assert "When to revisit" in report
    assert "twscrape-list-replies-2026-10.md" in LATENCY_REPORT.read_text(encoding="utf-8")


def test_decisions_and_deployment_cover_list_membership():
    decisions = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    (row,) = [line for line in decisions.splitlines() if "`list_membership`" in line]
    assert "counts as the account of the quoted post" in row
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "`0010`" in deployment
    assert "app.tweets members" in deployment


def test_backlog_has_deadline_day_slot_entry():
    (row,) = [row for row in _rows() if "deadline day" in row.lower()]
    assert " P2 " in row
    assert row.rstrip().rstrip("|").rsplit("|", 1)[-1].strip(), "an entry needs a trigger"


def test_decisions_cover_alert_slots():
    decisions = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    (row,) = [
        line for line in decisions.splitlines() if "ALERT_SLOTS`" in line and "D-1@20:00" in line
    ]
    assert "2026-10-02" in row and "supersedes" in row.lower()


def test_backlog_32_and_35_closed_by_the_comparison_and_writer_13():
    assert not [row for row in _rows() if row.startswith(("| 32 |", "| 35 |"))]
    decisions = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    (row,) = [line for line in decisions.splitlines() if "Writer prompt version 13" in line]
    for needle in ("team + owner", "BACKLOG #35", "gpt-6-sol"):
        assert needle in row, needle


def test_backlog_has_match_events_source_item():
    (row,) = [row for row in _rows() if row.startswith("| 34 | P3 |")]
    for needle in ("match-events", "VAR", "the owner misses timeline stories in the presser"):
        assert needle in row, needle


def test_decisions_cover_match_stories():
    text = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    for needle in ("Z boiska", "Chipy", "2500", "key moment", "team_name", "version 3"):
        assert needle in text, needle


def test_decisions_cover_gameweek_rank_and_overall():
    text = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    for needle in ("GW rank", "Overall", "10k"):
        assert needle in text, needle


def test_deployment_lists_migration_0012():
    text = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "0012" in text and "gameweek_rank" in text


def test_decisions_cover_full_match_stats():
    text = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    needles = (
        "live_stats",
        "player_fixture_stat",
        "decisive",
        "bonus_missed_by_one",
        "hat_trick",
        "keeper_wall",
        "0.70",
        "0.15",
        "ICT",
        "version 4",
        "points_modification",
        "routine stats (minutes, goals conceded)",
        "this amends the 2026-10-11 key-moment row",
        "this supersedes the fact sheet version 3, writer prompt version 5, judge prompt version 4"
        " and set v3 parts of the 2026-10-11 rows",
    )
    for needle in needles:
        assert needle in text, needle


def test_deployment_lists_migration_0013():
    text = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    for needle in ("0013", "live_stats", "player_fixture_stat", "backfill"):
        assert needle in text, needle


def test_evaluation_md_describes_the_retrieval_procedure():
    text = (ROOT / "docs" / "EVALUATION.md").read_text(encoding="utf-8")
    for needle in (
        "build-player-slice",
        "export-judge-inputs",
        "estimate",
        "import-judge",
        "agreement",
        "review",
        "evaluate",
        "compare",
        "session_judge.sh",
        "per query",
        "scratch database",
    ):
        assert needle in text, needle


def test_backlog_window_row_and_017_018_renumbering():
    rows = _rows()
    for number in ("43", "44", "45", "48"):
        assert not [row for row in rows if row.startswith(f"| {number} |")], number
    assert not [row for row in rows if row.startswith("| 53 |")]
    assert not [row for row in rows if "last match" in row and "window_floor" in row]
    (docstrings,) = [row for row in rows if row.startswith("| 50 |")]
    assert "alerts/config.py:55" not in docstrings
    for number in ("46", "47"):
        (row,) = [row for row in rows if row.startswith(f"| {number} |")]
        assert "spec 018" in row and "spec 017" not in row, number
    (row,) = [row for row in rows if row.startswith("| 49 |")]
    assert "specs 016–018" in row
    roadmap = (ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
    assert "planned as 018" in roadmap and "planned as 017" not in roadmap


def test_decisions_cover_retrieval_set_v2_and_results_header():
    decisions = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    (v2,) = [line for line in decisions.splitlines() if "Retrieval evaluation set v2" in line]
    for needle in ("player", "free", "session judge", "supersedes"):
        assert needle in v2, needle
    (header,) = [line for line in decisions.splitlines() if "eval_results.py" in line]
    for needle in (
        "run header",
        "atomic",
        "overwrite guard",
        "app.retrieval.evaluation",
        "summary",
    ):
        assert needle in header, needle


def test_retrieval_report_v2_has_every_section():
    text = RETRIEVAL_REPORT.read_text(encoding="utf-8")
    for heading in (
        "Set",
        "Labelling",
        "Relevance",
        "Tokens and spend",
        "compare",
        "Winner",
        "Misses",
        "Reranking",
        "Stale",
        "Limits",
    ):
        assert f"## {heading}" in text or f"## The {heading.lower()}" in text, heading
    for phrase in ("69", "0.05", "claude-opus-5-5", "qwen"):
        assert phrase in text, phrase


def test_adr_0009_and_decisions_row_for_the_retrieval_winner():
    adr = (ROOT / "docs" / "adr" / "0009-default-embedding-model.md").read_text(encoding="utf-8")
    for model in (
        "openai/text-embedding-3-large",
        "openai/text-embedding-3-small",
        "google/gemini-embedding-001",
        "qwen/qwen3-embedding-8b",
    ):
        assert model in adr, model
    decisions = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    (row,) = [line for line in decisions.splitlines() if "adr/0009-" in line]
    for needle in ('`QUERY_VARIANT = "names"`', '`SEARCH_MODE = "hybrid"`', "`SEARCH_LIMIT = 20`"):
        assert needle in row, needle


def test_decisions_supersede_the_previous_deadline_window():
    decisions = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    (row,) = [line for line in decisions.splitlines() if "previous_gameweek_end" in line]
    for needle in ("window_floor", "last match", "supersedes"):
        assert needle in row, needle


def test_readme_results_free_slice_row_and_roadmap():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Retrieval results" in readme
    results = readme.split("## Retrieval results", 1)[1].split("\n## ", 1)[0]
    assert "docs/reports/retrieval-eval-v2.md" in results
    (free,) = [row for row in _rows() if row.startswith("| 56 |")]
    assert " P3 " in free and "`free` slice" in free
    assert (
        free.rstrip().rstrip("|").rsplit("|", 1)[-1].strip() == "building the Q&A agent (Stage 4)"
    )
    roadmap = (ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
    (qa,) = [line for line in roadmap.splitlines() if "Q&A agent with cited sources" in line]
    assert "#56" in qa
    item = roadmap.split("- [x] Retrieval evaluation report on the GW6 window", 1)[1]
    item = item.split("\n- [", 1)[0]
    assert "reports/retrieval-eval-v2.md" in item and "adr/0009-" in item
    evaluation = (ROOT / "docs" / "EVALUATION.md").read_text(encoding="utf-8")
    assert "2026-10-10T10:00" in evaluation and "2026-10-10T18:30" not in evaluation
    assert "not judged until Stage 4" in evaluation
