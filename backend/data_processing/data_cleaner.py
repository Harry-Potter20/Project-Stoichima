"""
Transforms raw API responses into clean, consistent DataFrames.
This is step 1 before feature engineering.
"""
import pandas as pd


def clean_matches(raw_matches: list[dict]) -> pd.DataFrame:
    """
    Flatten the nested match JSON from football-data.org into a flat DataFrame.
    
    Raw match has shape like:
      { 'id': 1, 'utcDate': '...', 'homeTeam': {'name': '...'}, 
        'score': {'fullTime': {'home': 2, 'away': 1}}, ... }
    """
    rows = []
    for m in raw_matches:
        score = m.get("score", {}).get("fullTime", {})
        rows.append({
            "match_id":    m["id"],
            "date":        pd.to_datetime(m["utcDate"]),
            "competition": m["competition"]["code"],
            "season":      m["season"]["startDate"][:4],
            "home_team":   m["homeTeam"]["name"],
            "away_team":   m["awayTeam"]["name"],
            "home_goals":  score.get("home"),
            "away_goals":  score.get("away"),
            "status":      m["status"],  # FINISHED, SCHEDULED, etc.
        })

    df = pd.DataFrame(rows)

    # Only keep completed matches for training
    df = df[df["status"] == "FINISHED"].copy()

    # Derive result from home team's perspective
    df["result"] = df.apply(_get_result, axis=1)

    # Total goals (for Over/Under model)
    df["total_goals"] = df["home_goals"] + df["away_goals"]
    df["over_2_5"] = (df["total_goals"] > 2.5).astype(int)

    return df.reset_index(drop=True)


def _get_result(row) -> str:
    """H = home win, D = draw, A = away win"""
    if row["home_goals"] > row["away_goals"]:
        return "H"
    elif row["home_goals"] < row["away_goals"]:
        return "A"
    return "D"
