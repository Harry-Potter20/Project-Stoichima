"""
Monte Carlo Tournament Simulator for WC 2026 (and any group-stage + knockout tournament).

Usage:
    from prediction.tournament_simulator import MonteCarloSimulator
    sim = MonteCarloSimulator("WC", predictor, db)
    results = sim.simulate(n=10_000)

Returns a dict of { team_name: { "group": float, "r32": float, ... , "winner": float } }
"""

import numpy as np
import pandas as pd
from collections import defaultdict
from sqlalchemy.orm import Session

from app.models import Match

# WC 2026 format: 12 groups of 4, top 2 qualify + best 8 third-place teams → 32 total
WC_FORMAT = {
    "group_size": 4,
    "n_groups": 12,
    "auto_qualify": 2,        # top 2 per group
    "best_third_qualify": 8,  # best third-place teams that also advance
}

# Tournament stage labels for the output
STAGES = ["group", "r32", "r16", "qf", "sf", "final", "winner"]

STAGE_ENC = {
    "Group Stage": 0,
    "R32": 1, "R16": 2, "QF": 3, "SF": 4, "Final": 5,
}


class MonteCarloSimulator:

    def __init__(self, competition_id: str, predictor, db: Session):
        self.competition_id = competition_id
        self.predictor = predictor
        self.db = db
        self._groups: dict[str, list[str]] = {}
        self._finished: list[dict] = []
        self._upcoming: list[dict] = []
        self._history_df: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load tournament state from DB."""
        matches = (
            self.db.query(Match)
            .filter(Match.competition == self.competition_id)
            .order_by(Match.match_date)
            .all()
        )
        if not matches:
            raise ValueError(
                f"No matches found for competition {self.competition_id}. "
                "Run collect_international_data() first."
            )

        groups: dict[str, set] = defaultdict(set)
        finished, upcoming = [], []

        for m in matches:
            grp = m.tournament_group or "?"
            groups[grp].add(m.home_team)
            groups[grp].add(m.away_team)

            row = {
                "match_id":      m.id,
                "home_team":     m.home_team,
                "away_team":     m.away_team,
                "match_date":    m.match_date,
                "tournament_group": grp,
                "tournament_stage": m.tournament_stage or "Group Stage",
                "home_team_score": m.home_team_score,
                "away_team_score": m.away_team_score,
                "result":        m.result,
                "season":        m.season,
                "status":        m.status,
                "is_neutral_venue": bool(m.is_neutral_venue),
            }
            if m.status == "FINISHED" and m.result:
                finished.append(row)
            else:
                upcoming.append(row)

        self._groups   = {k: sorted(v) for k, v in groups.items() if k != "?"}
        self._finished = finished
        self._upcoming = upcoming
        self._history_df = pd.DataFrame(finished) if finished else pd.DataFrame()

    # ------------------------------------------------------------------
    # Monte Carlo
    # ------------------------------------------------------------------

    def simulate(self, n: int = 10_000) -> dict:
        """
        Run n Monte Carlo simulations and return stage-reach probabilities per team.

        Probabilities for all known group-stage matches are batch-precomputed in a
        single model call, making the Monte Carlo loop fast (no per-match model calls).
        Knockout matchups use ELO-based win probabilities.
        """
        if not self._groups:
            self.load()

        self._precompute_probas()    # one batch call, fills self._proba_cache
        self._load_elo_ratings()     # for knockout fallback

        all_teams = [t for teams in self._groups.values() for t in teams]
        counts: dict[str, dict[str, int]] = {
            team: {s: 0 for s in STAGES} for team in all_teams
        }

        for _ in range(n):
            self._run_one(counts)

        return {
            team: {stage: counts[team][stage] / n for stage in STAGES}
            for team in all_teams
        }

    # ------------------------------------------------------------------
    # Pre-computation helpers (called once before the MC loop)
    # ------------------------------------------------------------------

    def _precompute_probas(self) -> None:
        """Batch-predict probabilities for all upcoming group-stage matches in one call."""
        self._proba_cache: dict[tuple, np.ndarray] = {}
        group_upcoming = [m for m in self._upcoming if m.get("tournament_stage") == "Group Stage"]
        if not group_upcoming:
            return
        from datetime import datetime
        rows = [{
            "home_team":  m["home_team"],
            "away_team":  m["away_team"],
            "match_date": m.get("match_date") or datetime.utcnow(),
            "season":     m.get("season") or 2026,
            "status": "SCHEDULED",
            "result": None,
            "home_team_score": None, "away_team_score": None,
            "home_team_xG": None, "away_team_xG": None,
            "over_2_5_goals": None, "btts": None,
            "referee": None,
            "home_team_yellow_cards": None, "away_team_yellow_cards": None,
            "home_team_red_cards": None, "away_team_red_cards": None,
            "opening_home_odds": None, "opening_draw_odds": None, "opening_away_odds": None,
            "is_neutral_venue": True,
            "tournament_stage": "Group Stage",
        } for m in group_upcoming]
        try:
            upcoming_df = pd.DataFrame(rows)
            preds = self.predictor.predict(
                upcoming_df, pd.DataFrame(), db=self.db, competition=self.competition_id
            )
            for pred in preds:
                key = (pred["home_team"], pred["away_team"])
                o = pred.get("outcome", pred)
                p = np.array([
                    o.get("home_win_prob", 0.40),
                    o.get("draw_prob", 0.28),
                    o.get("away_win_prob", 0.32),
                ])
                p = np.clip(p, 0.05, 0.90)
                self._proba_cache[key] = p / p.sum()
        except Exception:
            pass

    def _load_elo_ratings(self) -> None:
        """Load ELO ratings from DB for fast knockout probability estimation."""
        self._elo: dict[str, float] = {}
        try:
            from app.models import EloRating
            records = self.db.query(EloRating).all()
            self._elo = {r.team: r.elo for r in records}
        except Exception:
            pass

    def _elo_proba(self, home: str, away: str, neutral: bool = True) -> np.ndarray:
        """Fast ELO-based win/draw/loss probability (no model call)."""
        elo_h = self._elo.get(home, 1500.0)
        elo_a = self._elo.get(away, 1500.0)
        # Home advantage: 0 at neutral venues, +100 at home
        ha = 0.0 if neutral else 100.0
        p_home = 1.0 / (1.0 + 10.0 ** ((elo_a - elo_h - ha) / 400.0))
        p_away = 1.0 / (1.0 + 10.0 ** ((elo_h + ha - elo_a) / 400.0))
        p_draw = max(0.10, 1.0 - p_home - p_away)
        p = np.array([p_home, p_draw, p_away])
        p = np.clip(p, 0.05, 0.90)
        return p / p.sum()

    # ------------------------------------------------------------------
    # Single simulation pass
    # ------------------------------------------------------------------

    def _run_one(self, counts: dict) -> None:
        # Start with completed group-stage results
        sim_results: list[dict] = list(self._finished)

        # Simulate remaining group-stage matches
        for match in self._upcoming:
            if match["tournament_stage"] == "Group Stage":
                result = self._sample_result(
                    match["home_team"], match["away_team"], neutral=True
                )
                sim_results.append({**match, "result": result})

        # Build group standings
        standings = self._group_standings(sim_results)

        # Determine who qualifies
        qualifiers = self._determine_qualifiers(standings)
        for team in qualifiers:
            counts[team]["group"] += 1

        # Simulate knockout rounds
        bracket = self._build_r32_bracket(qualifiers, standings)
        self._simulate_knockout(bracket, counts)

    def _sample_result(self, home: str, away: str, neutral: bool = False) -> str:
        """Sample H/D/A from model probabilities."""
        proba = self._get_proba(home, away, neutral)
        return np.random.choice(["H", "D", "A"], p=proba)

    def _sample_knockout_winner(self, home: str, away: str) -> str:
        """For knockout: draw → 50/50 extra time/penalties."""
        proba = self._get_proba(home, away, neutral=True)
        # P(home advances) = P(H) + P(D)*0.5
        p_home = proba[0] + proba[1] * 0.5
        return home if np.random.random() < p_home else away

    def _get_proba(self, home: str, away: str, neutral: bool = False) -> np.ndarray:
        """Return [P(H), P(D), P(A)] — from precomputed cache, then ELO, then prior."""
        cache = getattr(self, "_proba_cache", {})
        if (home, away) in cache:
            return cache[(home, away)]
        # Knockout matchups: fast ELO-based estimate (no full model call)
        if hasattr(self, "_elo"):
            return self._elo_proba(home, away, neutral)
        return np.array([0.40, 0.28, 0.32])

    # ------------------------------------------------------------------
    # Group stage helpers
    # ------------------------------------------------------------------

    def _group_standings(self, results: list[dict]) -> dict[str, list[dict]]:
        """Returns {group_label: [{team, pts, gd, gf, ...}, ...]} sorted desc."""
        team_stats: dict[str, dict] = defaultdict(lambda: {
            "pts": 0, "gd": 0, "gf": 0, "group": "?"
        })

        # Assign teams to groups from self._groups
        team_to_group = {
            team: grp
            for grp, teams in self._groups.items()
            for team in teams
        }

        for r in results:
            if r.get("tournament_stage") not in ("Group Stage", None, ""):
                continue
            home, away = r["home_team"], r["away_team"]
            res = r.get("result")
            hs = r.get("home_team_score") or 0
            as_ = r.get("away_team_score") or 0

            for team in (home, away):
                team_stats[team]["group"] = team_to_group.get(team, "?")

            if res == "H":
                team_stats[home]["pts"] += 3
            elif res == "D":
                team_stats[home]["pts"] += 1
                team_stats[away]["pts"] += 1
            elif res == "A":
                team_stats[away]["pts"] += 3

            team_stats[home]["gf"] += hs
            team_stats[home]["gd"] += hs - as_
            team_stats[away]["gf"] += as_
            team_stats[away]["gd"] += as_ - hs

        # Group by group label and sort
        standings: dict[str, list[dict]] = defaultdict(list)
        for team, stats in team_stats.items():
            standings[stats["group"]].append({"team": team, **stats})

        for grp in standings:
            standings[grp].sort(key=lambda x: (-x["pts"], -x["gd"], -x["gf"]))

        return dict(standings)

    def _determine_qualifiers(self, standings: dict[str, list[dict]]) -> list[str]:
        """Top 2 from each group + best 8 third-place finishers."""
        qualifiers = []
        third_place = []

        for grp_teams in standings.values():
            if len(grp_teams) >= 2:
                qualifiers.append(grp_teams[0]["team"])
                qualifiers.append(grp_teams[1]["team"])
            if len(grp_teams) >= 3:
                third_place.append(grp_teams[2])

        # Best 8 third-place teams (by pts then gd then gf)
        third_place.sort(key=lambda x: (-x["pts"], -x["gd"], -x["gf"]))
        qualifiers += [t["team"] for t in third_place[:WC_FORMAT["best_third_qualify"]]]
        return qualifiers

    def _build_r32_bracket(self, qualifiers: list[str], standings: dict) -> list[tuple]:
        """Pair up R32 matches. Simple sequential pairing on 32 qualifiers."""
        teams = qualifiers[:32]
        # Pair 1v2, 3v4, etc. (simplified seeding)
        return [(teams[i], teams[i + 1]) for i in range(0, len(teams) - 1, 2)]

    # ------------------------------------------------------------------
    # Knockout simulation
    # ------------------------------------------------------------------

    def _simulate_knockout(self, bracket: list[tuple], counts: dict) -> None:
        # r32…final count teams that REACH each round (both participants of
        # every match in that round); winner is the champion. This matches the
        # API docstring and the frontend column labels (R32/R16/QF/SF/Final/Win).
        stage_labels = ["r32", "r16", "qf", "sf", "final"]
        current_round = bracket
        last_winners: list[str] = []

        for stage in stage_labels:
            if not current_round:
                break
            last_winners = []
            for home, away in current_round:
                counts[home][stage] += 1
                counts[away][stage] += 1
                last_winners.append(self._sample_knockout_winner(home, away))
            current_round = [
                (last_winners[i], last_winners[i + 1])
                for i in range(0, len(last_winners) - 1, 2)
            ]

        # The sole survivor of the Final is the tournament champion
        if last_winners:
            counts[last_winners[0]]["winner"] += 1
