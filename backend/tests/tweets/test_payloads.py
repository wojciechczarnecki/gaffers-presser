import gzip
import re

import pytest

from tests.tweets.payloads import PAYLOADS_DIR

PAYLOAD_FILES = sorted(PAYLOADS_DIR.glob("*.json.gz"))


def _text(path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return f.read()


def test_payload_files_exist():
    assert len(PAYLOAD_FILES) == 6


@pytest.mark.parametrize("path", PAYLOAD_FILES, ids=lambda path: path.name)
def test_payloads_carry_no_real_account_data(path):
    text = _text(path)
    assert "pbs.twimg.com" not in text
    assert "t.co/" not in text
    assert not re.search(r'"location": "[^"]', text)
    long_ids = re.findall(r"\d{8,}", text)
    assert all(value.startswith("9999") for value in long_ids), long_ids
