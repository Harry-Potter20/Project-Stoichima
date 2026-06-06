"""
Live match state ingest — polls API-Football's /fixtures?live=all endpoint
during matchday hours, upserts LiveMatchState + LiveMatchEvent rows, and
runs the live Poisson predictor to write LivePredictionSnapshot per minute.

Wire-up in scheduler.py:
    from data_collection.live_match_ingest import run_live_poll
    scheduler.add_job(run_live_poll, trigger=CronTrigger(minute="*"),
                      id="live_match_poll", ...)

The poller is idempotent — running it twice in a row is safe; existing rows
are merged on (match_id, minute, type, api_event_id).

API-Football status codes we recognise (status_short):
    NS  = Not Started
    1H  = First half in play
    HT  = Halftime
    2H  = Second half in play
    ET  = Extra time
    BT  = Break time
    P   = Penalty shootout
    FT  = Full time
    AET = After extra time
    PEN = Penalty shootout finished
    SUSP = Suspended
"""
from __future__ import annotations
import logging
from datetime import datetime
import requests

from app.config import get_settings
from app.database import SessionLocal
from app.models import Match, LiveMatchState, LiveMatchEvent, LivePredictionSnapshot
from prediction.live_predictor import predict_live
from prediction.predictor import get_predictor
from models.goals_distribution_model import GoalsDistributionModel
from data_processing.team_normalizer import normalise_team_name

log = logging.getLogger(__name__)

BASE_URL = "https://v3.football.api-sports.io"
INTL_COMPS = {
    "WC", "EC", "UNL", "AFC", "ASIA", "CA", "GOLD",
    "FRIENDLY",
    "WCQ_EU", "WCQ_AF", "WCQ_AS", "WCQ_SA", "WCQ_CC", "WCQ_OC",
}

# API-Football status_short → our internal status
STATUS_MAP = {
    "NS":   "NOT_STARTED",
    "1H":   "IN_PLAY",
    "HT":   "HT",
    "2H":   "IN_PLAY_2H",
    "ET":   "AET",
    "BT":   "AET",
    "P":    "PEN",
    "FT":   "FT",
    "AET":  "AET",
    "PEN":  "PEN",
    "SUSP": "SUSPENDED",
}


def _http(endpoint: str, params: dict, key: str) -> dict | None:
    try:
        r = requests.get(
            f"{BASE_URL}{endpoint}",
            params=params,
            headers={"x-apisports-key": key},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning("API-Football %s failed: %s", endpoint, exc)
        return None


def _pre_match_lambdas(home: str, away: str, competition: str) -> tuple[float, float] | None:
    """
    Get pre-match Poisson lambdas from the appropriate DC model.
    Returns (λ_home, λ_away) or None if no DC model loaded.
    """
    try:
        predictor = get_predictor()
        if competition in INTL_COMPS and predictor.dist_model_intl is not None:
            dc = predictor.dist_model_intl
        elif predictor.dist_model is not None:
            dc = predictor.dist_model
        else:
            return None
        neutral = competition in INTL_COMPS
        return dc.get_lambdas(home, away, neutral_venue=neutral)
    except Exception as exc:
        log.warning("get_lambdas failed for %s vs %s: %s", home, away, exc)
        return None


def _upsert_state(db, fix: dict) -> LiveMatchState | None:
    """Upsert LiveMatchState from an API-Football fixture payload."""
    fixture = fix.get("fixture", {})
    teams   = fix.get("teams", {})
    goals   = fix.get("goals", {})
    status  = STATUS_MAP.get(fixture.get("status", {}).get("short"), "IN_PLAY")
    minute  = fixture.get("status", {}).get("elapsed") or 0
    af_id   = fixture.get("id")

    home_team = normalise_team_name(teams.get("home", {}).get("name") or "")
    away_team = normalise_team_name(teams.get("away", {}).get("name") or "")
    if not home_team or not away_team:
        return None

    # Look up our internal Match row by api_football_id (or fall back to team+date match)
    match = db.query(Match).filter(Match.api_football_id == af_id).first()
    if not match:
        try:
            ko_str = fixture.get("date")
            if ko_str:
                ko = datetime.fromisoformat(ko_str.replace("Z", "+00:00")).replace(tzinfo=None)
                match = (
                    db.query(Match)
                    .filter(
                        Match.home_team == home_team,
                        Match.away_team == away_team,
                        func_date_equal := Match.match_date == ko,
                    )
                    .first()
                )
        except Exception:
            pass
    if not match:
        log.debug("No internal Match row for AF fixture %s (%s v %s)", af_id, home_team, away_team)
        return None

    state = db.query(LiveMatchState).filter(LiveMatchState.match_id == match.id).first()
    if not state:
        # First time seeing this fixture live — freeze pre-match lambdas now
        lam = _pre_match_lambdas(home_team, away_team, match.competition)
        state = LiveMatchState(
            match_id=match.id,
            competition=match.competition,
            home_team=home_team,
            away_team=away_team,
            kickoff_at=match.match_date,
            prematch_lambda_home=lam[0] if lam else None,
            prematch_lambda_away=lam[1] if lam else None,
        )
        db.add(state)

    state.status         = status
    state.minute         = minute
    state.home_score     = goals.get("home") or 0
    state.away_score     = goals.get("away") or 0
    state.last_polled_at = datetime.utcnow()
    return state


def _send_event_telegram(state: LiveMatchState, event_type: str, minute: int, team: str | None, player: str | None) -> None:
    """Fire-and-forget Telegram push for noteworthy live events."""
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return
        # Only push the events fans actually care about
        push_set = {"goal", "own_goal", "penalty_goal", "penalty_missed", "red"}
        if event_type not in push_set:
            return

        emoji = {
            "goal":            "⚽",
            "own_goal":        "⚽ (OG)",
            "penalty_goal":    "⚽ (P)",
            "penalty_missed":  "🚫 (P)",
            "red":             "🟥",
        }.get(event_type, "•")

        # Latest live prediction for this match — surface the new probability state
        from app.database import SessionLocal
        from app.models import LivePredictionSnapshot
        with SessionLocal() as db2:
            latest = (
                db2.query(LivePredictionSnapshot)
                .filter(LivePredictionSnapshot.match_id == state.match_id)
                .order_by(LivePredictionSnapshot.minute.desc())
                .first()
            )

        score_line = f"{state.home_team} {state.home_score} – {state.away_score} {state.away_team}"
        who = f"{player}" if player else (team or "?")
        msg = (
            f"{emoji} *{minute}'* — {who}\n"
            f"⚽ {score_line}\n"
            f"[{state.competition}]"
        )
        if latest:
            h = (latest.home_win_prob or 0) * 100
            d = (latest.draw_prob or 0) * 100
            a = (latest.away_win_prob or 0) * 100
            msg += f"\nH {h:.0f}% · D {d:.0f}% · A {a:.0f}%"

        import urllib.request, json as _json
        url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
        payload = _json.dumps({
            "chat_id":    s.telegram_chat_id,
            "text":       msg,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        log.warning("event telegram failed: %s", exc)


def _record_events(db, state: LiveMatchState, fix: dict) -> int:
    """Append new events from the fixture payload. Dedup by api_event_id."""
    events = fix.get("events", []) or []
    if not events:
        return 0
    # Existing api_event_ids for this match
    existing = {
        row.api_event_id
        for row in db.query(LiveMatchEvent.api_event_id).filter(
            LiveMatchEvent.match_id == state.match_id
        ).all()
        if row.api_event_id
    }
    added = 0
    new_event_pushes: list[tuple[str, int, str | None, str | None]] = []
    for ev in events:
        # API-Football events have no stable id; synthesise from (minute,type,player)
        minute  = (ev.get("time") or {}).get("elapsed") or 0
        ev_type = (ev.get("type") or "").lower()
        detail  = ev.get("detail") or ""
        player  = (ev.get("player") or {}).get("name")
        team    = normalise_team_name((ev.get("team") or {}).get("name") or "")

        type_normalised = _normalise_event_type(ev_type, detail)
        api_event_id = f"{minute}|{type_normalised}|{player or '?'}|{team or '?'}"
        if api_event_id in existing:
            continue

        db.add(LiveMatchEvent(
            match_id=state.match_id,
            minute=minute,
            type=type_normalised,
            team=team or None,
            player=player,
            detail={"raw_type": ev_type, "raw_detail": detail},
            api_event_id=api_event_id,
        ))
        added += 1

        # Update red/yellow counts on state
        if type_normalised == "red":
            if team == state.home_team:
                state.home_red_cards = (state.home_red_cards or 0) + 1
            elif team == state.away_team:
                state.away_red_cards = (state.away_red_cards or 0) + 1
        elif type_normalised == "yellow":
            if team == state.home_team:
                state.home_yellow_cards = (state.home_yellow_cards or 0) + 1
            elif team == state.away_team:
                state.away_yellow_cards = (state.away_yellow_cards or 0) + 1

        if type_normalised in ("goal", "own_goal", "penalty_goal"):
            state.last_event_at = datetime.utcnow()

        # Queue noteworthy events for Telegram push after we commit
        if type_normalised in ("goal", "own_goal", "penalty_goal", "penalty_missed", "red"):
            new_event_pushes.append((type_normalised, minute, team, player))

    # Stash on the state object so the caller can fire pushes AFTER the new
    # snapshot is written — that way the message reflects updated probabilities.
    state._pending_pushes = new_event_pushes
    return added


def _normalise_event_type(raw_type: str, detail: str) -> str:
    detail_l = (detail or "").lower()
    if raw_type == "goal":
        if "own goal" in detail_l: return "own_goal"
        if "penalty" in detail_l:  return "penalty_goal"
        return "goal"
    if raw_type == "card":
        if "red"    in detail_l: return "red"
        if "yellow" in detail_l: return "yellow"
        return raw_type
    if raw_type == "subst": return "sub"
    if raw_type == "var":   return "var_check"
    return raw_type or "unknown"


def _write_snapshot(db, state: LiveMatchState) -> None:
    """Run live predictor + write LivePredictionSnapshot row for this minute."""
    if state.prematch_lambda_home is None or state.prematch_lambda_away is None:
        return
    # Skip if we already wrote a snapshot at this minute (idempotency)
    existing = db.query(LivePredictionSnapshot).filter(
        LivePredictionSnapshot.match_id == state.match_id,
        LivePredictionSnapshot.minute   == state.minute,
    ).first()
    if existing:
        return

    snap = predict_live(
        prematch_lambda_home=state.prematch_lambda_home,
        prematch_lambda_away=state.prematch_lambda_away,
        minute=state.minute,
        home_score=state.home_score,
        away_score=state.away_score,
        status=state.status,
        home_red_cards=state.home_red_cards or 0,
        away_red_cards=state.away_red_cards or 0,
    )
    db.add(LivePredictionSnapshot(
        match_id=state.match_id,
        minute=state.minute,
        home_win_prob=snap.home_win_prob,
        draw_prob=snap.draw_prob,
        away_win_prob=snap.away_win_prob,
        over_2_5_prob=snap.over_2_5_prob,
        btts_prob=snap.btts_prob,
        expected_total_goals=snap.expected_total_goals,
    ))


def run_live_poll() -> dict:
    """
    One poll cycle. Fetches /fixtures?live=all, upserts state, records events,
    writes live prediction snapshots. Returns a small summary dict for logs.
    """
    s = get_settings()
    if not s.api_football_key:
        log.debug("Live poll skipped — API_FOOTBALL_KEY not set")
        return {"ok": False, "reason": "no_api_key"}

    payload = _http("/fixtures", {"live": "all"}, s.api_football_key)
    if not payload:
        return {"ok": False, "reason": "fetch_failed"}
    if payload.get("errors"):
        log.warning("API-Football live errors: %s", payload["errors"])

    fixtures = payload.get("response", [])
    if not fixtures:
        return {"ok": True, "live_count": 0, "snapshots_written": 0, "events_added": 0}

    snaps = 0
    events_added = 0
    pending_pushes: list[tuple[LiveMatchState, str, int, str | None, str | None]] = []
    with SessionLocal() as db:
        for fix in fixtures:
            state = _upsert_state(db, fix)
            if not state:
                continue
            events_added += _record_events(db, state, fix)
            _write_snapshot(db, state)
            snaps += 1
            # Capture pending pushes BEFORE the session closes so the state
            # object's data is still accessible.
            for push in getattr(state, "_pending_pushes", []):
                pending_pushes.append((state, *push))
        db.commit()

    # Fire Telegram pushes outside the DB transaction. This is best-effort —
    # silently skipped if Telegram not configured.
    for state, ev_type, minute, team, player in pending_pushes:
        _send_event_telegram(state, ev_type, minute, team, player)

    log.info("Live poll: %d live, %d snapshots, %d new events",
             len(fixtures), snaps, events_added)
    return {"ok": True, "live_count": len(fixtures),
            "snapshots_written": snaps, "events_added": events_added}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_live_poll()
    print(result)
