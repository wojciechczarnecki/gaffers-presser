import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "session_judge.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("python3") is None,
    reason="bash and python3 are needed",
)

STUB = """#!/usr/bin/env python3
import json, os, sys
log = os.environ["STUB_LOG"]
with open(log, "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
n = sum(1 for _ in open(log))
sys.stdin.read()
reply = open(os.path.join(os.environ["STUB_REPLY_DIR"], f"{n}.txt")).read()
print(json.dumps({"result": reply, "usage": {"input_tokens": 10, "output_tokens": 5}}))
"""


class Judge:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        stub = self.bin / "claude"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.replies = tmp_path / "replies"
        self.replies.mkdir()
        self.log = tmp_path / "calls.log"
        self.dir = tmp_path / "export"
        (self.dir / "inputs").mkdir(parents=True)
        (self.dir / "inputs" / "judge-prompt.md").write_text("prompt")

    def add_input(self, name, text):
        (self.dir / "inputs" / f"{name}.md").write_text(text)

    def reply(self, n, text):
        (self.replies / f"{n}.txt").write_text(text)

    def calls(self):
        return (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists()
            else []
        )

    def run(self, *args):
        import os

        env = os.environ | {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "STUB_REPLY_DIR": str(self.replies),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.dir), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def verdict(self, name):
        path = self.dir / "verdicts" / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None


@pytest.fixture
def judge(tmp_path):
    return Judge(tmp_path)


def test_presser_default_writes_a_valid_claims_verdict(judge):
    judge.add_input("c1", "# Presser\ntext")
    judge.reply(1, '{"claims": [{"claim": "x", "label": "supported"}]}')
    done = judge.run("1")
    assert done.returncode == 0, done.stderr
    assert judge.verdict("c1") == {"claims": [{"claim": "x", "label": "supported"}]}
    assert "--effort" not in judge.calls()[0]
    assert "claims" in judge.calls()[0][judge.calls()[0].index("--system-prompt") + 1]


def test_presser_rejects_an_unknown_label(judge):
    judge.add_input("c1", "text")
    judge.reply(1, '{"claims": [{"claim": "x", "label": "maybe"}]}')
    judge.run("1")
    assert judge.verdict("c1") is None
    assert (judge.dir / "raw" / "c1.invalid.json").exists()


def test_retrieval_rejects_a_missing_label_and_a_rerun_judges_only_that_input(judge):
    judge.add_input("a", "header\n\n[P1] @x\nfirst\n\n[P2] @y\nsecond\n")
    judge.add_input("b", "header\n\n[P1] @x\nfirst\n\n[P2] @y\nsecond\n")
    judge.reply(1, '{"labels": {"P1": true}}')
    judge.reply(2, '{"labels": {"P1": true, "P2": false}}')
    first = judge.run("1", "sonnet", "retrieval")
    assert first.returncode == 0, first.stderr
    assert judge.verdict("a") is None
    assert judge.verdict("b") == {"labels": {"P1": True, "P2": False}}
    assert (judge.dir / "raw" / "a.invalid.json").exists()
    assert not (judge.dir / "raw" / "a.json").exists()
    assert len(judge.calls()) == 2

    judge.reply(3, '```json\n{"labels": {"P1": false, "P2": true}}\n```')
    judge.run("1", "sonnet", "retrieval")
    assert len(judge.calls()) == 3
    assert judge.verdict("a") == {"labels": {"P1": False, "P2": True}}
    tokens = judge.run("1", "sonnet", "retrieval").stdout
    assert "judge calls: 3  tokens: 45" in tokens


@pytest.mark.parametrize(
    "reply",
    ['{"labels": {"P1": "yes", "P2": true}}', '{"labels": {"P1": true, "P2": true, "P3": false}}'],
)
def test_retrieval_rejects_a_non_boolean_value_and_an_extra_id(judge, reply):
    judge.add_input("a", "[P1] @x\nfirst\n\n[P2] @y\nsecond\n")
    judge.reply(1, reply)
    judge.run("1", "sonnet", "retrieval")
    assert judge.verdict("a") is None
    assert (judge.dir / "raw" / "a.invalid.json").exists()


def test_the_fifth_argument_adds_the_effort_flag(judge):
    judge.add_input("a", "[P1] @x\nfirst\n")
    judge.reply(1, '{"labels": {"P1": true}}')
    judge.run("1", "sonnet", "retrieval", "medium")
    call = judge.calls()[0]
    assert call[call.index("--effort") + 1] == "medium"
    assert call[call.index("--model") + 1] == "sonnet"


def test_an_unknown_schema_is_refused(judge):
    done = judge.run("1", "sonnet", "other")
    assert done.returncode == 2
    assert "unknown schema" in done.stderr
