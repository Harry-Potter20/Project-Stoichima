"""
Live in-play Poisson predictor (V2.1 — closed-form decay).

Given pre-match Poisson rate parameters (λ_home, λ_away) from the Dixon-Coles
fit, the current score (h, a), the minute, and red-card counts, this module
computes the live probability distribution over remaining outcomes.

Approach
--------
The remaining match minutes scale the Poisson rates linearly. Red cards apply
a multiplicative penalty to the affected team (literature consensus is roughly
0.65 multiplier for attack and 0.85 for defense per red card; we use the
attacking-side penalty since that matches what fans observe).

Final probabilities are derived from the joint distribution of
(home_goals_remaining, away_goals_remaining) added to the current score:
  P(home wins) = Σ_{i, j} P_h(i) * P_a(j) * I[h + i > a + j]

This is fast (<1ms per match), deterministic, and requires no training. It
provides the baseline for the live tab. Future iterations (xG-rate driven,
LSTM) can replace _live_lambdas() while keeping the rest of the pipeline.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
from scipy.stats import poisson
import numpy as np

# Tuneable constants — keep here so they show up in code review.
RED_CARD_ATTACK_PENALTY = 0.65   # per red card on the affected team's attack
INJURY_TIME_BUFFER_MIN  = 4      # implicit minutes past 90'
MAX_GOALS               = 8      # truncate Poisson tail


@dataclass
class LiveSnapshot:
    home_win_prob:        float
    draw_prob:            float
    away_win_prob:        float
    over_2_5_prob:        float
    btts_prob:            float
    expected_total_goals: float
    home_lambda_remaining: float
    away_lambda_remaining: float


def _remaining_minutes(minute: int, status: str) -> int:
    """How many minutes of football are still expected."""
    if status in ("FT", "AET", "PEN"):
        return 0
    if status == "HT":
        # Halftime — second half + injury time
        return 45 + INJURY_TIME_BUFFER_MIN
    # IN_PLAY / IN_PLAY_2H / SUSPENDED
    remaining = max(0, 90 - minute) + INJURY_TIME_BUFFER_MIN
    return remaining


def _live_lambdas(
    prematch_lambda_home: float,
    prematch_lambda_away: float,
    minute:               int,
    status:               str,
    home_red_cards:       int,
    away_red_cards:       int,
) -> Tuple[float, float]:
    """
    Project remaining-match Poisson rates from pre-match parameters.
    Returns (λ_home_remaining, λ_away_remaining).
    """
    rem_minutes = _remaining_minutes(minute, status)
    time_factor = rem_minutes / 94.0       # 90 + injury buffer

    lam_h = prematch_lambda_home * time_factor
    lam_a = prematch_lambda_away * time_factor

    # Red cards: attacking power on the carded team drops; opponent benefits
    # marginally (we model only the dominant effect: own attack down).
    if home_red_cards > 0:
        lam_h *= RED_CARD_ATTACK_PENALTY ** home_red_cards
    if away_red_cards > 0:
        lam_a *= RED_CARD_ATTACK_PENALTY ** away_red_cards

    return float(lam_h), float(lam_a)


def predict_live(
    prematch_lambda_home: float,
    prematch_lambda_away: float,
    minute:               int,
    home_score:           int,
    away_score:           int,
    status:               str = "IN_PLAY",
    home_red_cards:       int = 0,
    away_red_cards:       int = 0,
) -> LiveSnapshot:
    """
    Compute the live outcome distribution.

    pre-match λ's are typically from GoalsDistributionModel.get_lambdas(home, away).
    """
    lam_h, lam_a = _live_lambdas(
        prematch_lambda_home, prematch_lambda_away,
        minute, status, home_red_cards, away_red_cards,
    )

    # Joint distribution of remaining goals
    goals = np.arange(MAX_GOALS + 1)
    pmf_h = poisson.pmf(goals, lam_h)
    pmf_a = poisson.pmf(goals, lam_a)
    # Renormalise (truncation loses a tiny bit of mass)
    pmf_h /= pmf_h.sum()
    pmf_a /= pmf_a.sum()

    joint = np.outer(pmf_h, pmf_a)        # joint[i, j] = P(home scores i more, away scores j more)

    # Outcome probabilities — overlay current score
    home_win = draw = away_win = 0.0
    btts     = 0.0
    over25   = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p   = joint[i, j]
            fin_h = home_score + i
            fin_a = away_score + j
            if fin_h > fin_a:    home_win += p
            elif fin_h < fin_a:  away_win += p
            else:                draw     += p
            if fin_h >= 1 and fin_a >= 1: btts += p
            if fin_h + fin_a >= 3:        over25 += p

    expected_total = home_score + away_score + lam_h + lam_a

    return LiveSnapshot(
        home_win_prob          = round(float(home_win), 4),
        draw_prob              = round(float(draw),     4),
        away_win_prob          = round(float(away_win), 4),
        over_2_5_prob          = round(float(over25),   4),
        btts_prob              = round(float(btts),     4),
        expected_total_goals   = round(float(expected_total), 2),
        home_lambda_remaining  = round(float(lam_h), 3),
        away_lambda_remaining  = round(float(lam_a), 3),
    )
