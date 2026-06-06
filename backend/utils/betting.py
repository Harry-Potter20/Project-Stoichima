"""Shared betting-math utilities used across routes and models."""


def kelly_fraction_stake(
    model_prob: float,
    decimal_odds: float,
    fraction: float = 0.25,
) -> float:
    """
    Fractional Kelly criterion stake as a percentage of bankroll.

    Args:
        model_prob:   Model's estimated win probability (0–1).
        decimal_odds: Bookmaker decimal odds (e.g. 2.10).
        fraction:     Kelly fraction multiplier (default 0.25 = quarter-Kelly).

    Returns:
        Stake as % of bankroll, floored at 0.0. Round at the call site.
    """
    if decimal_odds <= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    kelly_full = max(0.0, (model_prob * b - (1.0 - model_prob)) / b)
    return round(kelly_full * fraction * 100, 2)


def implied_prob(decimal_odds: float) -> float:
    """Vig-inclusive implied probability from decimal odds."""
    if decimal_odds <= 0:
        return 0.0
    return 1.0 / decimal_odds


def vig_free_prob(home_odds: float, draw_odds: float, away_odds: float) -> tuple[float, float, float]:
    """
    Remove the bookmaker vig from a three-way market using basic normalisation.
    Returns (home_prob, draw_prob, away_prob) summing to 1.0.
    """
    raw = [implied_prob(o) for o in (home_odds, draw_odds, away_odds)]
    total = sum(raw)
    if total <= 0:
        return (1/3, 1/3, 1/3)
    return tuple(p / total for p in raw)
