from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKLOG = ROOT / "docs" / "BACKLOG.md"


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
