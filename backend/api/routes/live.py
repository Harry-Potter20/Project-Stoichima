"""
GET /api/v1/live              — all currently in-play matches with latest snapshot
GET /api/v1/live/{match_id}   — detailed state + 1-min snapshot history
GET /api/v1/live/{match_id}/events — event timeline (goals, cards, subs)

Snapshot history is what drives the probability sparkline on the live tab.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import LiveMatchState, LiveMatchEvent, LivePredictionSnapshot, Match

router = APIRouter()

IN_PLAY_STATUSES = {"IN_PLAY", "HT", "IN_PLAY_2H", "AET", "PEN", "SUSPENDED"}


def _serialise_state(state: LiveMatchState, latest_snap: LivePredictionSnapshot | None) -> dict:
    out = {
        "match_id":     state.match_id,
        "competition":  state.competition,
        "home_team":    state.home_team,
        "away_team":    state.away_team,
        "kickoff_at":   state.kickoff_at.isoformat() if state.kickoff_at else None,
        "status":       state.status,
        "minute":       state.minute,
        "score":        {"home": state.home_score, "away": state.away_score},
        "red_cards":    {"home": state.home_red_cards, "away": state.away_red_cards},
        "yellow_cards": {"home": state.home_yellow_cards, "away": state.away_yellow_cards},
        "prematch": {
            "lambda_home": state.prematch_lambda_home,
            "lambda_away": state.prematch_lambda_away,
        },
        "last_polled_at": state.last_polled_at.isoformat() if state.last_polled_at else None,
    }
    if latest_snap:
        out["live_prediction"] = {
            "minute":        latest_snap.minute,
            "home_win_prob": latest_snap.home_win_prob,
            "draw_prob":     latest_snap.draw_prob,
            "away_win_prob": latest_snap.away_win_prob,
            "over_2_5_prob": latest_snap.over_2_5_prob,
            "btts_prob":     latest_snap.btts_prob,
            "expected_total_goals": latest_snap.expected_total_goals,
        }
    return out


@router.get("/live")
def list_live_matches(db: Session = Depends(get_db)):
    """All currently in-play matches with their latest live prediction."""
    states = (
        db.query(LiveMatchState)
        .filter(LiveMatchState.status.in_(list(IN_PLAY_STATUSES)))
        .order_by(LiveMatchState.kickoff_at)
        .all()
    )
    out = []
    for s in states:
        latest = (
            db.query(LivePredictionSnapshot)
            .filter(LivePredictionSnapshot.match_id == s.match_id)
            .order_by(LivePredictionSnapshot.minute.desc())
            .first()
        )
        out.append(_serialise_state(s, latest))
    return {"in_play": len(out), "matches": out}


@router.get("/live/{match_id}")
def get_live_match(match_id: int, db: Session = Depends(get_db)):
    """
    Detailed live state + full prediction history (one row per minute).
    The history powers the probability sparkline on the live tab.
    """
    state = db.query(LiveMatchState).filter(LiveMatchState.match_id == match_id).first()
    if not state:
        raise HTTPException(404, f"no live state for match {match_id}")

    snaps = (
        db.query(LivePredictionSnapshot)
        .filter(LivePredictionSnapshot.match_id == match_id)
        .order_by(LivePredictionSnapshot.minute)
        .all()
    )
    history = [
        {
            "minute":        s.minute,
            "home_win_prob": s.home_win_prob,
            "draw_prob":     s.draw_prob,
            "away_win_prob": s.away_win_prob,
            "over_2_5_prob": s.over_2_5_prob,
            "btts_prob":     s.btts_prob,
            "expected_total_goals": s.expected_total_goals,
        }
        for s in snaps
    ]
    latest = snaps[-1] if snaps else None
    payload = _serialise_state(state, latest)
    payload["history"] = history
    return payload


@router.get("/live/{match_id}/events")
def get_live_events(match_id: int, db: Session = Depends(get_db)):
    """Timeline of in-play events for a match (goals, cards, subs, VAR)."""
    events = (
        db.query(LiveMatchEvent)
        .filter(LiveMatchEvent.match_id == match_id)
        .order_by(LiveMatchEvent.minute, LiveMatchEvent.id)
        .all()
    )
    return {
        "match_id": match_id,
        "n_events": len(events),
        "events": [
            {
                "minute":   e.minute,
                "type":     e.type,
                "team":     e.team,
                "player":   e.player,
                "detail":   e.detail,
            }
            for e in events
        ],
    }
