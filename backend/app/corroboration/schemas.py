from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Label = Literal["supports", "contradicts", "related", "unrelated"]
Origin = Literal["sql", "judge"]
Freshness = Literal["new", "context"]
GradeLevel = Literal["high", "medium", "low"]


def post_url(author_handle: str, x_id: int) -> str:
    return f"https://x.com/{author_handle}/status/{x_id}"


@dataclass(frozen=True)
class PlayerRef:
    season: str
    fpl_id: int
    web_name: str
    team_name: str | None = None


@dataclass(frozen=True)
class PostRef:
    x_id: int
    author_handle: str
    reposted_author_handle: str | None
    is_repost: bool
    created_at: datetime
    text: str

    @property
    def original_author(self) -> str:
        """The account the content comes from: a repost's original author."""
        if self.is_repost and self.reposted_author_handle:
            return self.reposted_author_handle
        return self.author_handle

    @property
    def url(self) -> str:
        return post_url(self.author_handle, self.x_id)


@dataclass(frozen=True)
class Claim:
    post: PostRef
    event_type: str
    certainty: str


@dataclass(frozen=True)
class LabelledPost:
    post: PostRef
    label: Label
    origin: Origin
    certainty: str | None = None
    event_type: str | None = None


@dataclass(frozen=True)
class Citation:
    x_id: int
    url: str
    author_handle: str
    reposted_author_handle: str | None
    created_at: datetime
    certainty: str | None
    origin: Origin
    label: Label
    freshness: Freshness
    event_type: str | None
    text: str

    @property
    def original_author(self) -> str:
        # reposted_author_handle is set only for reposts (see corroboration.service._citation)
        return self.reposted_author_handle or self.author_handle


@dataclass(frozen=True)
class Grade:
    level: GradeLevel
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalReport:
    status: Literal["ran", "skipped"]
    skipped_reason: str | None = None
    failed_legs: tuple[str, ...] = ()
    failure: str | None = None
    judged: int = 0
    unjudged: int = 0


@dataclass(frozen=True)
class Corroboration:
    player: PlayerRef
    as_of: datetime
    window_start: datetime
    new_since: datetime
    anchor: Claim | None
    supporting: list[Citation] = field(default_factory=list)
    contradicting: list[Citation] = field(default_factory=list)
    related: list[Citation] = field(default_factory=list)
    reversal: bool = False
    newer_contradiction: bool = False
    grade: Grade | None = None
    retrieval: RetrievalReport = field(default_factory=lambda: RetrievalReport("skipped"))
    trace_id: str | None = None
