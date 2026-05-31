"""
API-Football availability ETL — fetches injury/suspension data and writes to
the `player_availability` table.

Free tier: 100 requests/day. We stay well within that by fetching per-league
per-round rather than per-match. Run daily (e.g. cron) before each matchday.

Usage:
    cd backend
    python3 data_collection/api_football_client.py [--leagues PL PD]

Requires API_FOOTBALL_KEY in backend/.env.
Sign up at https://www.api-football.com/ (free tier is sufficient).

Player matching strategy: name fuzzy-match against the `players` table populated
by the Understat player scraper. Unmatched players are still stored — they get a
new Player row created on the fly.
"""

import sys
import time
import logging
import argparse
from datetime import datetime, date

import requests
from sqlalchemy.orm import Session

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Player, PlayerAvailability
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

API_BASE = "https://v3.football.api-sports.io"

# API-Football league IDs for our competition codes
LEAGUE_IDS = {
    "PL":  39,
    "PD":  140,
    "BL1": 78,
    "SA":  135,
    "FL1": 61,
}

# Current season year (API-Football uses start-year of season)
CURRENT_SEASON = 2024

# Seconds between requests — free tier allows ~1 req/sec sustained
REQUEST_DELAY = 1.2


class APIFootballClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "x-rapidapi-key":  api_key,
            "x-rapidapi-host": "v3.football.api-sports.io",
        })

    def _get(self, endpoint: str, params: dict) -> dict:
        url = f"{API_BASE}/{endpoint}"
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return resp.json()

    def get_injuries(self, league_id: int, season: int) -> list[dict]:
        """Fetch all current injuries/suspensions for a league season."""
        data = self._get("injuries", {"league": league_id, "season": season})
        return data.get("response", [])

    def get_fixtures(self, league_id: int, season: int, next_n: int = 10) -> list[dict]:
        """Fetch upcoming fixtures to anchor availability records."""
        data = self._get("fixtures", {
            "league":  league_id,
            "season":  season,
            "next":    next_n,
            "status":  "NS",  # Not Started
        })
        return data.get("response", [])

    def get_lineups(self, fixture_id: int) -> dict:
        """Fetch confirmed starting XIs for a fixture. Available ~60 min before KO."""
        data = self._get("fixtures/lineups", {"fixture": fixture_id})
        return data.get("response", [])


def _normalise(name: str) -> str:
    return name.lower().strip()


def _find_or_create_player(
    db: Session,
    name: str,
    team: str,
    competition: str,
) -> int:
    norm = _normalise(name)
    # Exact match first
    p = db.query(Player).filter_by(name=name, team=team).first()
    if p is None:
        # Fuzzy match: same team, similar name
        candidates = db.query(Player).filter_by(team=team).all()
        for c in candidates:
            if _normalise(c.name) == norm:
                return c.id
        # Create new
        p = Player(name=name, team=team, competition=competition)
        db.add(p)
        db.flush()
    return p.id


def _map_status(reason: str) -> str:
    reason = reason.lower()
    if "suspended" in reason or "suspension" in reason:
        return "Suspended"
    if "doubt" in reason:
        return "Doubtful"
    return "Injured"


def _upsert_availability(records: list[dict]) -> int:
    if not records:
        return 0
    db = SessionLocal()
    inserted = 0
    try:
        for rec in records:
            existing = db.query(PlayerAvailability).filter_by(
                player_name=rec["player_name"],
                team=rec["team"],
                match_date=rec["match_date"],
            ).first()
            if existing:
                existing.status = rec["status"]
                existing.return_date = rec.get("return_date")
            else:
                player_id = _find_or_create_player(
                    db, rec["player_name"], rec["team"], rec["competition"]
                )
                avail = PlayerAvailability(player_id=player_id, **rec)
                db.add(avail)
                inserted += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return inserted


def run(competitions: list[str]):
    settings = get_settings()
    if not settings.api_football_key:
        log.error(
            "API_FOOTBALL_KEY not set in .env — skipping availability ETL.\n"
            "Sign up at https://www.api-football.com/ and add the key to backend/.env."
        )
        return

    client = APIFootballClient(settings.api_football_key)

    for competition in competitions:
        league_id = LEAGUE_IDS.get(competition)
        if league_id is None:
            log.warning("No API-Football league ID for %s — skipping", competition)
            continue

        log.info("Fetching injuries for %s (league_id=%d)", competition, league_id)
        try:
            injuries = client.get_injuries(league_id, CURRENT_SEASON)
        except Exception as e:
            log.error("Failed to fetch injuries for %s: %s", competition, e)
            continue

        log.info("  %d injury/suspension records", len(injuries))

        # Also fetch upcoming fixtures so we can pin availability to match dates
        try:
            fixtures = client.get_fixtures(league_id, CURRENT_SEASON, next_n=20)
        except Exception as e:
            log.warning("Failed to fetch fixtures for %s: %s", competition, e)
            fixtures = []

        # Build a team → next match date lookup
        team_next_match: dict[str, date] = {}
        for fix in fixtures:
            home = fix["teams"]["home"]["name"]
            away = fix["teams"]["away"]["name"]
            try:
                match_date = datetime.fromisoformat(fix["fixture"]["date"]).date()
            except (KeyError, ValueError):
                continue
            for team in (home, away):
                if team not in team_next_match:
                    team_next_match[team] = match_date

        records = []
        for entry in injuries:
            try:
                player_name = entry["player"]["name"]
                team_name   = entry["team"]["name"]
                reason      = entry["player"].get("reason", "Injured")
                status      = _map_status(reason)
                match_date  = team_next_match.get(
                    team_name,
                    datetime.utcnow().date()
                )
                records.append({
                    "player_name": player_name,
                    "team":        team_name,
                    "competition": competition,
                    "match_date":  datetime.combine(match_date, datetime.min.time()),
                    "status":      status,
                    "return_date": None,
                    "source":      "api_football",
                })
            except (KeyError, TypeError):
                continue

        inserted = _upsert_availability(records)
        log.info("  → %d new availability records for %s", inserted, competition)


def collect_lineups_for_upcoming(competitions: list[str] | None = None) -> int:
    """
    Fetch confirmed starting XIs for upcoming matches that have an api_football_id.
    Lineups become available roughly 60 minutes before kickoff.
    Upserts ConfirmedLineup rows and returns the count of new/updated records.
    """
    from app.database import SessionLocal
    from app.models import Match, ConfirmedLineup
    from datetime import datetime, timedelta

    settings = get_settings()
    if not settings.api_football_key:
        log.warning("API_FOOTBALL_KEY not set — skipping lineup collection")
        return 0

    client = APIFootballClient(settings.api_football_key)
    now = datetime.utcnow()
    # Only fetch lineups for matches starting within the next 3 hours
    window_start = now - timedelta(hours=1)
    window_end   = now + timedelta(hours=3)

    with SessionLocal() as db:
        query = db.query(Match).filter(
            Match.api_football_id != None,
            Match.status.in_(["SCHEDULED", "TIMED"]),
            Match.match_date >= window_start,
            Match.match_date <= window_end,
        )
        if competitions:
            query = query.filter(Match.competition.in_(competitions))
        upcoming = query.all()

    if not upcoming:
        log.info("Lineup poll: no matches in window (%s – %s)", window_start, window_end)
        return 0

    log.info("Lineup poll: checking %d fixtures", len(upcoming))
    updated = 0

    with SessionLocal() as db:
        for match in upcoming:
            try:
                lineups = client.get_lineups(match.api_football_id)
            except Exception as e:
                log.warning("  lineup fetch failed for fixture %s: %s", match.api_football_id, e)
                continue

            if not lineups:
                continue  # not confirmed yet

            for team_data in lineups:
                team_name  = team_data.get("team", {}).get("name", "")
                formation  = team_data.get("formation")
                starters   = [p["player"]["name"] for p in team_data.get("startXI", [])]

                existing = db.query(ConfirmedLineup).filter(
                    ConfirmedLineup.match_id == match.id,
                    ConfirmedLineup.team    == team_name,
                ).first()

                if existing:
                    existing.formation    = formation
                    existing.starters     = ",".join(starters)
                    existing.confirmed_at = datetime.utcnow()
                else:
                    db.add(ConfirmedLineup(
                        match_id     = match.id,
                        competition  = match.competition,
                        team         = team_name,
                        match_date   = match.match_date,
                        formation    = formation,
                        starters     = ",".join(starters),
                    ))
                updated += 1

        db.commit()

    log.info("Lineup poll: upserted %d lineup records", updated)
    return updated


def main():
    parser = argparse.ArgumentParser(description="API-Football availability ETL")
    parser.add_argument(
        "--leagues", nargs="+", default=list(LEAGUE_IDS.keys()),
        choices=list(LEAGUE_IDS.keys()),
    )
    args = parser.parse_args()
    run(args.leagues)


if __name__ == "__main__":
    main()
