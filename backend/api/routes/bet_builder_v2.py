"""
POST /api/v1/parlay/correlated
  Computes the TRUE joint probability of a multi-leg same-match parlay using
  the DC model's 9x9 score matrix. Replaces the naive ∏ p_i product which
  ignores correlation (e.g. "team wins" and "over 2.5" are positively
  correlated because winning teams tend to score more).

Supported leg types within a single match:
  - 1x2:           {"market": "1x2", "side": "H" | "D" | "A"}
  - over_under:    {"market": "over_under", "line": 2.5, "side": "over" | "under"}
  - btts:          {"market": "btts", "side": "yes" | "no"}
  - double_chance: {"market": "double_chance", "side": "1X" | "X2" | "12"}
  - asian_handicap: {"market": "asian_handicap", "line": -1.5, "side": "home" | "away"}
  - first_to_score: {"market": "first_to_score", "side": "home" | "away" | "no_goal"}

Each leg defines a SUBSET of the 9x9 score matrix; the joint probability is
the sum of the matrix entries in the INTERSECTION of all legs' subsets.

For cross-match parlays, leg probabilities ARE independent, so we multiply.
"""
from __future__ import annotations
import numpy as np
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from app.database import get_db
from app.models import Match
from prediction.predictor import get_predictor

router = APIRouter()

MAX_GOALS = 8     # must match GoalsDistributionModel.MAX_GOALS
INTL = {"WC","EC","UNL","AFC","ASIA","CA","GOLD","FRIENDLY","WCQ_EU","WCQ_AF","WCQ_AS","WCQ_SA","WCQ_CC","WCQ_OC"}


class Leg(BaseModel):
    market: str
    side:   str
    line:   float | None = None


class MatchLegs(BaseModel):
    competition: str
    home_team:   str
    away_team:   str
    legs:        list[Leg]


class CorrelatedParlayRequest(BaseModel):
    matches: list[MatchLegs] = Field(..., min_length=1)
    stake:   float | None    = 1.0    # for payout calc
    odds:    float | None    = None   # combined parlay odds, if user-supplied


def _leg_mask(leg: Leg, h_goals: np.ndarray, a_goals: np.ndarray) -> np.ndarray:
    """
    Returns a boolean mask over the (MAX_GOALS+1, MAX_GOALS+1) score matrix
    indicating which (h, a) outcomes satisfy this leg.
    """
    m = leg.market.lower()
    side = leg.side.lower()

    if m == "1x2":
        if side == "h":  return h_goals > a_goals
        if side == "d":  return h_goals == a_goals
        if side == "a":  return h_goals < a_goals
    elif m == "over_under":
        line = leg.line or 2.5
        total = h_goals + a_goals
        if side == "over":  return total > line
        if side == "under": return total <= line
    elif m == "btts":
        if side == "yes": return (h_goals >= 1) & (a_goals >= 1)
        if side == "no":  return (h_goals < 1) | (a_goals < 1)
    elif m == "double_chance":
        if side == "1x": return h_goals >= a_goals
        if side == "x2": return h_goals <= a_goals
        if side == "12": return h_goals != a_goals
    elif m == "asian_handicap":
        line = leg.line or 0.0
        # Pretend handicap is applied to home team's score
        adjusted_h = h_goals + line
        if side == "home": return adjusted_h > a_goals
        if side == "away": return adjusted_h < a_goals
    elif m == "first_to_score":
        # First-to-score is not a deterministic function of the score matrix
        # — it depends on which team scored first. Approximate using the
        # competing-Poisson ratio (rate share of total). For correlation
        # purposes we treat it as the unconditional probability and skip the
        # matrix-mask part. Caller falls back to product.
        raise ValueError("first_to_score uses competing-Poisson, not matrix-derivable")
    raise ValueError(f"Unsupported leg: market={m} side={side}")


def _joint_match_prob(score_matrix: np.ndarray, legs: list[Leg]) -> float:
    """Joint P over a single 9x9 score matrix for ALL legs in this match."""
    h = np.arange(MAX_GOALS + 1).reshape(-1, 1)
    a = np.arange(MAX_GOALS + 1).reshape(1, -1)
    h_goals = np.broadcast_to(h, (MAX_GOALS + 1, MAX_GOALS + 1))
    a_goals = np.broadcast_to(a, (MAX_GOALS + 1, MAX_GOALS + 1))

    combined = np.ones_like(score_matrix, dtype=bool)
    for leg in legs:
        if leg.market.lower() == "first_to_score":
            # Skip (handled outside) — caller must multiply by marginal P later
            continue
        combined &= _leg_mask(leg, h_goals, a_goals)

    return float(np.sum(score_matrix[combined]))


def _resolve_dc(predictor, competition: str):
    if competition in INTL and predictor.dist_model_intl is not None:
        return predictor.dist_model_intl
    return predictor.dist_model


@router.post("/parlay/correlated")
def evaluate_correlated_parlay(
    req: CorrelatedParlayRequest,
    db: Session = Depends(get_db),
):
    """
    Returns true joint probability for the parlay, plus comparison to the
    naive product (∏ p_i) so the user can see how much the correlation
    correction shifts the price.
    """
    predictor = get_predictor()
    joint_total = 1.0
    naive_total = 1.0
    breakdown = []

    for ml in req.matches:
        match = (
            db.query(Match)
            .filter(
                Match.competition == ml.competition,
                Match.home_team   == ml.home_team,
                Match.away_team   == ml.away_team,
                Match.status.in_(["SCHEDULED", "TIMED", "IN_PLAY", "IN_PLAY_2H", "HT"]),
            )
            .order_by(Match.match_date)
            .first()
        )
        if not match:
            raise HTTPException(404, f"No fixture {ml.home_team} v {ml.away_team} in {ml.competition}")

        dc = _resolve_dc(predictor, ml.competition)
        if dc is None:
            raise HTTPException(500, "DC model not loaded")
        neutral = bool(match.is_neutral_venue)

        # Joint correlated probability for this match's legs
        score_matrix = dc.predict_score_matrix(ml.home_team, ml.away_team, neutral_venue=neutral)
        try:
            joint_match = _joint_match_prob(score_matrix, ml.legs)
        except ValueError:
            # Some leg type isn't matrix-representable — fall back to product
            joint_match = None

        # Naive product for this match — use all_markets to pull marginals
        markets = dc.all_markets(ml.home_team, ml.away_team, neutral_venue=neutral)
        naive_match = 1.0
        for leg in ml.legs:
            naive_match *= _marginal_prob(leg, markets)

        # Use the matrix-derived joint when available; otherwise fall back
        match_p = naive_match if joint_match is None else joint_match
        joint_total *= match_p
        naive_total *= naive_match

        breakdown.append({
            "competition":   ml.competition,
            "home_team":     ml.home_team,
            "away_team":     ml.away_team,
            "legs":          [leg.model_dump() for leg in ml.legs],
            "joint_match":   round(match_p, 4),
            "naive_match":   round(naive_match, 4),
            "lift":          round(match_p / naive_match, 3) if naive_match else None,
        })

    fair_odds_joint = (1.0 / joint_total) if joint_total > 0 else None
    fair_odds_naive = (1.0 / naive_total) if naive_total > 0 else None

    payload = {
        "joint_probability": round(joint_total, 5),
        "naive_probability": round(naive_total, 5),
        "fair_odds_joint":   round(fair_odds_joint, 2) if fair_odds_joint else None,
        "fair_odds_naive":   round(fair_odds_naive, 2) if fair_odds_naive else None,
        "correlation_lift":  round(joint_total / naive_total, 3) if naive_total else None,
        "breakdown":         breakdown,
    }
    if req.odds:
        implied = 1.0 / req.odds if req.odds > 0 else 0
        edge = (joint_total - implied) * 100
        payload["bookmaker_odds"] = req.odds
        payload["implied_prob"]   = round(implied, 4)
        payload["edge_pct"]       = round(edge, 2)
        if req.stake:
            payload["expected_payout"] = round(req.stake * req.odds * joint_total - req.stake * (1 - joint_total), 4)
    return payload


def _marginal_prob(leg: Leg, markets: dict) -> float:
    m  = leg.market.lower()
    side = leg.side.lower()
    if m == "1x2":
        return float(markets.get("1x2", {}).get(side.upper(), 0.0))
    if m == "over_under":
        line = str(leg.line or 2.5)
        ou = markets.get("over_under", {}).get(line, {})
        return float(ou.get(side, 0.0))
    if m == "btts":
        return float(markets.get("btts", {}).get(side, 0.0))
    if m == "double_chance":
        return float(markets.get("double_chance", {}).get(leg.side.upper(), 0.0))
    if m == "asian_handicap":
        line = str(leg.line if leg.line is not None else 0.0)
        ah = markets.get("asian_handicap", {}).get(line, {})
        return float(ah.get(side, 0.0))
    if m == "first_to_score":
        return float(markets.get("first_to_score", {}).get(side, 0.0))
    return 0.0
