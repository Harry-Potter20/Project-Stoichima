"""
Understat batch ETL — scrapes shot-level data and writes to the `shots` table.

Usage:
    cd backend
    python3 data_collection/understat_scraper.py [--leagues PL PD] [--seasons 2022 2023]

Understat league name mapping (their URL slugs):
    PL  → EPL
    PD  → La_liga
    BL1 → Bundesliga
    SA  → Serie_A
    FL1 → Ligue_1

API endpoints (as of 2025):
    GET /getLeagueData/{league}/{season}  → {dates: [...], teams: {...}, players: {...}}
    GET /getMatchData/{match_id}          → {shots: {h: [...], a: [...]}, rosters: {...}}

This is a batch operation — never called during inference. Run nightly or before
training to keep the shots table fresh. Upserts by understat_id to stay idempotent.
"""

import asyncio
import json
import sys
import argparse
import logging
from datetime import datetime

import aiohttp
from sqlalchemy.exc import IntegrityError

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Shot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://understat.com"

LEAGUE_MAP = {
    "PL":  "EPL",
    "PD":  "La_liga",
    "BL1": "Bundesliga",
    "SA":  "Serie_A",
    "FL1": "Ligue_1",
}

DEFAULT_SEASONS = list(range(2017, 2025))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

SEMAPHORE_LIMIT = 4
_sem: asyncio.Semaphore | None = None


def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(SEMAPHORE_LIMIT)
    return _sem


async def _fetch_json(session: aiohttp.ClientSession, url: str, referer: str = BASE_URL) -> dict | list | None:
    headers = {**HEADERS, "Referer": referer}
    async with _get_sem():
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
                r.raise_for_status()
                text = await r.text()
                return json.loads(text)
        except Exception as e:
            log.warning("Failed to fetch %s: %s", url, e)
            return None


async def _fetch_league_match_ids(
    session: aiohttp.ClientSession,
    league: str,
    season: int,
) -> list[tuple[int, str, str]]:
    """Returns list of (understat_match_id, home_team, away_team) for the season."""
    url = f"{BASE_URL}/getLeagueData/{league}/{season}"
    referer = f"{BASE_URL}/league/{league}/{season}"
    data = await _fetch_json(session, url, referer=referer)
    if data is None:
        log.warning("No data returned for %s/%s", league, season)
        return []

    dates = data.get("dates", [])
    if not dates:
        log.warning("Empty dates list for %s/%s", league, season)
        return []

    matches = []
    for entry in dates:
        try:
            mid = int(entry["id"])
            home = entry["h"]["title"]
            away = entry["a"]["title"]
            matches.append((mid, home, away))
        except (KeyError, ValueError, TypeError):
            continue

    log.info("  %s/%s — %d matches found", league, season, len(matches))
    return matches


async def _fetch_match_shots(
    session: aiohttp.ClientSession,
    match_id: int,
    home_team: str,
    away_team: str,
    competition: str,
    season: int,
) -> list[dict]:
    """Fetches shots for a single match via the getMatchData API."""
    url = f"{BASE_URL}/getMatchData/{match_id}"
    referer = f"{BASE_URL}/match/{match_id}"
    data = await _fetch_json(session, url, referer=referer)
    if data is None:
        return []

    shots_data = data.get("shots")
    if not shots_data:
        return []

    rows = []
    for side in ("h", "a"):
        team_name = home_team if side == "h" else away_team
        for s in shots_data.get(side, []):
            try:
                rows.append({
                    "understat_id":  str(s.get("id", "")),
                    "competition":   competition,
                    "season":        season,
                    "home_team":     home_team,
                    "away_team":     away_team,
                    "minute":        int(s.get("minute", 0)),
                    "player":        s.get("player", ""),
                    "player_team":   team_name,
                    "x":             float(s.get("X", 0.0)),
                    "y":             float(s.get("Y", 0.0)),
                    "result":        s.get("result", ""),
                    "shot_type":     s.get("shotType", ""),
                    "situation":     s.get("situation", ""),
                    "last_action":   s.get("lastAction", ""),
                    "understat_xg":  float(s.get("xG", 0.0)),
                })
            except (KeyError, ValueError, TypeError):
                continue

    return rows


def _upsert_shots(rows: list[dict]) -> int:
    """Insert shots, skipping duplicates by understat_id. Returns count inserted."""
    if not rows:
        return 0
    db = SessionLocal()
    inserted = 0
    try:
        for row in rows:
            shot = Shot(**row)
            db.add(shot)
            try:
                db.flush()
                inserted += 1
            except IntegrityError:
                db.rollback()
        db.commit()
    finally:
        db.close()
    return inserted


async def scrape_league(
    session: aiohttp.ClientSession,
    competition: str,
    season: int,
):
    league = LEAGUE_MAP[competition]
    log.info("Scraping %s (%s) season %d", competition, league, season)

    match_ids = await _fetch_league_match_ids(session, league, season)
    if not match_ids:
        return

    tasks = [
        _fetch_match_shots(session, mid, home, away, competition, season)
        for mid, home, away in match_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_rows: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_rows.extend(r)

    inserted = _upsert_shots(all_rows)
    log.info("  → %d shots scraped, %d new rows inserted", len(all_rows), inserted)


async def run(competitions: list[str], seasons: list[int]):
    connector = aiohttp.TCPConnector(limit=SEMAPHORE_LIMIT)
    async with aiohttp.ClientSession(connector=connector) as session:
        for competition in competitions:
            for season in seasons:
                await scrape_league(session, competition, season)


def main():
    parser = argparse.ArgumentParser(description="Understat shot ETL")
    parser.add_argument(
        "--leagues", nargs="+", default=list(LEAGUE_MAP.keys()),
        choices=list(LEAGUE_MAP.keys()),
        help="Competition IDs to scrape (default: all)"
    )
    parser.add_argument(
        "--seasons", nargs="+", type=int, default=DEFAULT_SEASONS,
        help="Season start-years to scrape (default: 2017-2024)"
    )
    args = parser.parse_args()

    log.info("Starting Understat ETL — leagues=%s seasons=%s", args.leagues, args.seasons)
    start = datetime.now()
    asyncio.run(run(args.leagues, args.seasons))
    elapsed = (datetime.now() - start).total_seconds()
    log.info("Done in %.1fs", elapsed)


if __name__ == "__main__":
    main()
