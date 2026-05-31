"""
ELO rating ETL.

Club ELO  — fetched from clubelo.com (free, no key, returns CSV).
            http://api.clubelo.com/YYYY-MM-DD  →  Rank,Club,Country,Level,Elo,From,To

International ELO — computed from our own WC/EC match history using a standard
                    Elo update rule (K=40, home-field bonus omitted for neutrals).

Run:
    python3 -c "from data_collection.elo_client import run; run()"
"""

import csv
import io
import logging
from datetime import datetime, date

import requests

from app.database import SessionLocal
from app.models import EloRating, Match

log = logging.getLogger(__name__)

CLUBELO_URL = "http://api.clubelo.com/{date}"

# Map our competition IDs to the country/level values used in the clubelo CSV
COMP_COUNTRY_LEVEL: dict[str, tuple[str, int]] = {
    "PL":  ("ENG", 1),
    "PD":  ("ESP", 1),
    "BL1": ("GER", 1),
    "SA":  ("ITA", 1),
    "FL1": ("FRA", 1),
}

# International competition IDs
INTL_COMPETITIONS = {"WC", "EC"}

# Elo parameters
K_FACTOR   = 40     # update magnitude per result
HOME_BONUS = 60     # added to home team's Elo before calculating expected score
DEFAULT_ELO = 1500  # starting value for unseen teams


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _update(rating: float, expected: float, actual: float, k: float = K_FACTOR) -> float:
    return rating + k * (actual - expected)


# ---------------------------------------------------------------------------
# Club ELO from clubelo.com
# ---------------------------------------------------------------------------

def fetch_club_elo(as_of: date | None = None) -> dict[str, float]:
    """Return {team_name: elo} from clubelo.com for a given date (default today)."""
    d = as_of or date.today()
    url = CLUBELO_URL.format(date=d.strftime("%Y-%m-%d"))
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()

    ratings: dict[str, float] = {}
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        try:
            ratings[row["Club"]] = float(row["Elo"])
        except (KeyError, ValueError):
            continue
    return ratings


def _normalise_club_name(name: str) -> str:
    """Lower-case, strip punctuation for fuzzy matching."""
    return name.lower().replace(".", "").replace("-", " ").replace("'", "").strip()


def _best_match(team: str, elo_lookup: dict[str, float]) -> float | None:
    """Find the best matching Elo from the clubelo dict using fuzzy name matching."""
    norm = _normalise_club_name(team)
    # Exact
    for k, v in elo_lookup.items():
        if _normalise_club_name(k) == norm:
            return v
    # Substring — take the first that contains our name or vice-versa
    for k, v in elo_lookup.items():
        kn = _normalise_club_name(k)
        if norm in kn or kn in norm:
            return v
    return None


def collect_club_elo(as_of: date | None = None) -> int:
    """
    Fetch clubelo ratings and upsert into the elo_ratings table for all
    teams we have in our DB across club competitions.
    Returns the count of rows written.
    """
    d = as_of or date.today()
    log.info("Fetching club ELO from clubelo.com for %s…", d)
    try:
        elo_lookup = fetch_club_elo(d)
    except Exception as e:
        log.error("clubelo.com fetch failed: %s", e)
        return 0

    written = 0
    with SessionLocal() as db:
        # Get unique teams per competition from our match history
        for comp in COMP_COUNTRY_LEVEL:
            teams: set[str] = set()
            for m in db.query(Match).filter(Match.competition == comp).all():
                teams.add(m.home_team)
                teams.add(m.away_team)

            for team in teams:
                elo = _best_match(team, elo_lookup)
                if elo is None:
                    log.debug("No ELO found for %s (%s)", team, comp)
                    continue

                # Upsert: delete old record and insert fresh one
                db.query(EloRating).filter(
                    EloRating.team == team,
                    EloRating.competition == comp,
                ).delete()
                db.add(EloRating(
                    team=team,
                    competition=comp,
                    elo=elo,
                    as_of_date=datetime.combine(d, datetime.min.time()),
                    source="clubelo",
                ))
                written += 1

        db.commit()

    log.info("Club ELO: wrote %d records", written)
    return written


# ---------------------------------------------------------------------------
# International ELO computed from our own match history
# ---------------------------------------------------------------------------

def compute_international_elo() -> int:
    """
    Walk through all WC/EC finished matches chronologically and compute
    running Elo ratings for each national team. Writes only the final
    (most-recent) rating per team to elo_ratings.
    """
    with SessionLocal() as db:
        matches = (
            db.query(Match)
            .filter(
                Match.competition.in_(INTL_COMPETITIONS),
                Match.status == "FINISHED",
                Match.result != None,
            )
            .order_by(Match.match_date)
            .all()
        )

        ratings: dict[str, float] = {}
        last_seen: dict[str, datetime] = {}

        for m in matches:
            ht, at = m.home_team, m.away_team
            rh = ratings.get(ht, DEFAULT_ELO)
            ra = ratings.get(at, DEFAULT_ELO)

            # Neutral venue — no home bonus
            eh = _expected(rh, ra)
            ea = _expected(ra, rh)

            if m.result == "H":
                sh, sa = 1.0, 0.0
            elif m.result == "A":
                sh, sa = 0.0, 1.0
            else:
                sh, sa = 0.5, 0.5

            ratings[ht] = _update(rh, eh, sh)
            ratings[at] = _update(ra, ea, sa)
            last_seen[ht] = m.match_date
            last_seen[at] = m.match_date

        if not ratings:
            log.warning("No finished international matches found; skipping ELO computation")
            return 0

        # Write final ratings
        now = datetime.utcnow()
        for team, elo in ratings.items():
            db.query(EloRating).filter(
                EloRating.team == team,
                EloRating.competition == None,
            ).delete()
            db.add(EloRating(
                team=team,
                competition=None,
                elo=round(elo, 1),
                as_of_date=last_seen.get(team, now),
                source="computed",
            ))

        db.commit()

    log.info("International ELO: wrote %d records", len(ratings))
    return len(ratings)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)

    n_club  = collect_club_elo()
    n_intl  = compute_international_elo()
    print(f"ELO ETL complete: {n_club} club ratings, {n_intl} international ratings written.")
