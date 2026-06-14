"""
Core pipeline tests.
Run from backend/: pytest tests/ -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest


# ── Feature engineering ──────────────────────────────────────────────────────

def _sample_df(n=10):
    teams = ["Arsenal", "Chelsea", "Liverpool", "Man City", "Spurs"]
    rows = []
    for i in range(n):
        ht = teams[i % len(teams)]
        at = teams[(i + 1) % len(teams)]
        result = ["H", "D", "A"][i % 3]
        rows.append({
            "match_date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i * 7),
            "home_team": ht, "away_team": at,
            "home_team_score": 2 if result == "H" else (1 if result == "D" else 0),
            "away_team_score": 0 if result == "H" else (1 if result == "D" else 2),
            "home_team_xG": 1.5, "away_team_xG": 0.8,
            "home_team_shots": 12, "away_team_shots": 8,
            "home_team_shots_on_target": 5, "away_team_shots_on_target": 3,
            "home_team_red_cards": 0, "away_team_red_cards": 0,
            "home_team_yellow_cards": 1, "away_team_yellow_cards": 2,
            "result": result, "season": 2024, "status": "FINISHED",
            "referee": "Mike Dean",
            "opening_home_odds": 2.0, "opening_draw_odds": 3.4, "opening_away_odds": 3.8,
            "b365_home": 1.9, "b365_draw": 3.5, "b365_away": 4.0,
        })
    return pd.DataFrame(rows)


def test_form_features():
    from data_processing.feature_engineering import _add_form_and_goals_features
    df = _sample_df()
    out = _add_form_and_goals_features(df, window=5)
    assert "home_form" in out.columns
    assert "away_form" in out.columns
    assert "home_win_streak" in out.columns
    assert "streak_diff" in out.columns
    assert out["home_form"].between(0, 3).all()


def test_shots_features():
    from data_processing.feature_engineering import _add_shots_features
    df = _sample_df()
    out = _add_shots_features(df, window=5)
    assert "home_shots_avg" in out.columns
    assert "home_shot_acc" in out.columns
    assert "sot_diff" in out.columns
    assert (out["home_shot_acc"] >= 0).all()


def test_fixture_congestion():
    from data_processing.feature_engineering import _add_fixture_congestion
    df = _sample_df()
    out = _add_fixture_congestion(df)
    assert "home_matches_last_7d" in out.columns
    assert "congestion_diff" in out.columns
    # First match has no prior matches → 0 congestion
    assert out["home_matches_last_7d"].iloc[0] == 0


def test_odds_movement():
    from data_processing.feature_engineering import _add_odds_movement_features
    df = _sample_df()
    out = _add_odds_movement_features(df)
    assert "home_odds_move" in out.columns
    # b365_home=1.9, opening=2.0 → movement < 1 (odds shortened)
    assert out["home_odds_move"].iloc[0] < 1.0


def test_table_pressure():
    from data_processing.feature_engineering import _add_table_pressure
    df = _sample_df(15)
    out = _add_table_pressure(df)
    assert "home_table_pos" in out.columns
    assert "table_pos_diff" in out.columns


def test_ppg_red_card_discount():
    from data_processing.feature_engineering import _calculate_ppg
    normal_match = {"home_team": "Arsenal", "away_team": "Chelsea",
                    "result": "H", "home_team_red_cards": 0, "away_team_red_cards": 0}
    red_card_match = {"home_team": "Arsenal", "away_team": "Chelsea",
                      "result": "H", "home_team_red_cards": 1, "away_team_red_cards": 0}
    ppg_normal   = _calculate_ppg("Arsenal", [normal_match], window=1)
    ppg_red_card = _calculate_ppg("Arsenal", [red_card_match], window=1)
    assert ppg_red_card < ppg_normal, "Red card match should discount PPG"


def test_hot_streak():
    from data_processing.feature_engineering import _current_streak
    # Chronological: W, W, D (most recent last → reversed gives D, W, W)
    matches = [
        {"home_team": "Arsenal", "away_team": "Chelsea",  "result": "H"},  # Arsenal win
        {"home_team": "Liverpool", "away_team": "Arsenal", "result": "A"},  # Arsenal win
        {"home_team": "Arsenal", "away_team": "Spurs",    "result": "D"},  # Draw — most recent
    ]
    ws, us = _current_streak("Arsenal", matches)
    assert ws == 0, "Win streak should be 0 — most recent result is a draw"
    assert us == 3, "Unbeaten streak should be 3 (W, W, D all unbeaten)"

    # Pure win streak
    wins = [
        {"home_team": "Arsenal", "away_team": "Chelsea",  "result": "H"},
        {"home_team": "Arsenal", "away_team": "Liverpool", "result": "H"},
        {"home_team": "Arsenal", "away_team": "Spurs",    "result": "H"},
    ]
    ws2, us2 = _current_streak("Arsenal", wins)
    assert ws2 == 3
    assert us2 == 3


# ── Goals distribution model ─────────────────────────────────────────────────

def test_dc_model_markets():
    from models.goals_distribution_model import GoalsDistributionModel
    m = GoalsDistributionModel()
    m.attack = {"Arsenal": 0.3, "Chelsea": 0.0}
    m.defense = {"Arsenal": 0.0, "Chelsea": -0.1}
    m.home_advantage = 0.28
    m.rho = -0.05
    m.teams = ["Arsenal", "Chelsea"]

    mat = m.predict_score_matrix("Arsenal", "Chelsea")
    assert abs(mat.sum() - 1.0) < 1e-4, "Score matrix must sum to 1"

    dnb = m.market_dnb(mat)
    assert abs(dnb["home"] + dnb["away"] - 1.0) < 1e-4

    wbtts = m.market_wbtts(mat)
    assert 0 < wbtts["either"] < 1


def test_neutral_venue_reduces_home_advantage():
    from models.goals_distribution_model import GoalsDistributionModel
    m = GoalsDistributionModel()
    m.attack = {"A": 0.0, "B": 0.0}
    m.defense = {"A": 0.0, "B": 0.0}
    m.home_advantage = 0.3
    m.rho = -0.05
    m.teams = ["A", "B"]

    mat_home    = m.predict_score_matrix("A", "B", neutral_venue=False)
    mat_neutral = m.predict_score_matrix("A", "B", neutral_venue=True)
    p_home_win    = float(np.sum(np.tril(mat_home, -1)))
    p_neutral_win = float(np.sum(np.tril(mat_neutral, -1)))
    assert p_home_win > p_neutral_win, "Home advantage should increase home win probability"


# ── API smoke tests ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_bankroll_endpoint(client):
    r = client.get("/api/v1/bankroll/PL")
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body or body["count"] == 0


def test_recommendations_endpoint(client):
    r = client.get("/api/v1/bets/recommendations?min_edge=0")
    assert r.status_code == 200
    assert "recommendations" in r.json()


def test_parlay_endpoint(client):
    r = client.get("/api/v1/bets/parlay?top_n=3&min_edge=0")
    assert r.status_code == 200
    body = r.json()
    assert "legs" in body


def test_market_breakdown_endpoint(client):
    r = client.get("/api/v1/bets/market_breakdown")
    assert r.status_code == 200
    assert "by_outcome" in r.json()


def test_bet_slip_has_stake_amount(client):
    r = client.get("/api/v1/bets/slip?min_edge=0")
    assert r.status_code == 200
    body = r.json()
    assert "bankroll_size" in body
    # All bets in the slip should have stake_amount
    for day in body["slip"]:
        for bet in day["bets"]:
            assert "stake_amount" in bet


def test_basketball_predict(client):
    r = client.get("/api/v1/basketball/predict?home_team=Lakers&away_team=Celtics")
    assert r.status_code == 200
    body = r.json()
    assert abs(body["home_win_prob"] + body["away_win_prob"] - 1.0) < 1e-4


def test_manual_bet_endpoint(client):
    r = client.post("/api/v1/bets/manual", json={
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "competition": "PL",
        "match_date": "2026-06-15",
        "bet_on": "H",
        "market": "1x2",
        "decimal_odds": 2.10,
        "model_prob": 0.52,
        "notes": "test bet",
        "tags": "test,pipeline",
    })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["source"] == "manual"
    assert body["edge_pct"] > 0
    bet_id = body["id"]

    # Test notes update
    r2 = client.patch(f"/api/v1/bets/{bet_id}/notes", json={"notes": "updated", "tags": "wc2026"})
    assert r2.status_code == 200
    assert r2.json()["notes"] == "updated"

    # Test delete
    r3 = client.delete(f"/api/v1/bets/{bet_id}")
    assert r3.status_code == 200
    assert r3.json()["deleted"] == bet_id


def test_model_info_endpoint(client):
    r = client.get("/admin/model-info")
    assert r.status_code == 200
    body = r.json()
    assert "model_version" in body
    assert "brier_baseline" in body


def test_admin_auth_enforced_when_key_set(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main.settings, "admin_api_key", "s3cret")
    assert client.get("/admin/model-info").status_code == 401
    assert client.get("/admin/model-info", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.get("/admin/model-info", headers={"X-Admin-Key": "s3cret"}).status_code == 200


def test_draw_specialist_feature():
    from data_processing.feature_engineering import _add_form_and_goals_features
    df = _sample_df(15)
    out = _add_form_and_goals_features(df, window=5)
    assert "home_draw_rate" in out.columns
    assert "away_draw_rate" in out.columns
    assert "combined_draw_rate" in out.columns
    assert out["home_draw_rate"].between(0, 1).all()


def test_corners_feature():
    from data_processing.feature_engineering import _add_corners_features
    import pandas as pd
    df = _sample_df(10)
    # add corner data
    df["home_team_corners"] = 5
    df["away_team_corners"] = 4
    out = _add_corners_features(df)
    assert "home_corners_avg" in out.columns
    assert "expected_corners" in out.columns
