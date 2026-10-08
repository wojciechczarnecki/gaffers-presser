from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKLOG = ROOT / "docs" / "BACKLOG.md"
REPORT = ROOT / "docs" / "reports" / "twscrape-list-replies-2026-10.md"
LATENCY_REPORT = ROOT / "docs" / "reports" / "tweet-source-latency-2026-09.md"


def _rows() -> list[str]:
    return [line for line in BACKLOG.read_text(encoding="utf-8").splitlines() if line[:1] == "|"]


def test_backlog_has_reranking_and_ann_entries():
    rows = _rows()
    reranking = [row for row in rows if "rerank" in row.lower()]
    ann = [row for row in rows if "HNSW" in row]
    assert len(reranking) == 1 and " P2 " in reranking[0]
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
