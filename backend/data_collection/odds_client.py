"""
Bookmaker odds ETL using The Odds API (https://the-odds-api.com).

Free tier: 500 requests/month.
Each call to fetch_competition_odds() costs 1 request.

Fetches best available H2H (1X2) odds for upcoming matches and writes
to the odds_snapshots table. Also returns the model-vs-market edge
for use in the predictions API response.

Set ODDS_API_KEY in .env to enable. Without a key, all functions
return gracefully with empty results.

Run standalone:
    python3 -c "from data_collection.odds_client import run; run()"
"""

import logging
from datetime import datetime

import requests

from app.config import get_settings
from app.database import SessionLocal
from app.models import OddsSnapshot, Match

log = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

# Map our competition IDs to The Odds API sport keys
SPORT_KEY_MAP = {
    "PL":  "soccer_epl",
    "PD":  "soccer_spain_la_liga",
    "BL1": "soccer_germany_bundesliga",
    "SA":  "soccer_italy_serie_a",
    "FL1": "soccer_france_ligue_one",
    "WC":  "soccer_fifa_world_cup",
    "CL":  "soccer_uefa_champs_league",
}

REGIONS   = "eu"       # European bookmakers give best coverage
MARKETS   = "h2h"      # 1X2 match result
ODDS_FORMAT = "decimal"


def _get_api_key() -> str:
    return get_settings().odds_api_key


def fetch_competition_odds(competition_id: str) -> list[dict]:
    """
    Fetch H2H odds for all upcoming matches in a competition.
    Returns a list of raw event dicts from The Odds API.
    Returns [] when no API key is configured or on any error.
    """
    key = _get_api_key()
    if not key:
        log.debug("ODDS_API_KEY not set — skipping odds fetch for %s", competition_id)
        return []

    sport_key = SPORT_KEY_MAP.get(competition_id)
    if not sport_key:
        return []

    try:
        resp = requests.get(
            BASE_URL.format(sport_key=sport_key),
            params={
                "apiKey":     key,
                "regions":    REGIONS,
                "markets":    MARKETS,
                "oddsFormat": ODDS_FORMAT,
                "dateFormat": "iso",
            },
            timeout=15,
        )
        remaining = resp.headers.get("x-requests-remaining", "?")
        log.info("Odds API %s — HTTP %s, %s requests remaining", competition_id, resp.status_code, remaining)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("Odds fetch failed for %s: %s", competition_id, e)
        return []


def _best_odds(event: dict) -> tuple[float | None, float | None, float | None, str | None, str | None, str | None]:
    """
    Extract the best available H/D/A decimal odds INDEPENDENTLY per outcome
    across all bookmakers — true best-price shopping.

    Returns (home_odds, draw_odds, away_odds, bk_home, bk_draw, bk_away).
    """
    best_h = best_d = best_a = None
    bk_h = bk_d = bk_a = None

    for bk in event.get("bookmakers", []):
        for market in bk.get("markets", []):
            if market.get("key") != "h2h":
                continue
            prices = {o["name"]: o["price"] for o in market.get("outcomes", [])}
            h = prices.get(event.get("home_team"))
            a = prices.get(event.get("away_team"))
            d = prices.get("Draw")
            if d is None:
                names = set(prices) - {event.get("home_team"), event.get("away_team")}
                if names:
                    d = prices.get(next(iter(names)))

            if h and (best_h is None or h > best_h):
                best_h, bk_h = h, bk["title"]
            if d and (best_d is None or d > best_d):
                best_d, bk_d = d, bk["title"]
            if a and (best_a is None or a > best_a):
                best_a, bk_a = a, bk["title"]

    return best_h, best_d, best_a, bk_h, bk_d, bk_a


def detect_arb(event: dict) -> dict | None:
    """
    Check if best-available odds across bookmakers constitute an arbitrage.
    Returns arb details (margin %) if found, else None.
    Arb exists when 1/H + 1/D + 1/A < 1.0.
    """
    best_h, best_d, best_a, bk_h, bk_d, bk_a = _best_odds(event)
    if not (best_h and best_d and best_a):
        return None
    implied = 1 / best_h + 1 / best_d + 1 / best_a
    if implied >= 1.0:
        return None
    margin = round((1.0 - implied) * 100, 3)
    # Optimal stakes for each leg (Kelly-arb: stake ∝ 1/(odds × implied))
    total = implied
    return {
        "home_team":    event.get("home_team"),
        "away_team":    event.get("away_team"),
        "match_date":   event.get("commence_time", ""),
        "home_odds":    best_h, "bk_home": bk_h,
        "draw_odds":    best_d, "bk_draw": bk_d,
        "away_odds":    best_a, "bk_away": bk_a,
        "arb_margin":   margin,
        "implied_total": round(total, 4),
        # Optimal stake splits (% of bankroll per leg)
        "stake_home":   round(100 / (best_h * total), 2),
        "stake_draw":   round(100 / (best_d * total), 2),
        "stake_away":   round(100 / (best_a * total), 2),
    }


def _remove_vig(h: float, d: float, a: float) -> tuple[float, float, float]:
    """Convert decimal odds to vig-removed implied probabilities."""
    if not (h and d and a):
        return (1/3, 1/3, 1/3)
    raw = [1/h, 1/d, 1/a]
    total = sum(raw)
    return tuple(v / total for v in raw)


def collect_odds(competition_id: str) -> int:
    """Fetch and store odds for all upcoming matches. Returns rows written."""
    events = fetch_competition_odds(competition_id)
    if not events:
        return 0

    written = 0
    with SessionLocal() as db:
        # Build a quick lookup: (home_team_norm, away_team_norm) → match_id
        upcoming = (
            db.query(Match)
            .filter(Match.competition == competition_id, Match.status.in_(["SCHEDULED", "TIMED"]))
            .all()
        )
        match_lookup = {
            (_norm(m.home_team), _norm(m.away_team)): m.id for m in upcoming
        }

        now = datetime.utcnow()
        arb_found = []
        for event in events:
            # Arb check first (uses same per-bookmaker scan)
            arb = detect_arb(event)
            if arb:
                arb_found.append(arb)
                log.info("ARB found: %s vs %s margin=%.2f%%", arb["home_team"], arb["away_team"], arb["arb_margin"])

            h_odds, d_odds, a_odds, bk_h, bk_d, bk_a = _best_odds(event)
            bk = bk_h  # primary attribution
            if not (h_odds and d_odds and a_odds):
                continue

            h_impl, d_impl, a_impl = _remove_vig(h_odds, d_odds, a_odds)

            home = event.get("home_team", "")
            away = event.get("away_team", "")
            match_id = match_lookup.get((_norm(home), _norm(away)))

            try:
                commence = datetime.fromisoformat(
                    event["commence_time"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                commence = now

            # Upsert — delete previous snapshot for this match if any
            if match_id:
                db.query(OddsSnapshot).filter(OddsSnapshot.match_id == match_id).delete()
            else:
                db.query(OddsSnapshot).filter(
                    OddsSnapshot.competition == competition_id,
                    OddsSnapshot.home_team == home,
                    OddsSnapshot.away_team == away,
                ).delete()

            db.add(OddsSnapshot(
                match_id=match_id,
                competition=competition_id,
                home_team=home,
                away_team=away,
                match_date=commence,
                home_odds=round(h_odds, 3),
                draw_odds=round(d_odds, 3),
                away_odds=round(a_odds, 3),
                home_implied=round(h_impl, 4),
                draw_implied=round(d_impl, 4),
                away_implied=round(a_impl, 4),
                bookmaker=bk,
                fetched_at=now,
            ))
            written += 1

        db.commit()

    log.info("Odds: wrote %d snapshots for %s", written, competition_id)
    return written


def _norm(name: str) -> str:
    """Aggressively normalise team name for fuzzy matching."""
    import re
    n = name.lower().strip()
    # Remove common suffixes / articles
    n = re.sub(r'\b(fc|cf|sc|ac|as|afc|rcd|ud|cd|sd|bv|sv|tz)\b', '', n)
    # Common country-name variants
    replacements = {
        "czech republic": "czechia",
        "czech rep": "czechia",
        "bosnia and herzegovina": "bosnia-herzegovina",
        "bosnia & herzegovina": "bosnia-herzegovina",
        "ivory coast": "ivory coast",
        "cote d'ivoire": "ivory coast",
        "côte d'ivoire": "ivory coast",
        "usa": "united states",
        "united states of america": "united states",
        "korea republic": "south korea",
        "republic of korea": "south korea",
        "iran": "iran",
        "ir iran": "iran",
        "paris saint germain": "paris saint-germain",
        "paris sg": "paris saint-germain",
        "psg": "paris saint-germain",
        "saint etienne": "saint-étienne",
        "st etienne": "saint-étienne",
    }
    for src, dst in replacements.items():
        n = n.replace(src, dst)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def _fuzzy_match(query: str, candidates: set[str]) -> str | None:
    """Return the best matching candidate for query, or None."""
    q = _norm(query)
    # Exact
    if q in candidates:
        return q
    # One contains the other
    for c in candidates:
        if q in c or c in q:
            return c
    return None


def scan_arb(competition_ids: list[str] | None = None) -> list[dict]:
    """Scan all competitions for live arbitrage opportunities. Returns list of arb dicts."""
    targets = competition_ids or list(SPORT_KEY_MAP.keys())
    arbs = []
    for comp in targets:
        events = fetch_competition_odds(comp)
        for event in events:
            arb = detect_arb(event)
            if arb:
                arb["competition"] = comp
                arbs.append(arb)
    return sorted(arbs, key=lambda x: x["arb_margin"], reverse=True)


def fetch_closing_odds_for_upcoming(competition_id: str, within_minutes: int = 90) -> int:
    """
    Fetch current odds for matches kicking off within `within_minutes` and store
    them as closing odds in BetLog. Called by the scheduler close to kickoff.
    Returns number of BetLog rows updated.
    """
    events = fetch_competition_odds(competition_id)
    if not events:
        return 0

    now = datetime.utcnow()
    updated = 0
    with SessionLocal() as db:
        from app.models import BetLog
        open_bets = db.query(BetLog).filter(BetLog.actual_result.is_(None),
                                             BetLog.competition == competition_id).all()
        bet_map = {(b.home_team.lower(), b.away_team.lower()): b for b in open_bets}

        for event in events:
            try:
                kickoff = datetime.fromisoformat(
                    event["commence_time"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                continue
            minutes_to_go = (kickoff - now).total_seconds() / 60
            if not (0 <= minutes_to_go <= within_minutes):
                continue

            h_odds, d_odds, a_odds, *_ = _best_odds(event)
            if not (h_odds and d_odds and a_odds):
                continue

            key = (event.get("home_team", "").lower(), event.get("away_team", "").lower())
            bet = bet_map.get(key)
            if bet and not bet.closing_home_odds:
                bet.closing_home_odds = h_odds
                bet.closing_draw_odds = d_odds
                bet.closing_away_odds = a_odds
                updated += 1

        if updated:
            db.commit()
    return updated


def get_odds_map(competition_id: str) -> dict[tuple[str, str], dict]:
    """
    Return {(home_team_norm, away_team_norm): {odds + implied}} from the DB snapshot.
    Uses aggressive normalisation so team name variants between APIs still match.
    """
    with SessionLocal() as db:
        rows = db.query(OddsSnapshot).filter(
            OddsSnapshot.competition == competition_id
        ).all()

    result = {}
    for r in rows:
        key = (_norm(r.home_team), _norm(r.away_team))
        result[key] = {
            "home_odds":    r.home_odds,
            "draw_odds":    r.draw_odds,
            "away_odds":    r.away_odds,
            "home_implied": r.home_implied,
            "draw_implied": r.draw_implied,
            "away_implied": r.away_implied,
            "bookmaker":    r.bookmaker,
            "fetched_at":   r.fetched_at.isoformat() if r.fetched_at else None,
        }
    return result


def attach_odds(predictions: list[dict], competition_id: str) -> None:
    """
    Mutate each prediction dict in-place to add 'odds', 'edge', and 'kelly' keys.
    Safe to call even when no odds are available — adds nothing on miss.
    """
    try:
        odds_map = get_odds_map(competition_id)
        if not odds_map:
            return

        all_keys = set(odds_map.keys())
        home_index = {}   # norm_home → list of (norm_home, norm_away) keys
        for h, a in all_keys:
            home_index.setdefault(h, []).append((h, a))

        for pred in predictions:
            hn = _norm(pred.get("home_team", ""))
            an = _norm(pred.get("away_team", ""))

            # Try exact match first
            snap = odds_map.get((hn, an))

            # Fuzzy fallback
            if snap is None:
                bh = _fuzzy_match(hn, {k[0] for k in all_keys})
                ba = _fuzzy_match(an, {k[1] for k in all_keys})
                if bh and ba:
                    snap = odds_map.get((bh, ba))

            if not snap:
                continue

            pred["odds"] = {
                "home": snap["home_odds"],
                "draw": snap["draw_odds"],
                "away": snap["away_odds"],
                "bookmaker": snap["bookmaker"],
                "fetched_at": snap["fetched_at"],
            }

            outcome = pred.get("outcome", pred)  # tournament route nests under "outcome"
            h_prob = outcome.get("home_win_prob", pred.get("home_win_prob", 0))
            d_prob = outcome.get("draw_prob",     pred.get("draw_prob",     0))
            a_prob = outcome.get("away_win_prob", pred.get("away_win_prob", 0))

            pred["edge"] = {
                "home": round((h_prob - snap["home_implied"]) * 100, 1),
                "draw": round((d_prob - snap["draw_implied"]) * 100, 1),
                "away": round((a_prob - snap["away_implied"]) * 100, 1),
            }

            def _kelly(prob, decimal_odds):
                if not decimal_odds or decimal_odds <= 1:
                    return 0.0
                b = decimal_odds - 1
                return round(max(0.0, (prob * b - (1 - prob)) / b) * 100, 1)

            pred["kelly"] = {
                "home": _kelly(h_prob, snap["home_odds"]),
                "draw": _kelly(d_prob, snap["draw_odds"]),
                "away": _kelly(a_prob, snap["away_odds"]),
            }
    except Exception as e:
        log.warning("attach_odds failed: %s", e)


def run(competitions: list[str] | None = None):
    """Collect odds for all (or specified) competitions."""
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)

    targets = competitions or list(SPORT_KEY_MAP.keys())
    total = 0
    for comp in targets:
        n = collect_odds(comp)
        print(f"  {comp}: {n} snapshots")
        total += n
    print(f"Odds ETL complete: {total} total snapshots.")
