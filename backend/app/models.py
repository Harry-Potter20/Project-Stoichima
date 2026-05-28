from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey
from sqlalchemy.sql import func
from app.database import Base

class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True)
    match_date = Column(DateTime, nullable=False)
    matchday = Column(Integer, nullable=True)
    home_team_id = Column(Integer, nullable=True)
    away_team_id = Column(Integer, nullable=True)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    home_team_score = Column(Integer, nullable=True)
    away_team_score = Column(Integer, nullable=True)
    home_team_xG = Column(Float, nullable=True)
    away_team_xG = Column(Float, nullable=True)
    competition = Column(String, nullable=False)
    season = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    result = Column(String, nullable=True)
    total_goals = Column(Integer, nullable=True)
    over_1_5_goals = Column(Boolean, nullable=True)
    over_2_5_goals = Column(Boolean, nullable=True)
    btts = Column(Boolean, nullable=True)
    home_team_shots = Column(Integer, nullable=True)
    away_team_shots = Column(Integer, nullable=True)
    home_team_shots_on_target = Column(Integer, nullable=True)
    away_team_shots_on_target = Column(Integer, nullable=True)
    home_team_corners = Column(Integer, nullable=True)
    away_team_corners = Column(Integer, nullable=True)
    home_team_yellow_cards = Column(Integer, nullable=True)
    away_team_yellow_cards = Column(Integer, nullable=True)
    home_team_red_cards = Column(Integer, nullable=True)
    away_team_red_cards = Column(Integer, nullable=True)
    home_team_fouls = Column(Integer, nullable=True)
    away_team_fouls = Column(Integer, nullable=True)
    
    def __repr__(self):
        return f"<Match {self.home_team} vs {self.away_team} ({self.match_date.date()})>"

class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    country = Column(String, nullable=False)

    def __repr__(self):
        return f"<Team {self.name} ({self.country})>"


class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    competition = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    match_date = Column(DateTime, nullable=False)

    # Outcome
    predicted_outcome = Column(String, nullable=False)     # H / D / A
    home_win_prob = Column(Float, nullable=False)
    draw_prob = Column(Float, nullable=False)
    away_win_prob = Column(Float, nullable=False)

    # Over/Under 2.5
    predicted_over_2_5 = Column(Boolean, nullable=False)
    over_2_5_prob = Column(Float, nullable=False)

    # BTTS
    predicted_btts = Column(Boolean, nullable=False)
    btts_prob = Column(Float, nullable=False)

    # Actuals (filled in once the match is finished)
    actual_outcome = Column(String, nullable=True)
    actual_over_2_5 = Column(Boolean, nullable=True)
    actual_btts = Column(Boolean, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Prediction {self.home_team} vs {self.away_team} ({self.match_date.date()})>"