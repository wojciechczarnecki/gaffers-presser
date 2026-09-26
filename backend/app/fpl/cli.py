import argparse
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.errors import CollectorError
from app.core.settings import Settings, parse_league_ids
from app.db.engine import make_engine
from app.fpl.backfill import backfill
from app.fpl.client import FplClient
from app.fpl.leagues import sync_leagues
from app.fpl.reference import sync_reference
from app.fpl.results import sync_results
from app.fpl.snapshot import take_deadline_snapshot

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.fpl")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("reference-sync")

    snapshot_parser = sub.add_parser("deadline-snapshot")
    snapshot_parser.add_argument("--gameweek", type=int, required=True)

    league_parser = sub.add_parser("league-sync")
    league_parser.add_argument("--gameweek", type=int, required=True)

    results_parser = sub.add_parser("results-sync")
    results_parser.add_argument("--gameweek", type=int, required=True)

    sub.add_parser("backfill")

    return parser


def run_command(
    argv: list[str],
    *,
    engine: Engine,
    client: FplClient,
    league_ids_raw: str,
    now: datetime,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        with Session(engine) as session, session.begin():
            if args.command == "reference-sync":
                season = sync_reference(session, client, now)
                logger.info("reference sync: season=%s", season)
            elif args.command == "deadline-snapshot":
                take_deadline_snapshot(session, client, args.gameweek, now)
                logger.info("deadline snapshot: gameweek=%d", args.gameweek)
            elif args.command == "league-sync":
                league_ids = parse_league_ids(league_ids_raw)
                sync_reference(session, client, now)
                sync_leagues(session, client, league_ids, [args.gameweek], now)
            elif args.command == "results-sync":
                sync_reference(session, client, now)
                sync_results(session, client, args.gameweek, now)
            elif args.command == "backfill":
                league_ids = parse_league_ids(league_ids_raw)
                backfill(session, client, league_ids, now)
    except CollectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    _build_parser().parse_args(raw_argv)  # handles --help/usage errors before Settings()

    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    engine = make_engine(settings.database_url)
    client = FplClient()
    now = datetime.now(UTC)
    return run_command(
        raw_argv,
        engine=engine,
        client=client,
        league_ids_raw=settings.fpl_league_ids,
        now=now,
    )
