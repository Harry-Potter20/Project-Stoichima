"""
Understat player stats ETL — scrapes per-player per-match stats and writes to
`player_match_stats` (and upserts into `players`).

Usage:
    cd backend
    python3 data_collection/understat_player_scraper.py [--leagues PL PD] [--seasons 2022 2023]

API endpoints used:
    GET /getLeagueData/{league}/{season}  → {dates: [...]}  (match IDs + dates)
    GET /getMatchData/{match_id}          → {rosters: {h: {...}, a: {...}}}

Upserts by (understat_player_id, understat_match_id) to stay idempotent.
"""

import asyncio
import json
import sys
import argparse
import logging
from datetime import datetime

import aiohttp
from sqlalchemy.orm import Session

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Player, PlayerMatchStats

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
SEMAPHORE_LIMIT = 4
_sem: asyncio.Semaphore | None = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(SEMAPHORE_LIMIT)
    return _sem


async def _fetch_json(session: aiohttp.ClientSession, url: str, referer: str = BASE_URL) -> dict | None:
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


async def _fetch_league_matches(
    session: aiohttp.ClientSession,
    league: str,
    season: int,
) -> list[tuple[int, str, str, str]]:
    """Returns (match_id, home_team, away_team, datetime_str) for the season."""
    url = f"{BASE_URL}/getLeagueData/{league}/{season}"
    referer = f"{BASE_URL}/league/{league}/{season}"
    data = await _fetch_json(session, url, referer=referer)
    if not data:
        return []

    matches = []
    for entry in data.get("dates", []):
        try:
            matches.append((
                int(entry["id"]),
                entry["h"]["title"],
                entry["a"]["title"],
                entry.get("datetime", ""),
            ))
        except (KeyError, ValueError, TypeError):
            continue

    log.info("  %s/%s — %d matches", league, season, len(matches))
    return matches


async def _fetch_match_rosters(
    session: aiohttp.ClientSession,
    match_id: int,
    home_team: str,
    away_team: str,
    match_date_str: str,
    competition: str,
    season: int,
) -> list[dict]:
    url = f"{BASE_URL}/getMatchData/{match_id}"
    referer = f"{BASE_URL}/match/{match_id}"
    data = await _fetch_json(session, url, referer=referer)
    if not data:
        return []

    rosters = data.get("rosters")
    if not rosters:
        return []

    try:
        match_date = datetime.fromisoformat(match_date_str.replace(" ", "T"))
    except (ValueError, AttributeError):
        match_date = None

    rows = []
    for side, team_name in (("h", home_team), ("a", away_team)):
        for p in rosters.get(side, {}).values():
            try:
                rows.append({
                    "understat_player_id": str(p.get("player_id", "")),
                    "understat_match_id":  str(match_id),
                    "player_name":         p.get("player", ""),
                    "team":                team_name,
                    "competition":         competition,
                    "match_date":          match_date,
                    "season":              season,
                    "minutes":             int(p.get("time", 0)),
                    "goals":               int(p.get("goals", 0)),
                    "assists":             int(p.get("assists", 0)),
                    "shots":               int(p.get("shots", 0)),
                    "key_passes":          int(p.get("key_passes", 0)),
                    "xg":                  float(p.get("xG", 0.0)),
                    "xa":                  float(p.get("xA", 0.0)),
                    "yellow_card":         bool(int(p.get("yellow_card", 0))),
                    "red_card":            bool(int(p.get("red_card", 0))),
                })
            except (KeyError, ValueError, TypeError):
                continue
    return rows


def _get_or_create_player(db: Session, name: str, team: str, competition: str) -> int:
    p = db.query(Player).filter_by(name=name, team=team, competition=competition).first()
    if p is None:
        p = Player(name=name, team=team, competition=competition)
        db.add(p)
        db.flush()
    return p.id


def _upsert_stats(rows: list[dict]) -> int:
    if not rows:
        return 0
    db = SessionLocal()
    inserted = 0
    try:
        for row in rows:
            if row.get("match_date") is None:
                continue
            existing = db.query(PlayerMatchStats).filter_by(
                understat_player_id=row["understat_player_id"],
                understat_match_id=row["understat_match_id"],
            ).first()
            if existing:
                continue
            player_id = _get_or_create_player(
                db, row["player_name"], row["team"], row["competition"]
            )
            stat = PlayerMatchStats(player_id=player_id, **row)
            db.add(stat)
            inserted += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return inserted


async def scrape_league(
    session: aiohttp.ClientSession,
    competition: str,
    season: int,
):
    league = LEAGUE_MAP[competition]
    log.info("Scraping players %s (%s) season %d", competition, league, season)

    matches = await _fetch_league_matches(session, league, season)
    if not matches:
        return

    tasks = [
        _fetch_match_rosters(session, mid, home, away, date_str, competition, season)
        for mid, home, away, date_str in matches
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_rows: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_rows.extend(r)

    inserted = _upsert_stats(all_rows)
    log.info("  → %d player-match rows, %d new inserted", len(all_rows), inserted)


async def run(competitions: list[str], seasons: list[int]):
    connector = aiohttp.TCPConnector(limit=SEMAPHORE_LIMIT)
    async with aiohttp.ClientSession(connector=connector) as session:
        for competition in competitions:
            for season in seasons:
                await scrape_league(session, competition, season)


def main():
    parser = argparse.ArgumentParser(description="Understat player stats ETL")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUE_MAP.keys()),
                        choices=list(LEAGUE_MAP.keys()))
    parser.add_argument("--seasons", nargs="+", type=int, default=DEFAULT_SEASONS)
    args = parser.parse_args()

    log.info("Starting player ETL — leagues=%s seasons=%s", args.leagues, args.seasons)
    start = datetime.now()
    asyncio.run(run(args.leagues, args.seasons))
    log.info("Done in %.1fs", (datetime.now() - start).total_seconds())


if __name__ == "__main__":
    main()
