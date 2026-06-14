from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Match, Prediction, BetLog
import pandas as pd
from datetime import datetime
from prediction.predictor import get_predictor
import prediction.cache as pred_cache
from utils.betting import kelly_fraction_stake


def _prediction_ttl(upcoming_matches) -> int:
    """Kick-off-proximity-aware cache TTL.

    < 2 h to kick-off  → 5 min  (lineups drop ~60 min before)
    2–24 h             → 15 min (catch late injury news)
    > 24 h             → 30 min (default)
    """
    dates = [m.match_date for m in upcoming_matches if m.match_date]
    if not dates:
        return 1800
    now = datetime.utcnow()
    nearest = min(dates)
    delta_min = (nearest - now).total_seconds() / 60
    if delta_min < 0:
        return 300    # already started — refresh very often
    if delta_min < 120:
        return 300    # < 2h
    if delta_min < 1440:
        return 900    # < 24h
    return 1800       # > 24h

EDGE_THRESHOLD   = 3.0   # minimum edge % to auto-log a simulated bet
KELLY_FRACTION   = 0.25  # fractional Kelly multiplier
MIN_MODEL_PROB   = 0.28  # model must assign >= 28% to the bet outcome (stops low-confidence edges)
MAX_DECIMAL_ODDS = 8.0   # never bet odds above this (longshot guard — sharp markets rarely err this hard)
MIN_IMPLIED_PROB = 0.10  # market implies <10%? skip — claimed "value" on extreme outsiders is almost always model error
MAX_EDGE_RATIO   = 2.5   # model_prob / implied_prob cap — anything above ~2.5x is implausible

router = APIRouter()


def _matches_to_df(matches, include_result: bool = True) -> pd.DataFrame:
    rows = []
    for m in matches:
        row = {
            "match_date":           m.match_date,
            "home_team":            m.home_team,
            "away_team":            m.away_team,
            "home_team_score":      m.home_team_score,
            "away_team_score":      m.away_team_score,
            "home_team_xG":         m.home_team_xG,
            "away_team_xG":         m.away_team_xG,
            "season":               m.season,
            "status":               m.status,
            "over_2_5_goals":       m.over_2_5_goals,
            "btts":                 m.btts,
            "referee":              getattr(m, "referee", None),
            "home_team_yellow_cards": getattr(m, "home_team_yellow_cards", None),
            "away_team_yellow_cards": getattr(m, "away_team_yellow_cards", None),
            "home_team_red_cards":  getattr(m, "home_team_red_cards", None),
            "away_team_red_cards":  getattr(m, "away_team_red_cards", None),
            "opening_home_odds":    getattr(m, "opening_home_odds", None),
            "opening_draw_odds":    getattr(m, "opening_draw_odds", None),
            "opening_away_odds":    getattr(m, "opening_away_odds", None),
            # Closing/market odds for the optional market-anchor blend
            "avg_home":             getattr(m, "avg_home", None),
            "avg_draw":             getattr(m, "avg_draw", None),
            "avg_away":             getattr(m, "avg_away", None),
            "b365_home":            getattr(m, "b365_home", None),
            "b365_draw":            getattr(m, "b365_draw", None),
            "b365_away":            getattr(m, "b365_away", None),
            "is_neutral_venue":     bool(getattr(m, "is_neutral_venue", False) or False),
            "competition":          getattr(m, "competition", None),
        }
        if include_result:
            row["result"] = m.result
        else:
            row["result"] = None
        rows.append(row)
    return pd.DataFrame(rows)


@router.get("/predictions/{competition_id}")
def get_predictions(competition_id: str, db: Session = Depends(get_db)):
    cache_key = f"predictions:{competition_id}"
    cached = pred_cache.get(cache_key)
    if cached:
        return cached

    upcoming = db.query(Match).filter(
        Match.competition == competition_id,
        Match.status.in_(["SCHEDULED", "TIMED"])
    ).all()

    if not upcoming:
        # Return next known fixture date so the UI can say "check back on X"
        next_match = (
            db.query(Match)
            .filter(
                Match.competition == competition_id,
                Match.match_date > datetime.utcnow(),
            )
            .order_by(Match.match_date)
            .first()
        )
        next_date = str(next_match.match_date)[:10] if next_match else None
        return {
            "competition": competition_id,
            "predictions": [],
            "next_fixture_date": next_date,
        }

    finished = db.query(Match).filter(
        Match.competition == competition_id,
        Match.status == "FINISHED",
    ).order_by(Match.match_date).all()

    upcoming_df = _matches_to_df(upcoming, include_result=False)
    finished_df = _matches_to_df(finished, include_result=True)

    predictor = get_predictor()
    predictions = predictor.predict(upcoming_df, finished_df, db=db, competition=competition_id)

    # Persist prediction records — upsert: skip if a row already exists for
    # this fixture (prevents duplicates when the endpoint is polled repeatedly).
    from app.config import get_settings as _gs
    _model_ver = _gs().model_version
    for pred in predictions:
        meta = pred["_meta"]
        match_ts = pd.Timestamp(pred["match_date"])
        existing_pred = db.query(Prediction).filter(
            Prediction.competition == competition_id,
            Prediction.home_team  == pred["home_team"],
            Prediction.away_team  == pred["away_team"],
            Prediction.match_date == match_ts,
        ).first()
        if existing_pred:
            continue
        record = Prediction(
            competition=competition_id,
            home_team=pred["home_team"],
            away_team=pred["away_team"],
            match_date=match_ts,
            predicted_outcome=meta["predicted_outcome"],
            home_win_prob=meta["home_win_prob"],
            draw_prob=meta["draw_prob"],
            away_win_prob=meta["away_win_prob"],
            predicted_over_2_5=meta["predicted_over_2_5"],
            over_2_5_prob=meta["over_2_5_prob"],
            predicted_btts=meta["predicted_btts"],
            btts_prob=meta["btts_prob"],
            model_version=_model_ver,
        )
        db.add(record)

    try:
        db.commit()
    except Exception:
        db.rollback()

    # Strip internal _meta before returning
    for pred in predictions:
        pred.pop("_meta", None)

    # Attach bookmaker odds + model edge where available (best-effort, never breaks)
    try:
        from data_collection.odds_client import attach_odds
        attach_odds(predictions, competition_id)
    except Exception:
        pass

    # Auto-log simulated bets for edges >= threshold (best-effort)
    try:
        _auto_log_bets(predictions, competition_id, db)
    except Exception:
        pass

    result = {"competition": competition_id, "predictions": predictions}
    pred_cache.set(cache_key, result, ttl=_prediction_ttl(upcoming))
    return result


def _send_telegram_alert(
    home: str, away: str, match_date: str, competition: str,
    bet_on: str, decimal_odds: float, edge_pct: float, kelly_pct: float,
) -> None:
    """Fire-and-forget Telegram alert when a high-edge bet is logged."""
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return
        if edge_pct < s.telegram_min_edge:
            return
        labels = {"H": "Home Win", "D": "Draw", "A": "Away Win"}
        text = (
            f"🤖 *Betting Bot — Edge Alert*\n"
            f"📅 {str(match_date)[:10]}  [{competition}]\n"
            f"⚽ {home} vs {away}\n"
            f"✅ Bet: *{labels.get(bet_on, bet_on)}*\n"
            f"📊 Odds: {decimal_odds:.2f} | Edge: +{edge_pct}% | Kelly: {kelly_pct}%"
        )
        import urllib.request, json as _json
        url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
        payload = _json.dumps({"chat_id": s.telegram_chat_id, "text": text, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _daily_kelly_used(db: Session) -> float:
    """Sum of kelly_pct for all bets logged today (UTC). Used for daily exposure cap."""
    from datetime import date
    from sqlalchemy import func as sqlfunc
    today = date.today()
    result = db.query(sqlfunc.sum(BetLog.kelly_pct)).filter(
        sqlfunc.date(BetLog.created_at) == today
    ).scalar()
    return float(result or 0.0)


def _auto_log_bets(predictions: list[dict], competition_id: str, db: Session):
    """Log a BetLog row for the best-edge outcome when edge >= EDGE_THRESHOLD."""
    from app.config import get_settings
    s = get_settings()

    # Daily exposure cap — skip if we've already hit the limit today
    daily_used = _daily_kelly_used(db)
    if daily_used >= s.max_daily_kelly_pct:
        return

    # Daily loss limit — stop if today's resolved P&L is below the stop-loss floor
    try:
        from datetime import date
        from sqlalchemy import func as sqlfunc
        today = date.today()
        today_pl = db.query(sqlfunc.sum(BetLog.profit_loss_units)).filter(
            sqlfunc.date(BetLog.resolved_at) == today,
            BetLog.profit_loss_units.isnot(None),
        ).scalar()
        today_pl = float(today_pl or 0.0)
        if today_pl <= -(s.daily_loss_limit_pct):
            return
    except Exception:
        pass

    outcome_keys = {"home": "H", "draw": "D", "away": "A"}
    prob_keys    = {"home": "home_win_prob", "draw": "draw_prob", "away": "away_win_prob"}

    # Hoist the daily-used query above the loop — one DB round-trip total.
    daily_used = _daily_kelly_used(db)

    for pred in predictions:
        edge_map = pred.get("edge") or {}
        odds_map = pred.get("odds") or {}
        if not edge_map or not odds_map:
            continue

        # Only consider 1x2 markets for auto-logging; skip over/btts edge keys.
        eligible = {k: v for k, v in edge_map.items() if k in outcome_keys}
        if not eligible:
            continue
        best_key = max(eligible, key=lambda k: eligible[k])
        best_edge = eligible[best_key]
        if best_edge < EDGE_THRESHOLD:
            continue

        bet_on = outcome_keys[best_key]
        decimal_odds = odds_map.get(best_key)
        if not decimal_odds or decimal_odds <= 1:
            continue

        # Longshot guard — sharp markets rarely misprice this hard. Skip odds above cap.
        if decimal_odds > MAX_DECIMAL_ODDS:
            continue

        model_prob = pred["outcome"].get(prob_keys[best_key], 0.0)
        # Confidence gate: skip if model assigns < MIN_MODEL_PROB to this outcome
        if model_prob < MIN_MODEL_PROB:
            continue

        implied_prob = 1.0 / decimal_odds
        # Implied-prob floor — anything the market thinks is <10% likely is filtered out
        if implied_prob < MIN_IMPLIED_PROB:
            continue

        # Edge-ratio cap — model_prob/implied_prob > 2.5 means we're claiming the market
        # is wildly wrong; almost always a calibration/data error, not real value.
        if model_prob / implied_prob > MAX_EDGE_RATIO:
            continue

        kelly_stake  = kelly_fraction_stake(model_prob, decimal_odds, KELLY_FRACTION)

        # Cap check using the already-fetched daily_used value
        if daily_used + kelly_stake > s.max_daily_kelly_pct:
            break

        existing = db.query(BetLog).filter(
            BetLog.competition  == competition_id,
            BetLog.home_team    == pred["home_team"],
            BetLog.away_team    == pred["away_team"],
            BetLog.match_date   == pd.Timestamp(pred["match_date"]),
            BetLog.bet_on       == bet_on,
        ).first()
        if existing:
            continue

        db.add(BetLog(
            competition  = competition_id,
            home_team    = pred["home_team"],
            away_team    = pred["away_team"],
            match_date   = pd.Timestamp(pred["match_date"]),
            bet_on       = bet_on,
            model_prob   = round(model_prob, 4),
            implied_prob = round(implied_prob, 4),
            edge_pct     = round(best_edge, 2),
            decimal_odds = round(decimal_odds, 3),
            kelly_pct    = kelly_stake,
            bookmaker    = odds_map.get("bookmaker"),
        ))

        # Alert for high-confidence bets
        _send_telegram_alert(
            home=pred["home_team"], away=pred["away_team"],
            match_date=pred["match_date"], competition=competition_id,
            bet_on=bet_on, decimal_odds=decimal_odds,
            edge_pct=round(best_edge, 2), kelly_pct=kelly_stake,
        )

    try:
        db.commit()
    except Exception:
        db.rollback()


@router.get("/h2h/{home_team}/{away_team}")
def get_head_to_head(
    home_team: str,
    away_team: str,
    n: int = 10,
    db: Session = Depends(get_db),
):
    """
    Last n meetings between two teams across all competitions, plus aggregate stats.
    """
    from sqlalchemy import or_, and_
    meetings = (
        db.query(Match)
        .filter(
            Match.status == "FINISHED",
            Match.result.isnot(None),
            or_(
                and_(Match.home_team == home_team, Match.away_team == away_team),
                and_(Match.home_team == away_team, Match.away_team == home_team),
            ),
        )
        .order_by(Match.match_date.desc())
        .limit(n)
        .all()
    )

    records = []
    home_wins = draws = away_wins = total_goals = 0
    for m in reversed(meetings):
        is_home = m.home_team == home_team
        gf = (m.home_team_score if is_home else m.away_team_score) or 0
        ga = (m.away_team_score if is_home else m.home_team_score) or 0
        result_from_home = m.result
        perspective = (
            "W" if (is_home and result_from_home == "H") or (not is_home and result_from_home == "A")
            else "L" if (is_home and result_from_home == "A") or (not is_home and result_from_home == "H")
            else "D"
        )
        if perspective == "W": home_wins += 1
        elif perspective == "D": draws += 1
        else: away_wins += 1
        total_goals += gf + ga
        records.append({
            "match_date":  m.match_date.isoformat()[:10] if m.match_date else None,
            "competition": m.competition,
            "home_team":   m.home_team,
            "away_team":   m.away_team,
            "score":       f"{m.home_team_score or 0}–{m.away_team_score or 0}",
            "result":      m.result,
            "perspective": perspective,
        })

    total = len(records)
    return {
        "home_team":  home_team,
        "away_team":  away_team,
        "meetings":   total,
        "summary": {
            "home_wins":  home_wins,
            "draws":      draws,
            "away_wins":  away_wins,
            "avg_goals":  round(total_goals / total, 2) if total else 0,
        },
        "records": list(reversed(records)),  # most recent first
    }


@router.get("/form/{competition_id}/{team}")
def get_team_form(competition_id: str, team: str, n: int = 10, db: Session = Depends(get_db)):
    """Last n finished matches for a team, with points/goals/xG per match."""
    matches = (
        db.query(Match)
        .filter(
            Match.competition == competition_id,
            Match.status == "FINISHED",
            (Match.home_team == team) | (Match.away_team == team),
        )
        .order_by(Match.match_date.desc())
        .limit(n)
        .all()
    )
    if not matches:
        raise HTTPException(status_code=404, detail=f"No finished matches found for {team}")

    form = []
    for m in reversed(matches):
        is_home = m.home_team == team
        goals_f = (m.home_team_score if is_home else m.away_team_score) or 0
        goals_a = (m.away_team_score if is_home else m.home_team_score) or 0
        xg_f    = (m.home_team_xG   if is_home else m.away_team_xG)
        pts = 3 if m.result == ("H" if is_home else "A") else (1 if m.result == "D" else 0)
        form.append({
            "match_date":  m.match_date.isoformat() if m.match_date else None,
            "opponent":    m.away_team if is_home else m.home_team,
            "venue":       "H" if is_home else "A",
            "goals_for":   goals_f,
            "goals_against": goals_a,
            "xg_for":      round(xg_f, 2) if xg_f is not None else None,
            "result":      m.result,
            "points":      pts,
        })

    return {"competition": competition_id, "team": team, "form": form}
