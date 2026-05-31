"""
Dixon-Coles Poisson model for goal scoring.

Fits attack/defense/home-advantage parameters per team using MLE with
exponential time decay. Produces a full scoreline probability matrix from
which all betting markets are derived consistently.
"""
import numpy as np
import pandas as pd
import joblib
import os
from scipy.optimize import minimize
from scipy.stats import poisson


XI = 0.0018  # time-decay constant (half-life ≈ 1 season)
MAX_GOALS = 8  # matrix dimension: 0..MAX_GOALS inclusive


def _dc_correction(home_goals: int, away_goals: int, lambda_h: float, lambda_a: float, rho: float) -> float:
    """Dixon-Coles correction for low-scoring cells."""
    if home_goals == 0 and away_goals == 0:
        return 1 - lambda_h * lambda_a * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + lambda_a * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + lambda_h * rho
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


class GoalsDistributionModel:
    def __init__(self):
        self.attack: dict[str, float] = {}
        self.defense: dict[str, float] = {}
        self.home_advantage: float = 0.0
        self.rho: float = 0.0  # Dixon-Coles low-score correction
        self.teams: list[str] = []

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame):
        """
        df must have columns: home_team, away_team, home_team_score,
        away_team_score, match_date. Only FINISHED rows (non-null scores).
        """
        df = df.dropna(subset=["home_team_score", "away_team_score"]).copy()
        df["match_date"] = pd.to_datetime(df["match_date"])
        t_max = df["match_date"].max()
        df["weight"] = np.exp(-XI * (t_max - df["match_date"]).dt.days)

        self.teams = sorted(set(df["home_team"]) | set(df["away_team"]))
        n = len(self.teams)
        team_idx = {t: i for i, t in enumerate(self.teams)}

        def _neg_log_likelihood(params):
            attack  = params[:n]
            defense = params[n:2*n]
            home_adv = params[2*n]
            rho      = params[2*n + 1]

            ll = 0.0
            for _, row in df.iterrows():
                hi = team_idx[row["home_team"]]
                ai = team_idx[row["away_team"]]
                hg = int(row["home_team_score"])
                ag = int(row["away_team_score"])
                w  = row["weight"]

                lh = np.exp(attack[hi] + defense[ai] + home_adv)
                la = np.exp(attack[ai] + defense[hi])

                correction = _dc_correction(hg, ag, lh, la, rho)
                if correction <= 0:
                    return 1e9

                ll += w * (
                    np.log(correction)
                    + poisson.logpmf(hg, lh)
                    + poisson.logpmf(ag, la)
                )
            return -ll

        # Initialise: attack/defense ~ 0, home_adv ~ 0.3, rho ~ -0.1
        x0 = np.concatenate([
            np.zeros(n),    # attack
            np.zeros(n),    # defense
            [0.3],          # home_advantage
            [-0.1],         # rho
        ])

        # Pre-extract arrays for vectorised loss (avoids slow iterrows in MLE loop)
        home_idx = np.array([team_idx[t] for t in df["home_team"]])
        away_idx = np.array([team_idx[t] for t in df["away_team"]])
        hg_arr   = df["home_team_score"].values.astype(int)
        ag_arr   = df["away_team_score"].values.astype(int)
        w_arr    = df["weight"].values

        # Fix first team's attack to 0 for identifiability (reference team).
        # Free params: attack[1..n-1], defense[0..n-1], home_adv, rho
        def _neg_ll_fixed(params):
            attack_free = np.empty(n)
            attack_free[0] = 0.0
            attack_free[1:] = params[:n - 1]
            defense  = params[n - 1:2 * n - 1]
            home_adv = params[2 * n - 1]
            rho      = params[2 * n]

            lh = np.exp(attack_free[home_idx] + defense[away_idx] + home_adv)
            la = np.exp(attack_free[away_idx] + defense[home_idx])

            # Vectorised Poisson log-PMF
            ll_h = hg_arr * np.log(lh) - lh
            ll_a = ag_arr * np.log(la) - la

            # Dixon-Coles correction per match (vectorised)
            corr = np.ones(len(df))
            m00 = (hg_arr == 0) & (ag_arr == 0)
            m10 = (hg_arr == 1) & (ag_arr == 0)
            m01 = (hg_arr == 0) & (ag_arr == 1)
            m11 = (hg_arr == 1) & (ag_arr == 1)
            corr[m00] = 1 - lh[m00] * la[m00] * rho
            corr[m10] = 1 + la[m10] * rho
            corr[m01] = 1 + lh[m01] * rho
            corr[m11] = 1 - rho

            if np.any(corr <= 0):
                return 1e9

            ll = w_arr * (np.log(corr) + ll_h + ll_a)
            return -np.sum(ll)

        x0_fixed = np.concatenate([
            np.zeros(n - 1),  # attack[1..n-1]
            np.zeros(n),      # defense
            [0.3],            # home_advantage
            [-0.1],           # rho
        ])

        result = minimize(
            _neg_ll_fixed,
            x0_fixed,
            method="L-BFGS-B",
            options={"maxiter": 300, "ftol": 1e-9},
        )

        params = result.x
        attack_full = np.empty(n)
        attack_full[0] = 0.0
        attack_full[1:] = params[:n - 1]
        defense = params[n - 1:2 * n - 1]
        self.attack         = {t: float(attack_full[i]) for t, i in team_idx.items()}
        self.defense        = {t: float(defense[i])     for t, i in team_idx.items()}
        self.home_advantage = float(params[2 * n - 1])
        self.rho            = float(params[2 * n])

    # ------------------------------------------------------------------
    # Score matrix
    # ------------------------------------------------------------------

    def predict_score_matrix(
        self, home_team: str, away_team: str, neutral_venue: bool = False
    ) -> np.ndarray:
        """
        Returns a (MAX_GOALS+1, MAX_GOALS+1) matrix where entry [i, j] is
        P(home scores i, away scores j). Falls back to league-average
        parameters for unknown teams.
        When neutral_venue=True, home_advantage is zeroed out.
        """
        avg_attack  = float(np.mean(list(self.attack.values())))  if self.attack  else 0.0
        avg_defense = float(np.mean(list(self.defense.values()))) if self.defense else 0.0

        a_h = self.attack.get(home_team,  avg_attack)
        d_h = self.defense.get(home_team, avg_defense)
        a_a = self.attack.get(away_team,  avg_attack)
        d_a = self.defense.get(away_team, avg_defense)

        home_adv = 0.0 if neutral_venue else self.home_advantage
        lambda_h = np.exp(a_h + d_a + home_adv)
        lambda_a = np.exp(a_a + d_h)

        matrix = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
        for i in range(MAX_GOALS + 1):
            for j in range(MAX_GOALS + 1):
                matrix[i, j] = (
                    _dc_correction(i, j, lambda_h, lambda_a, self.rho)
                    * poisson.pmf(i, lambda_h)
                    * poisson.pmf(j, lambda_a)
                )
        matrix /= matrix.sum()  # renormalise after DC correction
        return matrix

    def get_lambdas(
        self, home_team: str, away_team: str, neutral_venue: bool = False
    ) -> tuple[float, float]:
        """Returns (lambda_home, lambda_away) — the Poisson rate parameters (= expected goals)."""
        avg_attack  = float(np.mean(list(self.attack.values())))  if self.attack  else 0.0
        avg_defense = float(np.mean(list(self.defense.values()))) if self.defense else 0.0
        a_h = self.attack.get(home_team,  avg_attack)
        d_h = self.defense.get(home_team, avg_defense)
        a_a = self.attack.get(away_team,  avg_attack)
        d_a = self.defense.get(away_team, avg_defense)
        home_adv = 0.0 if neutral_venue else self.home_advantage
        return float(np.exp(a_h + d_a + home_adv)), float(np.exp(a_a + d_h))

    # ------------------------------------------------------------------
    # Market derivations
    # ------------------------------------------------------------------

    def market_1x2(self, matrix: np.ndarray) -> dict:
        home = float(np.sum(np.tril(matrix, -1)))
        draw = float(np.sum(np.diag(matrix)))
        away = float(np.sum(np.triu(matrix, 1)))
        return {"H": home, "D": draw, "A": away}

    def market_over_under(self, matrix: np.ndarray, line: float) -> dict:
        total = np.arange(MAX_GOALS + 1)[:, None] + np.arange(MAX_GOALS + 1)[None, :]
        over  = float(np.sum(matrix[total > line]))
        under = float(np.sum(matrix[total <= line]))
        return {"over": over, "under": under}

    def market_btts(self, matrix: np.ndarray) -> dict:
        yes = float(np.sum(matrix[1:, 1:]))
        no  = 1.0 - yes
        return {"yes": yes, "no": no}

    def market_double_chance(self, matrix: np.ndarray) -> dict:
        m = self.market_1x2(matrix)
        return {
            "1X": round(m["H"] + m["D"], 6),
            "X2": round(m["D"] + m["A"], 6),
            "12": round(m["H"] + m["A"], 6),
        }

    def market_asian_handicap(self, matrix: np.ndarray, line: float) -> dict:
        """
        line is the handicap applied to the HOME team.
        Negative line = home gives goals (e.g. -0.5 → home must win to cover).
        Positive line = home receives goals (e.g. +0.5 → home wins if not losing).

        Home bet wins when: home_goals + line > away_goals
                        i.e. home_goals - away_goals > -line
        """
        diff = (np.arange(MAX_GOALS + 1)[:, None]
                - np.arange(MAX_GOALS + 1)[None, :])  # home - away

        threshold = -line  # the diff must exceed this for home to win

        if line % 1 != 0:
            # Half-ball: no push possible
            home_win = float(np.sum(matrix[diff > threshold]))
        else:
            # Whole-ball: quarter-ball split at threshold ± 0.5
            home_win = (
                float(np.sum(matrix[diff > threshold + 0.5]))
                + float(np.sum(matrix[diff > threshold - 0.5]))
            ) / 2

        away_win = 1.0 - home_win
        return {"home": round(home_win, 6), "away": round(away_win, 6)}

    def market_dnb(self, matrix: np.ndarray) -> dict:
        """
        Draw No Bet: stake returned on a draw.
        Effective win probability is conditioned on a result (non-draw).
        """
        m = self.market_1x2(matrix)
        non_draw = m["H"] + m["A"]
        if non_draw < 1e-9:
            return {"home": 0.5, "away": 0.5}
        return {
            "home": round(m["H"] / non_draw, 6),
            "away": round(m["A"] / non_draw, 6),
        }

    def market_wbtts(self, matrix: np.ndarray) -> dict:
        """
        Win & Both Teams To Score.
        home: P(home wins AND both score)
        away: P(away wins AND both score)
        """
        home_win_btts = float(np.sum(matrix[1:, 1:][
            np.tril(np.ones((MAX_GOALS, MAX_GOALS), dtype=bool), -1)
        ]))
        away_win_btts = float(np.sum(matrix[1:, 1:][
            np.triu(np.ones((MAX_GOALS, MAX_GOALS), dtype=bool), 1)
        ]))
        return {
            "home": round(home_win_btts, 6),
            "away": round(away_win_btts, 6),
            "either": round(home_win_btts + away_win_btts, 6),
        }

    def market_correct_score(self, matrix: np.ndarray, top_n: int = 10) -> list[dict]:
        scores = []
        for i in range(MAX_GOALS + 1):
            for j in range(MAX_GOALS + 1):
                scores.append({
                    "home_goals": i,
                    "away_goals": j,
                    "probability": round(float(matrix[i, j]), 6),
                })
        return sorted(scores, key=lambda x: x["probability"], reverse=True)[:top_n]

    def all_markets(self, home_team: str, away_team: str, neutral_venue: bool = False) -> dict:
        """Convenience: compute everything in one call."""
        matrix = self.predict_score_matrix(home_team, away_team, neutral_venue=neutral_venue)
        return {
            "1x2":            self.market_1x2(matrix),
            "btts":           self.market_btts(matrix),
            "dnb":            self.market_dnb(matrix),
            "wbtts":          self.market_wbtts(matrix),
            "double_chance":  self.market_double_chance(matrix),
            "over_under": {
                "1.5": self.market_over_under(matrix, 1.5),
                "2.5": self.market_over_under(matrix, 2.5),
                "3.5": self.market_over_under(matrix, 3.5),
                "4.5": self.market_over_under(matrix, 4.5),
            },
            "asian_handicap": {
                str(line): self.market_asian_handicap(matrix, line)
                for line in [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0]
            },
            "correct_score": self.market_correct_score(matrix),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, file_path: str):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        joblib.dump(self, file_path)

    @classmethod
    def load(cls, file_path: str) -> "GoalsDistributionModel":
        return joblib.load(file_path)
