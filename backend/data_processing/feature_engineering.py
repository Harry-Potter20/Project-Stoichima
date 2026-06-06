import pandas as pd
import numpy as np
from collections import defaultdict
from data_processing.team_normalizer import normalise_team_name


# ---------------------------------------------------------------------------
# xG features — two implementations: model-derived (preferred) and DB-column fallback
# ---------------------------------------------------------------------------

def _add_xg_features_db_fallback(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """Uses home_team_xG / away_team_xG columns already in the matches table."""
    df = df.sort_values("match_date").copy()
    team_xg_history: dict = defaultdict(list)
    home_xg_avg, away_xg_avg = [], []

    for _, row in df.iterrows():
        home_team = row["home_team"]
        away_team = row["away_team"]

        def _avg(history):
            recent = [m["xg"] for m in history[-window:] if m["xg"] is not None and not np.isnan(m["xg"])]
            return float(np.mean(recent)) if recent else 0.0

        home_xg_avg.append(_avg(team_xg_history[home_team]))
        away_xg_avg.append(_avg(team_xg_history[away_team]))

        team_xg_history[home_team].append({"xg": row.get("home_team_xG")})
        team_xg_history[away_team].append({"xg": row.get("away_team_xG")})

    df["home_xg_avg"] = home_xg_avg
    df["away_xg_avg"] = away_xg_avg
    df["home_shot_quality_avg"] = 0.0
    df["away_shot_quality_avg"] = 0.0
    return df


def _add_xg_features_model_derived(
    df: pd.DataFrame,
    shots_df: pd.DataFrame,
    xg_model,
    window: int = 5,
) -> pd.DataFrame:
    """
    Applies the own xG model to the shots table to derive:
      - home/away_xg_avg: mean total xG per match over last `window` matches
      - home/away_shot_quality_avg: mean per-shot xG over last `window` matches

    shots_df must contain columns: home_team, away_team, match_date (or home/away
    identifiers that align with df), player_team, x, y, shot_type, situation,
    last_action, result.

    If shots_df is empty or xg_model is None, falls back to DB columns.
    """
    if shots_df is None or shots_df.empty or xg_model is None:
        return _add_xg_features_db_fallback(df, window)

    shots_df = shots_df.copy()

    # Predict xG per shot
    shots_df["xg_pred"] = xg_model.predict_proba(shots_df)

    # Aggregate per (home_team, away_team, match_date) → xG totals per team per match
    shot_match_agg = (
        shots_df
        .groupby(["home_team", "away_team", "player_team"])["xg_pred"]
        .agg(total_xg="sum", shots="count", shot_quality="mean")
        .reset_index()
    )

    # Build per-team per-match series keyed by (team, home_team, away_team, date).
    # We match shots to matches by (home_team, away_team) — the same identifiers
    # used in the matches table (both normalised by the scraper).
    df = df.sort_values("match_date").copy()
    team_xg_history: dict = defaultdict(list)  # team → list of {xg, quality}

    home_xg_avg, away_xg_avg = [], []
    home_sq_avg, away_sq_avg = [], []

    for _, row in df.iterrows():
        home_team = row["home_team"]
        away_team = row["away_team"]

        def _rolling_avg(history, key, fallback=0.0):
            recent = [m[key] for m in history[-window:]]
            return float(np.mean(recent)) if recent else fallback

        home_xg_avg.append(_rolling_avg(team_xg_history[home_team], "xg"))
        away_xg_avg.append(_rolling_avg(team_xg_history[away_team], "xg"))
        home_sq_avg.append(_rolling_avg(team_xg_history[home_team], "quality"))
        away_sq_avg.append(_rolling_avg(team_xg_history[away_team], "quality"))

        # Find this match's shot aggregates
        mask = (
            (shot_match_agg["home_team"] == home_team) &
            (shot_match_agg["away_team"] == away_team)
        )
        match_shots = shot_match_agg[mask]

        for team in (home_team, away_team):
            t_mask = match_shots["player_team"] == team
            t_rows = match_shots[t_mask]
            if not t_rows.empty:
                team_xg_history[team].append({
                    "xg":     float(t_rows["total_xg"].iloc[0]),
                    "quality": float(t_rows["shot_quality"].iloc[0]),
                })
            else:
                # No shots in DB for this team in this match — use DB column fallback
                col = "home_team_xG" if team == home_team else "away_team_xG"
                xg_val = row.get(col)
                if xg_val is not None and not np.isnan(float(xg_val)):
                    team_xg_history[team].append({"xg": float(xg_val), "quality": 0.0})

    df["home_xg_avg"] = home_xg_avg
    df["away_xg_avg"] = away_xg_avg
    df["home_shot_quality_avg"] = home_sq_avg
    df["away_shot_quality_avg"] = away_sq_avg
    return df


STAGE_ENC = {
    "Group Stage": 0,
    "R32":         1,
    "R16":         2,
    "QF":          3,
    "SF":          4,
    "Third Place": 4,
    "Final":       5,
}


def _add_dc_features(df: pd.DataFrame, dist_model) -> pd.DataFrame:
    """
    Dixon-Coles H/D/A probabilities as XGBoost features.
    Falls back to uniform (1/3) for teams the DC model hasn't seen.
    """
    df = df.copy()
    dc_home = np.full(len(df), 1.0 / 3.0)
    dc_draw = np.full(len(df), 1.0 / 3.0)
    dc_away = np.full(len(df), 1.0 / 3.0)

    if dist_model is None:
        df["dc_home_prob"] = dc_home
        df["dc_draw_prob"] = dc_draw
        df["dc_away_prob"] = dc_away
        return df

    for i, (_, row) in enumerate(df.iterrows()):
        try:
            matrix = dist_model.predict_score_matrix(row["home_team"], row["away_team"])
            market = dist_model.market_1x2(matrix)
            dc_home[i] = market.get("H", 1.0 / 3.0)
            dc_draw[i] = market.get("D", 1.0 / 3.0)
            dc_away[i] = market.get("A", 1.0 / 3.0)
        except Exception:
            pass

    df["dc_home_prob"] = dc_home
    df["dc_draw_prob"] = dc_draw
    df["dc_away_prob"] = dc_away
    return df


def _add_shots_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Rolling shots and shots-on-target averages per team.
    Already in the DB from football-data.co.uk CSV imports — just not used as features.
    shot_accuracy = shots_on_target / shots (finishing quality proxy).
    """
    df = df.sort_values("match_date").copy()
    team_shots: dict = defaultdict(list)

    h_shots, a_shots = [], []
    h_sot, a_sot     = [], []
    h_acc, a_acc      = [], []

    def _avg(hist, key):
        vals = [m[key] for m in hist[-window:] if m[key] is not None]
        return float(np.mean(vals)) if vals else 0.0

    for _, row in df.iterrows():
        ht, at = row["home_team"], row["away_team"]

        h_shots.append(_avg(team_shots[ht], "shots_f"))
        a_shots.append(_avg(team_shots[at], "shots_f"))
        h_sot.append(_avg(team_shots[ht], "sot_f"))
        a_sot.append(_avg(team_shots[at], "sot_f"))

        def _acc(hist):
            recent = hist[-window:]
            s   = sum(m["shots_f"] or 0 for m in recent)
            sot = sum(m["sot_f"]   or 0 for m in recent)
            return sot / s if s > 0 else 0.3
        h_acc.append(_acc(team_shots[ht]))
        a_acc.append(_acc(team_shots[at]))

        hs = row.get("home_team_shots")
        as_ = row.get("away_team_shots")
        hsot = row.get("home_team_shots_on_target")
        asot = row.get("away_team_shots_on_target")
        if row.get("result") is not None:
            team_shots[ht].append({"shots_f": hs, "sot_f": hsot})
            team_shots[at].append({"shots_f": as_, "sot_f": asot})

    df["home_shots_avg"]    = h_shots
    df["away_shots_avg"]    = a_shots
    df["home_sot_avg"]      = h_sot
    df["away_sot_avg"]      = a_sot
    df["home_shot_acc"]     = h_acc
    df["away_shot_acc"]     = a_acc
    df["shots_diff"]        = np.array(h_shots) - np.array(a_shots)
    df["sot_diff"]          = np.array(h_sot) - np.array(a_sot)
    return df


def _add_corners_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Rolling average corners for/against per team.
    Useful for corners O/U market (expected total = home_corners_avg + away_corners_avg).
    Falls back to 5.0 / 4.0 (league average) when data is absent.
    """
    df = df.sort_values("match_date").copy()
    team_corners: dict = defaultdict(list)

    h_avg, a_avg = [], []

    def _avg(hist, key):
        vals = [m[key] for m in hist[-window:] if m[key] is not None]
        return float(np.mean(vals)) if vals else None

    for _, row in df.iterrows():
        ht, at = row["home_team"], row["away_team"]

        hca = _avg(team_corners[ht], "corners_f")
        aca = _avg(team_corners[at], "corners_f")
        h_avg.append(hca if hca is not None else 5.0)
        a_avg.append(aca if aca is not None else 4.0)

        hc = row.get("home_team_corners")
        ac = row.get("away_team_corners")
        if row.get("result") is not None:
            team_corners[ht].append({"corners_f": hc})
            team_corners[at].append({"corners_f": ac})

    df["home_corners_avg"] = h_avg
    df["away_corners_avg"] = a_avg
    df["expected_corners"] = np.array(h_avg) + np.array(a_avg)
    df["corner_diff"]      = np.array(h_avg) - np.array(a_avg)
    return df


def _add_odds_movement_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ratio of closing odds to opening odds — proxy for sharp-money movement.
    > 1.0 = odds drifted (market less confident), < 1.0 = odds shortened (sharp money in).
    Uses b365_home as closing where available; falls back to 1.0 (no movement).
    For upcoming matches there is no closing yet, so all values are 1.0.
    """
    df = df.copy()

    def _move(closing, opening):
        try:
            c, o = float(closing), float(opening)
            if c > 1 and o > 1:
                return round(c / o, 4)
        except (TypeError, ValueError):
            pass
        return 1.0

    df["home_odds_move"] = [_move(r.get("b365_home"), r.get("opening_home_odds")) for _, r in df.iterrows()]
    df["draw_odds_move"] = [_move(r.get("b365_draw"), r.get("opening_draw_odds")) for _, r in df.iterrows()]
    df["away_odds_move"] = [_move(r.get("b365_away"), r.get("opening_away_odds")) for _, r in df.iterrows()]
    # Biggest mover: which side took the most money?
    df["max_odds_move"] = df[["home_odds_move", "draw_odds_move", "away_odds_move"]].max(axis=1)
    return df


def _add_fixture_congestion(df: pd.DataFrame) -> pd.DataFrame:
    """
    Matches each team played in the 7 days prior to each fixture.
    High congestion → rotation risk, fatigue, reduced quality.

    Vectorised: for each team build a DatetimeIndex of all their match dates,
    then use searchsorted to count matches in the rolling 7-day window — O(n log n)
    instead of the previous O(n²) nested loop.
    """
    df = df.sort_values("match_date").copy()
    dates = pd.to_datetime(df["match_date"])

    # Build a sorted list of match dates per team (both home and away appearances).
    from collections import defaultdict
    team_dates: dict[str, list] = defaultdict(list)
    for d, ht, at in zip(dates, df["home_team"], df["away_team"]):
        team_dates[ht].append(d)
        team_dates[at].append(d)
    team_date_arrays = {t: np.array(sorted(ds), dtype="datetime64[ns]") for t, ds in team_dates.items()}

    window = np.timedelta64(7, "D")
    home_c, away_c = [], []
    for d, ht, at in zip(dates.values, df["home_team"].values, df["away_team"].values):
        d64 = np.datetime64(d, "ns")
        for team, out in ((ht, home_c), (at, away_c)):
            arr = team_date_arrays.get(team, np.array([], dtype="datetime64[ns]"))
            # Count dates in (d - 7d, d) — exclude d itself (prior matches only)
            lo = np.searchsorted(arr, d64 - window, side="left")
            hi = np.searchsorted(arr, d64, side="left")  # exclusive of current match
            out.append(int(hi - lo))

    df["home_matches_last_7d"] = home_c
    df["away_matches_last_7d"] = away_c
    df["congestion_diff"] = np.array(home_c, dtype=float) - np.array(away_c, dtype=float)
    return df


def _add_table_pressure(df: pd.DataFrame) -> pd.DataFrame:
    """
    Incremental league-table features derived from the match history within df.
    At each match date, computes per-team points, position and gaps to:
      - top 4 (European / promotion zone)
      - relegation (bottom 3)

    Falls back to neutral (0.0) when fewer than 5 teams have played.
    """
    df = df.sort_values("match_date").copy()
    pts_running: dict[str, int] = {}

    home_pos, away_pos = [], []
    home_gap_top4, away_gap_top4 = [], []
    home_gap_rel, away_gap_rel = [], []

    def _standings_snapshot(pts: dict[str, int]):
        if len(pts) < 5:
            return {}
        ranked = sorted(pts.items(), key=lambda x: -x[1])
        return {team: rank + 1 for rank, (team, _) in enumerate(ranked)}

    for _, row in df.iterrows():
        ht = row["home_team"]
        at = row["away_team"]

        snap = _standings_snapshot(pts_running)
        all_pts = sorted(pts_running.values(), reverse=True) if pts_running else []

        def _gap_top4(team):
            if len(all_pts) < 4 or team not in pts_running:
                return 0.0
            fourth_pts = all_pts[3] if len(all_pts) >= 4 else all_pts[-1]
            return float(pts_running[team] - fourth_pts)

        def _gap_relegation(team):
            if len(all_pts) < 4 or team not in pts_running:
                return 0.0
            relegation_pts = all_pts[-3] if len(all_pts) >= 3 else all_pts[0]
            return float(pts_running[team] - relegation_pts)

        home_pos.append(float(snap.get(ht, 0)))
        away_pos.append(float(snap.get(at, 0)))
        home_gap_top4.append(_gap_top4(ht))
        away_gap_top4.append(_gap_top4(at))
        home_gap_rel.append(_gap_relegation(ht))
        away_gap_rel.append(_gap_relegation(at))

        # Update pts for finished matches only
        result = row.get("result")
        if result == "H":
            pts_running[ht] = pts_running.get(ht, 0) + 3
            pts_running[at] = pts_running.get(at, 0)
        elif result == "D":
            pts_running[ht] = pts_running.get(ht, 0) + 1
            pts_running[at] = pts_running.get(at, 0) + 1
        elif result == "A":
            pts_running[ht] = pts_running.get(ht, 0)
            pts_running[at] = pts_running.get(at, 0) + 3
        else:
            pts_running.setdefault(ht, 0)
            pts_running.setdefault(at, 0)

    df["home_table_pos"]       = home_pos
    df["away_table_pos"]       = away_pos
    df["home_pts_gap_top4"]    = home_gap_top4
    df["away_pts_gap_top4"]    = away_gap_top4
    df["home_pts_gap_rel"]     = home_gap_rel
    df["away_pts_gap_rel"]     = away_gap_rel
    df["table_pos_diff"]       = np.array(home_pos, dtype=float) - np.array(away_pos, dtype=float)
    return df


def _add_closing_line_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Vig-removed implied probabilities from the Bet365 closing price.
    Bet365's final price is the sharpest freely-available signal — it
    reflects all sharp-money movement and is consistently the best
    single predictor in academic betting research.
    Falls back to 0.0 (model ignores via XGB gain) when odds absent.
    """
    df = df.copy()
    close_h, close_d, close_a = [], [], []
    for _, row in df.iterrows():
        ch = row.get("b365_home")
        cd = row.get("b365_draw")
        ca = row.get("b365_away")
        try:
            if ch and cd and ca and float(ch) > 1 and float(cd) > 1 and float(ca) > 1:
                raw   = [1 / float(ch), 1 / float(cd), 1 / float(ca)]
                total = sum(raw)
                close_h.append(raw[0] / total)
                close_d.append(raw[1] / total)
                close_a.append(raw[2] / total)
            else:
                close_h.append(0.0)
                close_d.append(0.0)
                close_a.append(0.0)
        except (TypeError, ValueError):
            close_h.append(0.0)
            close_d.append(0.0)
            close_a.append(0.0)
    df["mkt_close_home_prob"] = close_h
    df["mkt_close_draw_prob"] = close_d
    df["mkt_close_away_prob"] = close_a
    return df


def _add_promotion_relegation_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    Flags teams in their first season at this competition level.
    Newly-promoted sides regress heavily toward division average —
    their rolling form from the previous division is misleading.
    """
    df = df.copy()

    # Build set of (team, competition, season) occurrences
    team_comp_seasons: dict[tuple, set] = {}
    for _, row in df.iterrows():
        comp = row.get("competition", "")
        season = row.get("season")
        if season is None:
            continue
        for team in (row["home_team"], row["away_team"]):
            key = (team, comp)
            if key not in team_comp_seasons:
                team_comp_seasons[key] = set()
            team_comp_seasons[key].add(int(season))

    def _is_newly_promoted(team: str, comp: str, season) -> float:
        if season is None:
            return 0.0
        s = int(season)
        prev_seasons = team_comp_seasons.get((team, comp), set())
        return 1.0 if (s - 1) not in prev_seasons else 0.0

    home_np, away_np = [], []
    for _, row in df.iterrows():
        comp   = row.get("competition", "")
        season = row.get("season")
        home_np.append(_is_newly_promoted(row["home_team"], comp, season))
        away_np.append(_is_newly_promoted(row["away_team"], comp, season))

    df["home_newly_promoted"] = home_np
    df["away_newly_promoted"] = away_np
    return df


def _add_match_importance_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Continuous match-importance scores based on live table pressure.
    Teams fighting for the title or against relegation play differently —
    more cautious or more desperate — producing distinct statistical patterns.

    home/away_title_pressure:    pts gap to 1st place, clipped [0, 15], inverted
    home/away_relegation_danger: pts gap to safety threshold, clipped [0, 15], inverted
    match_importance:            sum of both sides' max(title, relegation) scores
    """
    df = df.copy()
    required = {"home_table_pos", "home_pts_gap_top4", "home_pts_gap_rel",
                "away_table_pos", "away_pts_gap_top4", "away_pts_gap_rel"}
    if not required.issubset(df.columns):
        for col in ["home_title_pressure", "away_title_pressure",
                    "home_relegation_danger", "away_relegation_danger",
                    "match_importance"]:
            df[col] = 0.0
        return df

    # pts_gap_top4 > 0  → above 4th place (not in a title race)
    # pts_gap_rel  > 0  → above relegation zone (safe)
    # We invert and normalise to [0, 1]: higher = more pressure
    CLIP = 15.0
    df["home_title_pressure"]     = (1.0 - (df["home_pts_gap_top4"].clip(upper=CLIP) / CLIP)).clip(0.0, 1.0)
    df["away_title_pressure"]     = (1.0 - (df["away_pts_gap_top4"].clip(upper=CLIP) / CLIP)).clip(0.0, 1.0)
    df["home_relegation_danger"]  = (1.0 - (df["home_pts_gap_rel"].clip(upper=CLIP) / CLIP)).clip(0.0, 1.0)
    df["away_relegation_danger"]  = (1.0 - (df["away_pts_gap_rel"].clip(upper=CLIP) / CLIP)).clip(0.0, 1.0)

    home_stake = df[["home_title_pressure", "home_relegation_danger"]].max(axis=1)
    away_stake = df[["away_title_pressure", "away_relegation_danger"]].max(axis=1)
    df["match_importance"] = (home_stake + away_stake) / 2.0

    # Zero out for rows where table hasn't formed yet (early season)
    no_table = df["home_table_pos"] == 0
    for col in ["home_title_pressure", "away_title_pressure",
                "home_relegation_danger", "away_relegation_danger", "match_importance"]:
        df.loc[no_table, col] = 0.0
    return df


def _add_steam_move_features(df: pd.DataFrame, threshold: float = 0.08) -> pd.DataFrame:
    """
    Binary steam-move flags: True when odds shortened by >= threshold
    (sharp money moved the line significantly toward one side).
    home_steam:  home odds shortened ≥ threshold  (strong home signal)
    away_steam:  away odds shortened ≥ threshold
    max_steam:   largest move across all three outcomes (volatility proxy)
    """
    df = df.copy()
    # odds_move < 1 means odds shortened; 1 - odds_move = fractional shortening
    hm = df.get("home_odds_move", pd.Series(1.0, index=df.index))
    dm = df.get("draw_odds_move",  pd.Series(1.0, index=df.index))
    am = df.get("away_odds_move",  pd.Series(1.0, index=df.index))

    df["home_steam"] = ((1.0 - hm) >= threshold).astype(float)
    df["draw_steam"] = ((1.0 - dm) >= threshold).astype(float)
    df["away_steam"] = ((1.0 - am) >= threshold).astype(float)
    df["max_steam"]  = (1.0 - df[["home_odds_move", "draw_odds_move", "away_odds_move"]]
                        .fillna(1.0).min(axis=1)).clip(lower=0.0)
    return df


def _apply_promotion_shrinkage(df: pd.DataFrame, shrink: float = 0.4) -> pd.DataFrame:
    """
    Pull newly-promoted teams' rolling attack/defense stats toward league average.
    Their stats from the previous (lower) division overstate / understate their true
    top-flight level. A shrinkage fraction of 0.4 means 40% regression to mean.

    Modifies home/away_goals_scored_avg and home/away_goals_conceded_avg in-place.
    Must be called AFTER _add_form_and_goals_features.
    """
    if "home_newly_promoted" not in df.columns:
        return df

    df = df.copy()
    # Compute league average from FINISHED matches only; upcoming rows have 0.0-imputed
    # rolling averages that would drag the median toward zero.
    finished_mask = df["status"] == "FINISHED"
    ref = df[finished_mask] if finished_mask.any() else df
    league_avg_scored   = ref["home_goals_scored_avg"].median()
    league_avg_conceded = ref["home_goals_conceded_avg"].median()

    for side in ("home", "away"):
        mask = df[f"{side}_newly_promoted"] == 1.0
        if not mask.any():
            continue
        df.loc[mask, f"{side}_goals_scored_avg"] = (
            (1 - shrink) * df.loc[mask, f"{side}_goals_scored_avg"]
            + shrink * league_avg_scored
        )
        df.loc[mask, f"{side}_goals_conceded_avg"] = (
            (1 - shrink) * df.loc[mask, f"{side}_goals_conceded_avg"]
            + shrink * league_avg_conceded
        )
    return df


def build_features(
    df: pd.DataFrame,
    shots_df: pd.DataFrame | None = None,
    xg_model=None,
    dist_model=None,
    is_international: bool = False,
    db=None,
) -> pd.DataFrame:
    form_window = 3 if is_international else 5
    h2h_window  = 20 if is_international else 10

    df = df.sort_values("match_date").copy()
    df["home_team"] = df["home_team"].apply(normalise_team_name)
    df["away_team"] = df["away_team"].apply(normalise_team_name)
    if "home_team_xG" not in df.columns:
        df["home_team_xG"] = np.nan
    if "away_team_xG" not in df.columns:
        df["away_team_xG"] = np.nan

    df = _add_form_and_goals_features(df, window=form_window)
    if shots_df is not None and xg_model is not None:
        df = _add_xg_features_model_derived(df, shots_df, xg_model, window=form_window)
    else:
        df = _add_xg_features_db_fallback(df, window=form_window)
    df = _add_h2h_features(df, window=h2h_window)
    df = _add_rest_features(df)
    df = _add_elo_features(df, db=db, is_international=is_international)
    df = _add_home_advantage_features(df, window=form_window)
    df = _add_season_progress(df)
    df = _add_opening_line_features(df)
    df = _add_referee_features(df, window=10)
    df = _add_dc_features(df, dist_model)
    df = _add_managerial_change_features(df, db=db)
    if not is_international:
        df = _add_table_pressure(df)
    df = _add_fixture_congestion(df)
    df = _add_shots_features(df, window=form_window)
    df = _add_corners_features(df, window=form_window)
    df = _add_odds_movement_features(df)
    df = _add_closing_line_features(df)
    df = _add_promotion_relegation_flags(df)
    df = _apply_promotion_shrinkage(df)
    df = _add_steam_move_features(df)
    if not is_international:
        df = _add_match_importance_features(df)

    # ELO-derived win probability: bounded [0,1], more informative than raw elo_diff
    df["elo_win_prob"] = 1.0 / (1.0 + np.power(10.0, -df["elo_diff"] / 400.0))

    # International-specific features
    if is_international:
        df["is_neutral_venue"] = df.get("is_neutral_venue", False).astype(float)
        df["tournament_stage_enc"] = (
            df.get("tournament_stage", pd.Series(dtype=str))
            .map(STAGE_ENC)
            .fillna(0)
            .astype(float)
        )

    # Derived matchup features
    df["home_away_form_diff"] = df["home_form"] - df["away_form"]
    df["home_away_goals_scored_diff"] = df["home_goals_scored_avg"] - df["away_goals_scored_avg"]
    df["home_away_goals_conceded_diff"] = df["home_goals_conceded_avg"] - df["away_goals_conceded_avg"]
    df["home_attack_vs_away_defense"] = df["home_goals_scored_avg"] - df["away_goals_conceded_avg"]
    df["away_attack_vs_home_defense"] = df["away_goals_scored_avg"] - df["home_goals_conceded_avg"]
    df["xg_diff"] = df["home_xg_avg"] - df["away_xg_avg"]
    df["shot_quality_diff"] = df["home_shot_quality_avg"] - df["away_shot_quality_avg"]
    return df

_RED_CARD_PPG_DISCOUNT = 0.7  # matches with a red card count 70% toward form


def _calculate_ppg(team: str, matches: list, window: int) -> float:
    if not matches:
        return 0.0
    recent_matches = matches[-window:]

    def _get_points(m) -> float:
        result = m.get("result") if hasattr(m, "get") else m["result"]
        if result is None:
            return 0.0
        if m.get("home_team") == team:
            pts = 3 if result == "H" else (1 if result == "D" else 0)
            had_red = bool((m.get("home_team_red_cards") or 0) + (m.get("away_team_red_cards") or 0))
        else:
            pts = 3 if result == "A" else (1 if result == "D" else 0)
            had_red = bool((m.get("home_team_red_cards") or 0) + (m.get("away_team_red_cards") or 0))
        weight = _RED_CARD_PPG_DISCOUNT if had_red else 1.0
        return pts * weight

    total_weighted_points = sum(_get_points(m) for m in recent_matches)
    return total_weighted_points / len(recent_matches)

def _current_streak(team: str, matches: list) -> tuple[int, int]:
    """
    Returns (win_streak, unbeaten_streak) leading up to the current match.
    A draw stops the win streak but not the unbeaten streak.
    A loss breaks both.
    """
    win_streak, unbeaten = 0, 0
    win_broken = False
    for m in reversed(matches):
        result = m.get("result") if hasattr(m, "get") else m["result"]
        if result is None:
            break
        is_home = m.get("home_team") == team
        won  = (result == "H" and is_home) or (result == "A" and not is_home)
        drew = result == "D"
        if won:
            if not win_broken:
                win_streak += 1
            unbeaten += 1
        elif drew:
            win_broken = True   # stop adding to win_streak, keep counting unbeaten
            unbeaten  += 1
        else:
            break               # loss ends both streaks
    return win_streak, unbeaten


def _add_form_and_goals_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    df = df.sort_values("match_date").copy()
    team_history = defaultdict(list)

    home_form, away_form = [], []
    home_scored, home_conceded = [], []
    away_scored, away_conceded = [], []
    home_win_streak, away_win_streak = [], []
    home_unbeaten,   away_unbeaten   = [], []
    home_draw_rate,  away_draw_rate  = [], []

    def _calculate_goal_averages(team: str, matches: list, window: int) -> tuple:
        if not matches:
            return 0.0, 0.0
        recent_matches = matches[-window:]
        scored_matches = [m for m in recent_matches if m["home_team_score"] is not None and m["away_team_score"] is not None]
        if not scored_matches:
            return 0.0, 0.0
        goals_scored   = sum(m["home_team_score"] if m["home_team"] == team else m["away_team_score"] for m in scored_matches)
        goals_conceded = sum(m["away_team_score"] if m["home_team"] == team else m["home_team_score"] for m in scored_matches)
        return goals_scored / len(scored_matches), goals_conceded / len(scored_matches)

    for _, row in df.iterrows():
        home_team = row["home_team"]
        away_team = row["away_team"]

        home_form.append(_calculate_ppg(home_team, team_history[home_team], window))
        away_form.append(_calculate_ppg(away_team, team_history[away_team], window))

        h_scored, h_conceded = _calculate_goal_averages(home_team, team_history[home_team], window)
        a_scored, a_conceded = _calculate_goal_averages(away_team, team_history[away_team], window)
        home_scored.append(h_scored)
        home_conceded.append(h_conceded)
        away_scored.append(a_scored)
        away_conceded.append(a_conceded)

        hw, hu = _current_streak(home_team, team_history[home_team])
        aw, au = _current_streak(away_team, team_history[away_team])
        home_win_streak.append(float(hw))
        away_win_streak.append(float(aw))
        home_unbeaten.append(float(hu))
        away_unbeaten.append(float(au))

        def _draw_rate(matches, w):
            recent = matches[-w:]
            if not recent:
                return 0.25
            draws = sum(1 for m in recent if m.get("result") == "D")
            return draws / len(recent)

        home_draw_rate.append(_draw_rate(team_history[home_team], window))
        away_draw_rate.append(_draw_rate(team_history[away_team], window))

        team_history[home_team].append(row)
        team_history[away_team].append(row)

    df["home_form"] = home_form
    df["away_form"] = away_form
    df["home_goals_scored_avg"]   = home_scored
    df["home_goals_conceded_avg"] = home_conceded
    df["away_goals_scored_avg"]   = away_scored
    df["away_goals_conceded_avg"] = away_conceded
    df["home_win_streak"]  = home_win_streak
    df["away_win_streak"]  = away_win_streak
    df["home_unbeaten"]    = home_unbeaten
    df["away_unbeaten"]    = away_unbeaten
    df["streak_diff"]      = np.array(home_win_streak) - np.array(away_win_streak)
    df["home_draw_rate"]   = home_draw_rate
    df["away_draw_rate"]   = away_draw_rate
    df["combined_draw_rate"] = np.array(home_draw_rate) + np.array(away_draw_rate)
    return df

_H2H_DECAY = 0.82   # each older H2H match is worth 18% less than the next


def _add_h2h_features(df: pd.DataFrame, window: int = 10) -> pd.DataFrame:
    df = df.sort_values("match_date").copy()
    h2h_history = defaultdict(list)
    h2h_win_rates, h2h_draw_rates, h2h_goals_avgs = [], [], []

    for _, row in df.iterrows():
        home_team = row["home_team"]
        away_team = row["away_team"]
        key = tuple(sorted([home_team, away_team]))
        previous = h2h_history[key][-window:]

        if not previous:
            h2h_win_rates.append(0.5)
            h2h_draw_rates.append(0.25)
            h2h_goals_avgs.append(2.5)
        else:
            n = len(previous)
            # Exponential decay: most recent meeting has weight 1.0, k-th back has _H2H_DECAY^k
            weights = np.array([_H2H_DECAY ** (n - 1 - i) for i in range(n)], dtype=float)
            weights /= weights.sum()

            home_wins = np.array([
                1.0 if (m.get("home_team") == home_team and m.get("result") == "H")
                     or (m.get("away_team") == home_team and m.get("result") == "A")
                else 0.0
                for m in previous
            ])
            draws = np.array([1.0 if m.get("result") == "D" else 0.0 for m in previous])
            goals = np.array(
                [(m.get("home_team_score") or 0) + (m.get("away_team_score") or 0)
                 for m in previous], dtype=float
            )
            h2h_win_rates.append(float(np.dot(weights, home_wins)))
            h2h_draw_rates.append(float(np.dot(weights, draws)))
            h2h_goals_avgs.append(float(np.dot(weights, goals)))

        h2h_history[key].append(row)

    df["h2h_home_win_rate"] = h2h_win_rates
    df["h2h_draw_rate"] = h2h_draw_rates
    df["h2h_goals_avg"] = h2h_goals_avgs
    return df


def _add_elo_features(
    df: pd.DataFrame,
    db=None,
    is_international: bool = False,
) -> pd.DataFrame:
    """
    Look up home_elo / away_elo from the elo_ratings table.
    Falls back to 1500 when a team has no record.
    Also adds elo_diff = home_elo - away_elo.
    """
    if db is None:
        df["home_elo"]  = 1500.0
        df["away_elo"]  = 1500.0
        df["elo_diff"]  = 0.0
        return df

    try:
        from app.models import EloRating
        records = db.query(EloRating).all()
        if is_international:
            elo_map = {r.team: r.elo for r in records if r.competition is None}
        else:
            elo_map = {r.team: r.elo for r in records if r.competition is not None}

        df = df.copy()
        df["home_elo"] = df["home_team"].map(elo_map).fillna(1500.0)
        df["away_elo"] = df["away_team"].map(elo_map).fillna(1500.0)
        df["elo_diff"] = df["home_elo"] - df["away_elo"]
    except Exception:
        df["home_elo"] = 1500.0
        df["away_elo"] = 1500.0
        df["elo_diff"] = 0.0
    return df


def _add_home_advantage_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Per-team rolling home advantage coefficient:
      home_ha_score  = home team's PPG at home - home team's PPG away (last `window` home/away games)
      away_ha_score  = away team's same metric (negative = poor travellers)
    Captures stadium fortress / road woes that generic form misses.
    """
    df = df.sort_values("match_date").copy()
    home_games: dict = defaultdict(list)   # team → list of {pts, venue}
    away_games: dict = defaultdict(list)

    ha_home, ha_away = [], []

    def _ppg(games: list, venue: str) -> float:
        relevant = [g["pts"] for g in games[-window * 2:] if g["venue"] == venue]
        return float(np.mean(relevant)) if relevant else 1.0  # neutral prior

    for _, row in df.iterrows():
        home_t = row["home_team"]
        away_t = row["away_team"]

        h_home_ppg = _ppg(home_games[home_t], "H")
        h_away_ppg = _ppg(home_games[home_t], "A")
        a_home_ppg = _ppg(away_games[away_t], "H")
        a_away_ppg = _ppg(away_games[away_t], "A")

        ha_home.append(h_home_ppg - h_away_ppg)
        ha_away.append(a_home_ppg - a_away_ppg)

        if row.get("result") is not None and row.get("home_team_score") is not None:
            pts_h = 3 if row["result"] == "H" else (1 if row["result"] == "D" else 0)
            pts_a = 3 if row["result"] == "A" else (1 if row["result"] == "D" else 0)
            home_games[home_t].append({"pts": pts_h, "venue": "H"})
            home_games[away_t].append({"pts": pts_a, "venue": "A"})
            away_games[home_t].append({"pts": pts_h, "venue": "H"})
            away_games[away_t].append({"pts": pts_a, "venue": "A"})

    df["home_ha_score"] = ha_home
    df["away_ha_score"] = ha_away
    return df


def _add_season_progress(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fraction of the season elapsed at the time of the match (0.0 → 1.0).
    Captures end-of-season pressure, title run-ins, relegation battles.
    """
    df = df.sort_values("match_date").copy()
    progress = []
    for _, row in df.iterrows():
        season = row.get("season")
        date   = pd.to_datetime(row["match_date"])
        if season:
            season_start = pd.Timestamp(f"{season}-07-01")
            season_end   = pd.Timestamp(f"{season + 1}-06-30")
            total_days   = (season_end - season_start).days
            elapsed      = (date - season_start).days
            frac = float(np.clip(elapsed / total_days, 0.0, 1.0))
        else:
            frac = 0.5
        progress.append(frac)
    df["season_progress"] = progress
    return df


def _add_opening_line_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Vig-removed implied probabilities from the opening bookmaker line.
    The opening line is the market's first-available prediction before sharp
    money moves it — it's strong signal even after vig removal.
    Returns mkt_open_home_prob, mkt_open_draw_prob, mkt_open_away_prob.
    Falls back to 0.0 (model ignores via XGB gain) when odds absent.
    """
    df = df.copy()
    open_h, open_d, open_a = [], [], []
    for _, row in df.iterrows():
        oh = row.get("opening_home_odds")
        od = row.get("opening_draw_odds")
        oa = row.get("opening_away_odds")
        try:
            if oh and od and oa and float(oh) > 1 and float(od) > 1 and float(oa) > 1:
                raw = [1/float(oh), 1/float(od), 1/float(oa)]
                total = sum(raw)
                open_h.append(raw[0] / total)
                open_d.append(raw[1] / total)
                open_a.append(raw[2] / total)
            else:
                open_h.append(0.0)
                open_d.append(0.0)
                open_a.append(0.0)
        except (TypeError, ValueError):
            open_h.append(0.0)
            open_d.append(0.0)
            open_a.append(0.0)
    df["mkt_open_home_prob"] = open_h
    df["mkt_open_draw_prob"] = open_d
    df["mkt_open_away_prob"] = open_a
    return df


def _add_referee_features(df: pd.DataFrame, window: int = 10) -> pd.DataFrame:
    """
    Rolling referee statistics over last `window` matches officiated.
    Features:
      ref_avg_yellow — average yellow cards total per game
      ref_avg_red    — average red cards total per game
      ref_home_win_rate — fraction of matches officiated that the home team won
                          (captures referee bias and game-management style)
    Falls back to league averages (2.5 yellows, 0.1 reds, 0.45 HWR) when unknown.
    """
    df = df.sort_values("match_date").copy()
    ref_history: dict = defaultdict(list)
    r_yellow, r_red, r_hwr = [], [], []

    for _, row in df.iterrows():
        ref = row.get("referee")
        hist = ref_history[ref][-window:] if ref else []
        if hist:
            r_yellow.append(float(np.mean([h["yellows"] for h in hist])))
            r_red.append(float(np.mean([h["reds"] for h in hist])))
            r_hwr.append(float(np.mean([h["home_won"] for h in hist])))
        else:
            r_yellow.append(2.5)
            r_red.append(0.1)
            r_hwr.append(0.45)

        if ref and row.get("result") is not None:
            hy = row.get("home_team_yellow_cards") or 0
            ay = row.get("away_team_yellow_cards") or 0
            hr = row.get("home_team_red_cards") or 0
            ar = row.get("away_team_red_cards") or 0
            ref_history[ref].append({
                "yellows":  hy + ay,
                "reds":     hr + ar,
                "home_won": 1 if row["result"] == "H" else 0,
            })

    df["ref_avg_yellow"]    = r_yellow
    df["ref_avg_red"]       = r_red
    df["ref_home_win_rate"] = r_hwr
    return df


def _add_managerial_change_features(df: pd.DataFrame, db=None) -> pd.DataFrame:
    """
    Days since last managerial change for each team.
    New managers show a short-term bounce (~0-30 days) then regression.
    Falls back to 365 (no recent change) when DB unavailable.
    """
    df = df.copy()
    if db is None:
        df["home_days_since_mgr_change"] = 365.0
        df["away_days_since_mgr_change"] = 365.0
        return df

    try:
        from app.models import ManagerialChange
        changes = db.query(ManagerialChange).all()
        # latest change per team
        latest: dict[str, pd.Timestamp] = {}
        for c in changes:
            t = normalise_team_name(c.team)
            dt = pd.Timestamp(c.change_date)
            if t not in latest or dt > latest[t]:
                latest[t] = dt
    except Exception:
        latest = {}

    home_days, away_days = [], []
    for _, row in df.iterrows():
        match_date = pd.to_datetime(row["match_date"])
        for team, out_list in [(row["home_team"], home_days), (row["away_team"], away_days)]:
            if team in latest:
                delta = (match_date - latest[team]).days
                out_list.append(float(np.clip(delta, 0, 365)))
            else:
                out_list.append(365.0)

    df["home_days_since_mgr_change"] = home_days
    df["away_days_since_mgr_change"] = away_days
    return df


def _add_rest_features(df: pd.DataFrame, cap_days: int = 14) -> pd.DataFrame:
    """Days since last match for each team — capped to avoid outlier season breaks."""
    df = df.sort_values("match_date").copy()
    last_match: dict[str, pd.Timestamp] = {}
    home_rest, away_rest = [], []

    for _, row in df.iterrows():
        home_team = row["home_team"]
        away_team = row["away_team"]
        match_date = pd.to_datetime(row["match_date"])

        def _days(team):
            if team in last_match:
                return min((match_date - last_match[team]).days, cap_days)
            return cap_days  # unknown → assume well-rested

        home_rest.append(float(_days(home_team)))
        away_rest.append(float(_days(away_team)))
        last_match[home_team] = match_date
        last_match[away_team] = match_date

    df["home_days_rest"] = home_rest
    df["away_days_rest"] = away_rest
    df["rest_advantage"] = df["home_days_rest"] - df["away_days_rest"]
    return df
