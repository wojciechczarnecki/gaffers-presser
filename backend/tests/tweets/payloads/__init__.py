import copy
import gzip
import json
from pathlib import Path
from typing import Any

PAYLOADS_DIR = Path(__file__).resolve().parent
_cache: dict[str, Any] = {}


def load(name: str) -> Any:
    if name not in _cache:
        with gzip.open(PAYLOADS_DIR / f"{name}.json.gz", "rt", encoding="utf-8") as f:
            _cache[name] = json.load(f)
    return copy.deepcopy(_cache[name])


# Posts of the recorded twscrape-page-conversation pages (see README.md).
# Page 1:
REPOST = 99990000520  # a member's repost of an off-list post
REPOSTED_ORIGINAL = 99990000510  # never yielded as a post of its own
QUOTE_OF_OLD_POST = 99990000490
OLD_QUOTED = 99990000180  # a member's older post, embedded only
MODULE_ROOT, MODULE_HEAD = 99990000460, 99990000470  # a module of two member posts
QUOTE_OF_ENTRY, PLAIN = 99990000450, 99990000440  # PLAIN is also quoted inside QUOTE_OF_ENTRY
# A module: an off-list post, a member reply to a post absent from the page, the member's
# reply to it.
OFF_LIST_ROOT = 99990000370
MODULE_REPLY_TO_ABSENT, OFF_LIST_MODULE_HEAD = 99990000420, 99990000430
LATER_PLAIN = 99990000400
# Page 2:
QUOTED_OFF_LIST = (99990000320, 99990000240)  # off-list posts quoted by members
SINCE_ID = 99990000260
OLDER_THAN_SINCE = 99990000210
PAGE_2_MEMBER_POSTS = (
    99990000350,
    99990000340,
    99990000290,
    99990000270,
    SINCE_ID,
    99990000250,
    99990000230,
    OLDER_THAN_SINCE,
    99990000200,
)
