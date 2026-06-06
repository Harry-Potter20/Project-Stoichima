"""
Live odds polling — fetches in-play odds from The Odds API and compares them
against the live model probability stored in LivePredictionSnapshot. Edges
exceeding the live threshold are auto-logged to BetLog with is_inplay flag.

The Odds API is paginated by sport_key (PL, PD, etc.), so we iterate over all
competitions tracked by SPORT_KEY_MAP. Free tier limits are tight (500/mo) so
we only poll matches we have a LiveMatchState for (i.e., genuinely in-play
matches our predictor is also tracking).
"""
from __future__ import annotations
import logging
from datetime import datetime
import requests

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    LiveMatchState, LivePredictionSnapshot, BetLog, Match,
)
from data_collection.odds_client import SPORT_KEY_MAP, BASE_URL, _best_odds
from data_processing.team_normalizer import normalise_team_name
from utils.betting import kelly_fraction_stake

log = logging.getLogger(__name__)

LIVE_EDGE_THRESHOLD   = 5.0    # in-play edges noisier than pre-match, require higher floor
LIVE_KELLY_FRACTION   = 0.15   # smaller than pre-match (0.25) — variance is higher
LIVE_MIN_MODEL_PROB   = 0.30


def _fetch_live_odds(sport_key: str, key: str) -> list[dict]:
    try:
        resp = requests.get(
            BASE_URL.format(sport_key=sport_key),
            params={
                "apiKey":     key,
                "regions":    "eu",
                "markets":    "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("Live odds fetch failed for %s: %s", sport_key, e)
        return []


def _match_event_to_state(db, event: dict) -> LiveMatchState | None:
    """Find the LiveMatchState row matching this odds event."""
    h = normalise_team_name(event.get("home_team", ""))
    a = normalise_team_name(event.get("away_team", ""))
    return (
        db.query(LiveMatchState)
        .filter(
            LiveMatchState.home_team == h,
            LiveMatchState.away_team == a,
            LiveMatchState.status.in_(["IN_PLAY", "HT", "IN_PLAY_2H"]),
        )
        .first()
    )


def _log_inplay_bet(db, state: LiveMatchState, side: str, odds: float, model_prob: float, edge: float) -> None:
    """Insert a BetLog row tagged as in-play. Idempotent via exists-check."""
    existing = db.query(BetLog).filter(
        BetLog.home_team    == state.home_team,
        BetLog.away_team    == state.away_team,
        BetLog.match_date   == state.kickoff_at,
        BetLog.bet_on       == side,
        BetLog.tags.like("%inplay%"),
    ).first()
    if existing:
        return

    kelly = kelly_fraction_stake(model_prob, odds, LIVE_KELLY_FRACTION)
    db.add(BetLog(
        competition  = state.competition,
        home_team    = state.home_team,
        away_team    = state.away_team,
        match_date   = state.kickoff_at,
        bet_on       = side,
        model_prob   = round(model_prob, 4),
        implied_prob = round(1.0 / odds, 4),
        edge_pct     = round(edge, 2),
        decimal_odds = round(odds, 3),
        kelly_pct    = kelly,
        bookmaker    = "live",
        tags         = "inplay",
    ))


def run_live_odds_poll() -> dict:
    s = get_settings()
    if not s.odds_api_key:
        return {"ok": False, "reason": "no_odds_api_key"}

    flagged = 0
    sports_polled = 0
    with SessionLocal() as db:
        # Only poll sports where we have a tracked live match — saves quota
        active_comps = {
            row.competition for row in db.query(LiveMatchState).filter(
                LiveMatchState.status.in_(["IN_PLAY", "HT", "IN_PLAY_2H"])
            ).all()
        }
        if not active_comps:
            return {"ok": True, "reason": "no_live_matches"}

        for comp in active_comps:
            sport_key = SPORT_KEY_MAP.get(comp)
            if not sport_key:
                continue
            events = _fetch_live_odds(sport_key, s.odds_api_key)
            sports_polled += 1

            for event in events:
                state = _match_event_to_state(db, event)
                if not state:
                    continue
                h_odds, d_odds, a_odds, bk_h, bk_d, bk_a = _best_odds(event)
                if not (h_odds and d_odds and a_odds):
                    continue

                # Latest live model snapshot
                snap = (
                    db.query(LivePredictionSnapshot)
                    .filter(LivePredictionSnapshot.match_id == state.match_id)
                    .order_by(LivePredictionSnapshot.minute.desc())
                    .first()
                )
                if not snap:
                    continue

                # Persist live odds onto the snapshot (for CLV trajectory)
                snap.book_home_odds = h_odds
                snap.book_draw_odds = d_odds
                snap.book_away_odds = a_odds
                snap.bookmaker      = (bk_h or bk_d or bk_a or "")

                # Compute edge per outcome — log only the most attractive one
                outcomes = [
                    ("H", snap.home_win_prob, h_odds),
                    ("D", snap.draw_prob,     d_odds),
                    ("A", snap.away_win_prob, a_odds),
                ]
                best = max(outcomes, key=lambda o: o[1] - 1.0 / o[2])
                side, model_prob, odds = best
                if not model_prob or model_prob < LIVE_MIN_MODEL_PROB:
                    continue
                implied = 1.0 / odds
                edge = (model_prob - implied) * 100
                if edge < LIVE_EDGE_THRESHOLD:
                    continue

                _log_inplay_bet(db, state, side, odds, model_prob, edge)
                flagged += 1
                log.info("In-play edge: %s v %s | %s @%.2f | model %.2f vs market %.2f | edge +%.1fpp",
                         state.home_team, state.away_team, side, odds, model_prob, implied, edge)
        db.commit()

    return {"ok": True, "sports_polled": sports_polled, "edges_logged": flagged}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_live_odds_poll())
