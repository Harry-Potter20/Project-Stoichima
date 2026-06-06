"""
GET /api/v1/bankroll/{competition_id}

Returns simulated bankroll performance based on auto-logged bets (BetLog table).
Bets are logged whenever the model detects edge >= 3% on any upcoming match.
Stake is sized using fractional Kelly criterion (1/4 Kelly default).
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import BetLog, Prediction
import numpy as np
import io
import csv
from typing import Optional
from datetime import datetime
from utils.betting import kelly_fraction_stake

router = APIRouter()

KELLY_FRACTION = 0.25   # 1/4 Kelly to limit variance


class ManualBetIn(BaseModel):
    home_team:    str
    away_team:    str
    competition:  str
    match_date:   str            # ISO date string "2026-06-11"
    bet_on:       str            # H / D / A / over_2.5 / btts_yes / dnb_home / etc.
    market:       Optional[str] = "1x2"
    decimal_odds: float
    model_prob:   float          # your estimated probability (0–1)
    stake_pct:    Optional[float] = None   # % of bankroll to stake; None → Kelly
    notes:        Optional[str] = None
    tags:         Optional[str] = None    # comma-separated
    sport:        Optional[str] = "football"


class BetNoteIn(BaseModel):
    notes: Optional[str] = None
    tags:  Optional[str] = None


def _bankroll_size() -> float:
    try:
        from app.config import get_settings
        return get_settings().bankroll_size
    except Exception:
        return 1000.0


@router.get("/bets/recommendations")
def get_recommendations(
    competition: str = "all",
    min_edge: float = 0.0,
    db: Session = Depends(get_db),
):
    """Pending bet recommendations — bets not yet resolved (upcoming matches)."""
    bankroll = _bankroll_size()
    query = db.query(BetLog).filter(BetLog.won.is_(None))
    if competition != "all":
        query = query.filter(BetLog.competition == competition)
    if min_edge > 0:
        query = query.filter(BetLog.edge_pct >= min_edge)

    bets = query.order_by(BetLog.match_date).all()
    total_kelly = round(sum(b.kelly_pct for b in bets), 2)
    avg_edge    = round(sum(b.edge_pct  for b in bets) / len(bets), 2) if bets else 0.0

    return {
        "competition":           competition,
        "bankroll_size":         bankroll,
        "count":                 len(bets),
        "total_kelly_exposure":  total_kelly,
        "avg_edge_pct":          avg_edge,
        "recommendations": [
            {
                "match_date":    b.match_date.isoformat() if b.match_date else None,
                "competition":   b.competition,
                "home_team":     b.home_team,
                "away_team":     b.away_team,
                "bet_on":        b.bet_on,
                "decimal_odds":  b.decimal_odds,
                "edge_pct":      b.edge_pct,
                "kelly_pct":     b.kelly_pct,
                "stake_amount":  round(bankroll * b.kelly_pct / 100, 2),
                "model_prob":    round(b.model_prob * 100, 1),
                "implied_prob":  round(b.implied_prob * 100, 1),
                "bookmaker":     b.bookmaker,
            }
            for b in bets
        ],
    }


@router.get("/bets/slip")
def get_bet_slip(
    competition: str = "all",
    min_edge: float = 3.0,
    db: Session = Depends(get_db),
):
    """
    Printable bet slip — all pending recommendations above edge threshold,
    grouped by match date with Kelly stake as % of bankroll and real currency amount.
    """
    bankroll = _bankroll_size()
    query = db.query(BetLog).filter(BetLog.won.is_(None))
    if competition != "all":
        query = query.filter(BetLog.competition == competition)
    if min_edge > 0:
        query = query.filter(BetLog.edge_pct >= min_edge)

    bets = query.order_by(BetLog.match_date, BetLog.edge_pct.desc()).all()
    total_kelly  = round(sum(b.kelly_pct for b in bets), 2)
    total_stake  = round(bankroll * total_kelly / 100, 2)

    from collections import defaultdict as _dd
    by_date: dict = _dd(list)
    for b in bets:
        date_str = b.match_date.date().isoformat() if b.match_date else "unknown"
        by_date[date_str].append({
            "competition":  b.competition,
            "home_team":    b.home_team,
            "away_team":    b.away_team,
            "bet_on":       b.bet_on,
            "decimal_odds": b.decimal_odds,
            "edge_pct":     b.edge_pct,
            "kelly_pct":    b.kelly_pct,
            "stake_amount": round(bankroll * b.kelly_pct / 100, 2),
            "model_prob":   round(b.model_prob * 100, 1),
            "bookmaker":    b.bookmaker,
        })

    return {
        "bankroll_size":     bankroll,
        "total_bets":        len(bets),
        "total_kelly_stake": total_kelly,
        "total_stake_amount": total_stake,
        "slip": [
            {"date": d, "bets": bs}
            for d, bs in sorted(by_date.items())
        ],
    }


@router.get("/bets/export.csv")
def export_bets_csv(
    competition: str = "all",
    resolved_only: bool = False,
    db: Session = Depends(get_db),
):
    """Download full BetLog history as CSV — useful for external analysis and tax records."""
    bankroll = _bankroll_size()
    query = db.query(BetLog)
    if competition != "all":
        query = query.filter(BetLog.competition == competition)
    if resolved_only:
        query = query.filter(BetLog.won.isnot(None))

    bets = query.order_by(BetLog.match_date).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "match_date", "competition", "home_team", "away_team",
        "bet_on", "decimal_odds", "edge_pct", "kelly_pct",
        "stake_amount", "model_prob_pct", "implied_prob_pct", "bookmaker",
        "actual_result", "won", "profit_loss_units", "profit_loss_currency",
        "closing_home_odds", "closing_draw_odds", "closing_away_odds",
        "created_at", "resolved_at",
    ])
    for b in bets:
        pl_currency = round((b.profit_loss_units or 0) * bankroll / 100, 2) if b.profit_loss_units is not None else ""
        writer.writerow([
            b.match_date.date().isoformat() if b.match_date else "",
            b.competition, b.home_team, b.away_team,
            b.bet_on, b.decimal_odds, b.edge_pct, b.kelly_pct,
            round(bankroll * b.kelly_pct / 100, 2),
            round(b.model_prob * 100, 1), round(b.implied_prob * 100, 1),
            b.bookmaker or "",
            b.actual_result or "", b.won if b.won is not None else "",
            b.profit_loss_units or "", pl_currency,
            b.closing_home_odds or "", b.closing_draw_odds or "", b.closing_away_odds or "",
            b.created_at.isoformat() if b.created_at else "",
            b.resolved_at.isoformat() if b.resolved_at else "",
        ])

    output.seek(0)
    filename = f"stoichima_bets_{competition}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/bets/parlay")
def get_parlay(
    ids: str = "",
    top_n: int = 3,
    min_edge: float = 3.0,
    db: Session = Depends(get_db),
):
    """
    Accumulator / parlay builder.

    Pass comma-separated BetLog IDs (`?ids=1,2,3`) OR leave blank to auto-select
    the top `top_n` pending bets by edge above `min_edge`.

    Returns combined probability, combined decimal odds, expected value,
    and fractional Kelly stake for the parlay as a whole.
    """
    bankroll = _bankroll_size()

    if ids:
        id_list = [int(i.strip()) for i in ids.split(",") if i.strip().isdigit()]
        legs = db.query(BetLog).filter(BetLog.id.in_(id_list), BetLog.won.is_(None)).all()
    else:
        legs = (
            db.query(BetLog)
            .filter(BetLog.won.is_(None), BetLog.edge_pct >= min_edge)
            .order_by(BetLog.edge_pct.desc())
            .limit(top_n)
            .all()
        )

    if not legs:
        return {"legs": [], "parlay": None}

    combined_prob  = 1.0
    combined_odds  = 1.0
    for b in legs:
        combined_prob  *= b.model_prob
        combined_odds  *= b.decimal_odds

    ev = round(combined_prob * combined_odds - 1.0, 4)  # expected profit per unit
    kelly_stake = kelly_fraction_stake(combined_prob, combined_odds, KELLY_FRACTION)
    stake_amt   = round(bankroll * kelly_stake / 100, 2)

    return {
        "legs": [
            {
                "id":           b.id,
                "match":        f"{b.home_team} v {b.away_team}",
                "match_date":   b.match_date.date().isoformat() if b.match_date else None,
                "bet_on":       b.bet_on,
                "decimal_odds": b.decimal_odds,
                "model_prob":   round(b.model_prob * 100, 1),
                "edge_pct":     b.edge_pct,
            }
            for b in legs
        ],
        "parlay": {
            "legs":             len(legs),
            "combined_prob_pct": round(combined_prob * 100, 3),
            "combined_odds":    round(combined_odds, 3),
            "expected_value":   ev,
            "ev_pct":           round(ev * 100, 2),
            "kelly_pct":        kelly_stake,
            "stake_amount":     stake_amt,
            "positive_ev":      ev > 0,
        },
    }


@router.get("/bets/market_breakdown")
def get_market_breakdown(
    competition: str = "all",
    db: Session = Depends(get_db),
):
    """
    P&L and accuracy broken down by bet type (H / D / A) and by competition.
    Shows where the model's edge is genuine vs lucky.
    """
    query = db.query(BetLog).filter(BetLog.won.isnot(None))
    if competition != "all":
        query = query.filter(BetLog.competition == competition)
    bets = query.all()

    if not bets:
        return {"competition": competition, "by_outcome": {}, "by_competition": {}}

    def _summary(subset):
        if not subset:
            return None
        won   = [b for b in subset if b.won]
        total_pl = sum(b.profit_loss_units or 0 for b in subset)
        total_staked = sum(b.kelly_pct for b in subset)
        return {
            "bets":        len(subset),
            "won":         len(won),
            "win_rate":    round(len(won) / len(subset) * 100, 1),
            "total_pl":    round(total_pl, 3),
            "roi_pct":     round(total_pl / total_staked * 100, 1) if total_staked else 0.0,
            "avg_edge":    round(sum(b.edge_pct for b in subset) / len(subset), 1),
        }

    by_outcome = {
        label: _summary([b for b in bets if b.bet_on == label])
        for label in ["H", "D", "A"]
    }
    by_comp = {}
    comps = sorted({b.competition for b in bets})
    for comp in comps:
        by_comp[comp] = _summary([b for b in bets if b.competition == comp])

    return {
        "competition":     competition,
        "by_outcome":      {k: v for k, v in by_outcome.items() if v},
        "by_competition":  by_comp,
    }


@router.get("/bets/arb")
def get_arb_opportunities(db: Session = Depends(get_db)):
    """
    Scan current bookmaker prices for arbitrage opportunities.
    Returns sorted list of arb opportunities (arb_margin desc).
    """
    try:
        from data_collection.odds_client import scan_arb, SPORT_KEY_MAP
        competitions = list(SPORT_KEY_MAP.keys())
        opportunities = scan_arb(competitions)
        return {
            "count": len(opportunities),
            "opportunities": opportunities,
        }
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"Odds API unavailable: {exc}")


@router.post("/bets/manual")
def log_manual_bet(body: ManualBetIn, db: Session = Depends(get_db)):
    """
    Manually log a bet to the BetLog for settlement tracking.
    Implied probability is derived from decimal_odds. Edge = model_prob - implied.
    If stake_pct is not provided, fractional Kelly is used.
    """
    bankroll = _bankroll_size()

    implied = 1.0 / body.decimal_odds if body.decimal_odds > 1 else 0.0
    edge_pct = round((body.model_prob - implied) * 100, 2)

    # Kelly sizing
    kelly_pct = kelly_fraction_stake(body.model_prob, body.decimal_odds, KELLY_FRACTION)
    stake_pct = body.stake_pct if body.stake_pct is not None else kelly_pct
    stake_amt = round(bankroll * stake_pct / 100, 2)

    try:
        match_date = datetime.fromisoformat(body.match_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid match_date — use ISO format e.g. 2026-06-11")

    bet = BetLog(
        competition=body.competition,
        home_team=body.home_team,
        away_team=body.away_team,
        match_date=match_date,
        bet_on=body.bet_on,
        market=body.market or "1x2",
        model_prob=body.model_prob,
        implied_prob=round(implied, 4),
        edge_pct=edge_pct,
        decimal_odds=body.decimal_odds,
        kelly_pct=stake_pct,
        stake_amount=stake_amt,
        source="manual",
        notes=body.notes,
        tags=body.tags,
        sport=body.sport or "football",
    )
    db.add(bet)
    db.commit()
    db.refresh(bet)

    return {
        "id":           bet.id,
        "home_team":    bet.home_team,
        "away_team":    bet.away_team,
        "competition":  bet.competition,
        "match_date":   bet.match_date.isoformat(),
        "bet_on":       bet.bet_on,
        "market":       bet.market,
        "decimal_odds": bet.decimal_odds,
        "model_prob":   round(bet.model_prob * 100, 1),
        "implied_prob": round(bet.implied_prob * 100, 1),
        "edge_pct":     bet.edge_pct,
        "kelly_pct":    bet.kelly_pct,
        "stake_amount": bet.stake_amount,
        "notes":        bet.notes,
        "tags":         bet.tags,
        "source":       bet.source,
    }


@router.patch("/bets/{bet_id}/notes")
def update_bet_notes(bet_id: int, body: BetNoteIn, db: Session = Depends(get_db)):
    """Update notes and/or tags on any BetLog row."""
    bet = db.query(BetLog).filter(BetLog.id == bet_id).first()
    if not bet:
        raise HTTPException(status_code=404, detail="Bet not found")
    if body.notes is not None:
        bet.notes = body.notes
    if body.tags is not None:
        bet.tags = body.tags
    db.commit()
    return {"id": bet_id, "notes": bet.notes, "tags": bet.tags}


@router.delete("/bets/{bet_id}")
def delete_manual_bet(bet_id: int, db: Session = Depends(get_db)):
    """Delete a manually-logged pending bet (cannot delete resolved bets)."""
    bet = db.query(BetLog).filter(BetLog.id == bet_id).first()
    if not bet:
        raise HTTPException(status_code=404, detail="Bet not found")
    if bet.won is not None:
        raise HTTPException(status_code=400, detail="Cannot delete a resolved bet")
    db.delete(bet)
    db.commit()
    return {"deleted": bet_id}


@router.get("/bets/tags")
def get_tag_analytics(db: Session = Depends(get_db)):
    """
    P&L and win-rate grouped by bet tag.
    Tags are comma-separated strings stored on BetLog rows (e.g. 'value,wc2026').
    Only resolved bets are included.
    """
    from collections import defaultdict as _dd
    resolved = db.query(BetLog).filter(
        BetLog.won.isnot(None),
        BetLog.tags.isnot(None),
    ).all()

    tag_map: dict = _dd(list)
    for b in resolved:
        for tag in (b.tags or "").split(","):
            tag = tag.strip()
            if tag:
                tag_map[tag].append(b)

    result = []
    for tag, bets in sorted(tag_map.items()):
        won = [b for b in bets if b.won]
        total_pl = sum(b.profit_loss_units or 0 for b in bets)
        total_staked = sum(b.kelly_pct for b in bets)
        result.append({
            "tag":       tag,
            "bets":      len(bets),
            "won":       len(won),
            "win_rate":  round(len(won) / len(bets) * 100, 1),
            "total_pl":  round(total_pl, 3),
            "roi_pct":   round(total_pl / total_staked * 100, 1) if total_staked else 0.0,
            "avg_edge":  round(sum(b.edge_pct for b in bets) / len(bets), 1),
        })

    return {"count": len(result), "tags": result}


@router.get("/bankroll/{competition_id}")
def get_bankroll(competition_id: str, db: Session = Depends(get_db)):
    query = db.query(BetLog).filter(BetLog.competition == competition_id)
    if competition_id == "all":
        query = db.query(BetLog)

    bets = query.order_by(BetLog.match_date).all()

    if not bets:
        return {
            "competition": competition_id,
            "count": 0,
            "summary": None,
            "running": [],
            "bets": [],
        }

    total = len(bets)
    resolved = [b for b in bets if b.won is not None]
    won_bets  = [b for b in resolved if b.won]
    lost_bets = [b for b in resolved if not b.won]
    pending   = total - len(resolved)

    total_staked = round(sum(b.kelly_pct for b in resolved), 2)
    total_pl     = round(sum(b.profit_loss_units or 0 for b in resolved), 2)
    roi = round((total_pl / total_staked * 100) if total_staked else 0, 1)
    win_rate = round(len(won_bets) / len(resolved) * 100 if resolved else 0, 1)

    # Running P&L sorted by match date
    running, cum_pl = [], 0.0
    for b in resolved:
        cum_pl += b.profit_loss_units or 0
        running.append({
            "date": b.match_date.date().isoformat() if b.match_date else None,
            "cumulative_units": round(cum_pl, 3),
            "match": f"{b.home_team} v {b.away_team}",
            "won": b.won,
        })

    # Recent 50 bets for table
    recent = []
    for b in bets[-50:]:
        recent.append({
            "match_date":  b.match_date.date().isoformat() if b.match_date else None,
            "home_team":   b.home_team,
            "away_team":   b.away_team,
            "bet_on":      b.bet_on,
            "decimal_odds": b.decimal_odds,
            "edge_pct":    b.edge_pct,
            "kelly_pct":   b.kelly_pct,
            "model_prob":  round(b.model_prob * 100, 1),
            "implied_prob": round(b.implied_prob * 100, 1),
            "bookmaker":   b.bookmaker,
            "actual":      b.actual_result,
            "won":         b.won,
            "pl_units":    b.profit_loss_units,
        })

    return {
        "competition": competition_id,
        "count": total,
        "summary": {
            "total_bets":       total,
            "resolved":         len(resolved),
            "pending":          pending,
            "won":              len(won_bets),
            "lost":             len(lost_bets),
            "win_rate_pct":     win_rate,
            "total_staked":     total_staked,
            "total_pl_units":   total_pl,
            "roi_pct":          roi,
        },
        "running": running,
        "bets": recent,
    }


@router.get("/predictions/export.csv")
def export_predictions_csv(
    competition: str = "all",
    db: Session = Depends(get_db),
):
    """Download all stored predictions (with actuals where resolved) as CSV."""
    q = db.query(Prediction)
    if competition != "all":
        q = q.filter(Prediction.competition == competition)
    preds = q.order_by(Prediction.match_date.desc()).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "date", "competition", "home_team", "away_team",
        "predicted_outcome", "home_win_prob", "draw_prob", "away_win_prob",
        "predicted_over_2_5", "over_2_5_prob", "predicted_btts", "btts_prob",
        "actual_outcome", "actual_over_2_5", "actual_btts",
        "outcome_correct", "model_version", "created_at",
    ])
    for p in preds:
        outcome_correct = (
            int(p.predicted_outcome == p.actual_outcome)
            if p.actual_outcome else ""
        )
        w.writerow([
            str(p.match_date)[:10] if p.match_date else "",
            p.competition, p.home_team, p.away_team,
            p.predicted_outcome,
            round(p.home_win_prob, 4) if p.home_win_prob else "",
            round(p.draw_prob, 4) if p.draw_prob else "",
            round(p.away_win_prob, 4) if p.away_win_prob else "",
            p.predicted_over_2_5,
            round(p.over_2_5_prob, 4) if p.over_2_5_prob else "",
            p.predicted_btts,
            round(p.btts_prob, 4) if p.btts_prob else "",
            p.actual_outcome or "",
            p.actual_over_2_5 if p.actual_over_2_5 is not None else "",
            p.actual_btts if p.actual_btts is not None else "",
            outcome_correct,
            p.model_version or "",
            str(p.created_at)[:19] if p.created_at else "",
        ])

    buf.seek(0)
    filename = f"predictions_{competition}_{datetime.utcnow().date()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/bets/value-scan")
def value_scan(
    min_edge: float = 3.0,
    max_results: int = 20,
    db: Session = Depends(get_db),
):
    """
    Cross-competition value scan — returns highest-edge pending bets across all leagues.
    Sorted by edge descending. Useful for prioritising the best opportunities each day.
    """
    bets = (
        db.query(BetLog)
        .filter(BetLog.won.is_(None))   # pending only
        .order_by(BetLog.edge_pct.desc())
        .all()
    )
    results = []
    for b in bets:
        edge = float(b.edge_pct or 0)
        if edge < min_edge:
            break
        results.append({
            "competition":  b.competition,
            "match_date":   str(b.match_date)[:10] if b.match_date else None,
            "home_team":    b.home_team,
            "away_team":    b.away_team,
            "bet_on":       b.bet_on,
            "market":       b.market or "1x2",
            "decimal_odds": round(b.decimal_odds, 2) if b.decimal_odds else None,
            "edge_pct":     round(edge, 2),
            "kelly_pct":    round(float(b.kelly_pct or 0), 2),
            "stake_amount": b.stake_amount,
        })
        if len(results) >= max_results:
            break
    return {"min_edge": min_edge, "count": len(results), "value_bets": results}


@router.get("/odds/steam")
def steam_scanner(
    threshold: float = 8.0,
    db: Session = Depends(get_db),
):
    """
    Sharp-money steam scanner — upcoming matches where odds moved >= threshold%
    toward one side. Steam indicates coordinated sharp-money action.
    Reads from OddsSnapshot table (populated by the odds poller scheduler job).
    """
    try:
        from app.models import OddsSnapshot, Match
        from data_processing.team_normalizer import normalise_team_name

        snapshots = db.query(OddsSnapshot).all()
        if not snapshots:
            return {"threshold_pct": threshold, "steam_moves": [], "note": "No odds snapshots available yet"}

        steam_moves = []
        for snap in snapshots:
            # Line movement = ratio of closing to opening; < 1 = shortened = steam
            def _move(closing, opening):
                try:
                    c, o = float(closing or 0), float(opening or 0)
                    if c > 1 and o > 1:
                        return round((1 - c / o) * 100, 1)  # positive = odds shortened
                except Exception:
                    pass
                return 0.0

            h_move = _move(snap.home_odds, getattr(snap, "opening_home_odds", None))
            d_move = _move(snap.draw_odds, getattr(snap, "opening_draw_odds", None))
            a_move = _move(snap.away_odds, getattr(snap, "opening_away_odds", None))

            max_move = max(h_move, d_move, a_move)
            if max_move < threshold:
                continue

            direction = (
                "home" if h_move == max_move else
                "draw" if d_move == max_move else "away"
            )
            steam_moves.append({
                "competition": snap.competition,
                "home_team":   snap.home_team,
                "away_team":   snap.away_team,
                "direction":   direction,
                "move_pct":    max_move,
                "home_move":   h_move,
                "draw_move":   d_move,
                "away_move":   a_move,
                "current_odds": {
                    "home": snap.home_odds,
                    "draw": snap.draw_odds,
                    "away": snap.away_odds,
                },
                "updated_at":  str(snap.updated_at)[:16] if snap.updated_at else None,
            })

        steam_moves.sort(key=lambda x: -x["move_pct"])
        return {
            "threshold_pct": threshold,
            "count":         len(steam_moves),
            "steam_moves":   steam_moves,
        }
    except Exception as e:
        return {"threshold_pct": threshold, "steam_moves": [], "error": str(e)}
