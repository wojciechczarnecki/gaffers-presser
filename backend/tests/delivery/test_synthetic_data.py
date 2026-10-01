import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")
RESEND_KEY = re.compile(r"re_[A-Za-z0-9]{20,}")
ALLOWED_DOMAINS = {"example.com", "example.test", "localhost.invalid", "resend.dev"}
ROOTS = ("backend/app", "backend/tests", "backend/.env.example", "docs")


def tracked_text_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--", *ROOTS],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode()
    files = []
    for name in filter(None, listing.split("\0")):
        path = REPO / name
        if not path.is_file():
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(path)
    return files


def test_only_synthetic_addresses_and_keys():
    files = tracked_text_files()
    assert files
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for match in ADDRESS.finditer(text):
            if match.group(1).lower() not in ALLOWED_DOMAINS:
                offenders.append(f"{path.relative_to(REPO)}: address")
        if RESEND_KEY.search(text):
            offenders.append(f"{path.relative_to(REPO)}: key")
    assert offenders == []
