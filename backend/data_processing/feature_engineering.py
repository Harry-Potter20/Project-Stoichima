import pandas as pd
import numpy as np
from collections import defaultdict
from data_processing.team_normalizer import normalise_team_name

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("match_date").copy()
    df["home_team"] = df["home_team"].apply(normalise_team_name)
    df["away_team"] = df["away_team"].apply(normalise_team_name)
    df = _add_form_and_goals_features(df)
    df = _add_h2h_features(df)

    # Derived matchup features
    df["home_away_form_diff"] = df["home_form"] - df["away_form"]
    df["home_away_goals_scored_diff"] = df["home_goals_scored_avg"] - df["away_goals_scored_avg"]
    df["home_away_goals_conceded_diff"] = df["home_goals_conceded_avg"] - df["away_goals_conceded_avg"]
    df["home_attack_vs_away_defense"] = df["home_goals_scored_avg"] - df["away_goals_conceded_avg"]
    df["away_attack_vs_home_defense"] = df["away_goals_scored_avg"] - df["home_goals_conceded_avg"]
    return df

def _calculate_ppg(team: str, matches: list, window: int) -> float:
    if not matches:
        return 0.0
    # Consider only the last 'window' matches
    recent_matches = matches[-window:]
    def _get_points(m: dict) -> int:
        result = m["result"]
        if m["home_team"] == team:
            # team was at home
            if result == "H":
                return 3
            elif result == "D":
                return 1
            else:
                return 0 
        else:
            # team was away
            if result == "A":
                return 3
            elif result == "D":
                return 1
            else:
                return 0

    total_points = sum(_get_points(m) for m in recent_matches)
    return total_points / len(recent_matches)

def _add_form_and_goals_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    df = df.sort_values("match_date").copy()
    team_history = defaultdict(list)
    
    home_form, away_form = [], []
    home_scored, home_conceded = [], []
    away_scored, away_conceded = [], []

    def _calculate_goal_averages(team: str, matches: list, window: int) -> tuple:
        if not matches:
            return 0.0, 0.0
        recent_matches = matches[-window:]
        # filter out matches with no scores
        scored_matches = [m for m in recent_matches if m["home_team_score"] is not None and m["away_team_score"] is not None]
        if not scored_matches:
            return 0.0, 0.0
        goals_scored = sum(m["home_team_score"] if m["home_team"] == team else m["away_team_score"] for m in scored_matches)
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
        
        team_history[home_team].append(row)
        team_history[away_team].append(row)
    
    df["home_form"] = home_form
    df["away_form"] = away_form
    df["home_goals_scored_avg"] = home_scored
    df["home_goals_conceded_avg"] = home_conceded
    df["away_goals_scored_avg"] = away_scored
    df["away_goals_conceded_avg"] = away_conceded
    return df

def _add_h2h_features(df: pd.DataFrame, window: int = 10) -> pd.DataFrame:
    df = df.sort_values("match_date").copy()
    h2h_history = defaultdict(list)
    h2h_rates = []

    for _, row in df.iterrows():
        home_team = row["home_team"]
        away_team = row["away_team"]
        key = tuple(sorted([home_team, away_team]))

        # calculate from history BEFORE adding current match
        previous = h2h_history[key]
        if not previous:
            h2h_rates.append(0.5)
        else:
            home_wins = sum(1 for m in previous if (m["home_team"] == home_team and m["result"] == "H") or (m["away_team"] == home_team and m["result"] == "A"))
            h2h_rates.append(home_wins / len(previous))

        h2h_history[key].append(row)

    df["h2h_home_win_rate"] = h2h_rates
    return df
