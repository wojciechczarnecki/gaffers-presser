from datetime import datetime, timedelta

from sqlmodel import Session

from app.corroboration.schemas import PlayerRef
from app.corroboration.sources import (
    EMBED_TIMEOUT_SECONDS,
    MAX_JUDGED,
    resolve_player,
    retrieval_candidates,
    sql_claims,
    window_start,
)
from app.extraction.linking import PlayerAlias
from app.retrieval.store import save_embedded
from tests.corroboration.helpers import (
    GABRIEL_JESUS,
    GABRIEL_MAGALHAES,
    ISAK,
    NOW,
    SAKA,
    SEASON,
    add_claim,
    add_extraction,
    seed_reference,
)
from tests.retrieval.fakes import FakeEmbedder, RecordingTracer, SlowEmbedder
from tests.retrieval.helpers import MODEL, PRICES, add_tweet
from tests.tweets.membership_helpers import set_members

DEADLINE = NOW - timedelta(days=3)
SAKA_REF = PlayerRef(SEASON, SAKA, "Saka", "Arsenal")
NO_ALIASES = ([], [])


def test_window_start_is_the_latest_deadline_at_or_before_as_of(db):
    seed_reference(db, {5: NOW - timedelta(days=10), 6: DEADLINE, 7: NOW + timedelta(days=4)})
    with Session(db) as session:
        assert window_start(session, NOW) == DEADLINE
        assert window_start(session, DEADLINE) == DEADLINE
        assert window_start(session, DEADLINE - timedelta(seconds=1)) == NOW - timedelta(days=10)


def test_window_start_without_a_deadline_is_seven_days_before(db):
    seed_reference(db, {7: NOW + timedelta(days=4)})
    with Session(db) as session:
        assert window_start(session, NOW) == NOW - timedelta(days=7)


def test_window_start_since_overrides(db):
    seed_reference(db, {6: DEADLINE})
    since = NOW - timedelta(hours=5)
    with Session(db) as session:
        assert window_start(session, NOW, since) == since


def test_resolve_player_by_fpl_id_and_by_name(db):
    seed_reference(db)
    with Session(db) as session:
        assert resolve_player(session, "1", NO_ALIASES) == [SAKA_REF]
        assert resolve_player(session, "saka", NO_ALIASES) == [SAKA_REF]
        assert resolve_player(session, "Bukayo Saka", NO_ALIASES) == [SAKA_REF]
        assert resolve_player(session, "Isak", NO_ALIASES) == [
            PlayerRef(SEASON, ISAK, "Isak", "Newcastle")
        ]


def test_resolve_player_via_an_alias(db):
    seed_reference(db)
    aliases = ([PlayerAlias(alias="Starboy", season=SEASON, fpl_id=SAKA)], [])
    with Session(db) as session:
        assert resolve_player(session, "Starboy", aliases) == [SAKA_REF]


def test_resolve_player_ambiguous_returns_every_candidate(db):
    seed_reference(db)
    with Session(db) as session:
        found = resolve_player(session, "Gabriel", NO_ALIASES)
    assert {p.fpl_id for p in found} == {GABRIEL_JESUS, GABRIEL_MAGALHAES}


def test_resolve_player_unknown_is_empty(db):
    seed_reference(db)
    with Session(db) as session:
        assert resolve_player(session, "Nobody Atall", NO_ALIASES) == []
        assert resolve_player(session, "999", NO_ALIASES) == []


def test_resolve_player_looks_in_the_latest_season_only(db):
    seed_reference(db)
    with Session(db) as session:
        from app.fpl.models.reference import Player, Season, Team

        session.add(Season(label="2025/26"))
        session.flush()
        session.add(Team(season="2025/26", fpl_id=1, name="Old", short_name="OLD"))
        session.flush()
        session.add(
            Player(
                season="2025/26",
                fpl_id=50,
                web_name="Oldie",
                first_name="Old",
                second_name="Timer",
                team_fpl_id=1,
                position=3,
            )
        )
        session.commit()
        assert resolve_player(session, "Oldie", NO_ALIASES) == []
        assert resolve_player(session, "50", NO_ALIASES) == []


def test_sql_claims_only_the_players_current_events_in_the_window(db):
    seed_reference(db)
    start = NOW - timedelta(hours=10)
    add_claim(db, 1, SAKA, "out", created_at=start + timedelta(hours=1))
    add_claim(db, 2, ISAK, "out", created_at=start + timedelta(hours=2), mention="Isak")
    add_claim(db, 3, SAKA, "doubt", created_at=start - timedelta(minutes=1))
    add_claim(db, 4, SAKA, "doubt", created_at=NOW)
    add_claim(db, 5, SAKA, "confirmed_starter", created_at=start)
    add_tweet(db, 6, created_at=start + timedelta(hours=3))
    with Session(db) as session:
        claims = sql_claims(session, SAKA_REF, start, NOW)
    assert [(c.post.x_id, c.event_type) for c in claims] == [
        (5, "confirmed_starter"),
        (1, "out"),
    ]
    assert claims[1].certainty == "likely"


def test_sql_claims_one_claim_per_post_and_a_superseded_extraction_is_ignored(db):
    seed_reference(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=2), finished=1)
    add_extraction(db, 1, SAKA, "doubt", finished=9)
    add_extraction(db, 1, SAKA, "confirmed_starter", finished=20, status="failed")
    with Session(db) as session:
        claims = sql_claims(session, SAKA_REF, NOW - timedelta(days=1), NOW)
    assert [(c.post.x_id, c.event_type) for c in claims] == [(1, "doubt")]


def test_sql_claims_carry_the_repost_fields(db):
    seed_reference(db)
    add_claim(db, 1, SAKA, "out", is_repost=True, reposted_author_handle="origin", author="lister")
    with Session(db) as session:
        (claim,) = sql_claims(session, SAKA_REF, NOW - timedelta(days=1), NOW)
    assert (claim.post.is_repost, claim.post.reposted_author_handle) == (True, "origin")
    assert claim.post.author_handle == "lister"


def _embed(db, x_id: int) -> None:
    with Session(db) as session, session.begin():
        save_embedded(
            session,
            x_id=x_id,
            model=MODEL,
            vector=[1.0, 0.0, 0.0],
            input_tokens=5,
            cost_usd=None,
            latency_seconds=None,
            attempts=1,
            now=NOW,
        )


def _start() -> datetime:
    return NOW - timedelta(days=2)


def test_candidates_exclude_sql_claims_and_posts_outside_the_window(db):
    seed_reference(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3))
    add_tweet(db, 2, "Saka withdrawn injury", created_at=NOW - timedelta(hours=2))
    add_tweet(db, 3, "Saka news at the boundary", created_at=NOW)
    add_tweet(db, 4, "Saka after as of", created_at=NOW + timedelta(hours=1))
    add_tweet(db, 5, "Saka before the window", created_at=_start() - timedelta(minutes=1))
    result = retrieval_candidates(
        db, SAKA_REF, _start(), NOW, embedder=FakeEmbedder(), claimed_ids={1}, prices=PRICES
    )
    assert [p.x_id for p in result.posts] == [2]


def test_candidates_carry_the_repost_fields(db):
    seed_reference(db)
    add_tweet(
        db,
        2,
        "Saka injury",
        created_at=NOW - timedelta(hours=2),
        is_repost=True,
        reposted_author_handle="origin",
        author="lister",
    )
    (post,) = retrieval_candidates(
        db, SAKA_REF, _start(), NOW, embedder=FakeEmbedder(), prices=PRICES
    ).posts
    assert (post.author_handle, post.is_repost, post.reposted_author_handle) == (
        "lister",
        True,
        "origin",
    )


def test_candidates_are_capped_at_max_judged_in_rank_order(db):
    seed_reference(db)
    for x_id in range(1, 16):
        text = "Saka " + "injury " * (16 - x_id)
        add_tweet(db, x_id, text, created_at=NOW - timedelta(hours=1, minutes=x_id))
    result = retrieval_candidates(
        db, SAKA_REF, _start(), NOW, embedder=FakeEmbedder(), prices=PRICES
    )
    assert len(result.posts) == MAX_JUDGED


def test_candidates_search_uses_the_short_timeout_and_the_window_filters(db):
    seed_reference(db)
    add_tweet(db, 1, "Saka fit", created_at=NOW - timedelta(hours=2))
    _embed(db, 1)
    embedder = SlowEmbedder(delay_seconds=EMBED_TIMEOUT_SECONDS + 1)
    tracer = RecordingTracer()
    result = retrieval_candidates(
        db, SAKA_REF, _start(), NOW, embedder=embedder, tracer=tracer, prices=PRICES
    )
    assert embedder.timeouts == [EMBED_TIMEOUT_SECONDS]
    assert result.failed_legs == ("vector",)
    assert result.failure is not None and "TimeoutError" in result.failure
    assert [p.x_id for p in result.posts] == [1]
    (search,) = tracer.searches
    assert search["query"] == "Saka"
    assert search["mode"] == "hybrid"
    assert search["filters"]["since"] == _start().isoformat()
    assert search["filters"]["until"] == NOW.isoformat()


def test_context_post_never_a_claim_or_candidate(db):
    seed_reference(db)
    add_claim(db, 1, SAKA, "out", author="member", created_at=NOW - timedelta(hours=3))
    add_claim(
        db,
        2,
        SAKA,
        "out",
        author="outsider",
        text="Saka injury reply parent",
        created_at=NOW - timedelta(hours=2),
        embedded=True,
    )
    add_tweet(db, 3, "Saka injury chatter", author="outsider2", created_at=NOW - timedelta(hours=2))
    set_members(db, ["member"])

    with Session(db) as session:
        claims = sql_claims(session, SAKA_REF, _start(), NOW)
    candidates = retrieval_candidates(
        db,
        SAKA_REF,
        _start(),
        NOW,
        embedder=FakeEmbedder(),
        claimed_ids={1},
        prices=PRICES,
    )

    assert [c.post.x_id for c in claims] == [1]
    assert [p.x_id for p in candidates.posts] == []


def test_quoted_off_list_post_stays_a_claim_and_candidate(db):
    seed_reference(db)
    add_claim(db, 2, SAKA, "out", author="outsider", created_at=NOW - timedelta(hours=3))
    add_tweet(
        db,
        3,
        "confirmed",
        author="member",
        quoted_x_id=2,
        created_at=NOW - timedelta(hours=2),
    )
    set_members(db, ["member"])
    with Session(db) as session:
        claims = sql_claims(session, SAKA_REF, _start(), NOW)
    assert [c.post.x_id for c in claims] == [2]


def test_quote_carries_quoted_author(db):
    seed_reference(db)
    add_claim(db, 2, SAKA, "out", author="Outsider", created_at=NOW - timedelta(hours=3))
    add_claim(
        db,
        3,
        SAKA,
        "out",
        author="member",
        quoted_x_id=2,
        created_at=NOW - timedelta(hours=2),
    )
    add_claim(db, 4, SAKA, "out", author="member", created_at=NOW - timedelta(hours=1))
    add_tweet(
        db,
        5,
        "Saka injury quote",
        author="member",
        quoted_x_id=2,
        created_at=NOW - timedelta(hours=1),
    )
    set_members(db, ["member"])

    with Session(db) as session:
        claims = {c.post.x_id: c.post for c in sql_claims(session, SAKA_REF, _start(), NOW)}
    assert claims[3].quoted_author_handle == "Outsider"
    assert claims[4].quoted_author_handle is None
    assert claims[2].quoted_author_handle is None

    (candidate,) = [
        p
        for p in retrieval_candidates(
            db,
            SAKA_REF,
            _start(),
            NOW,
            embedder=FakeEmbedder(),
            claimed_ids={2, 3, 4},
            prices=PRICES,
        ).posts
    ]
    assert candidate.x_id == 5
    assert candidate.quoted_author_handle == "Outsider"
