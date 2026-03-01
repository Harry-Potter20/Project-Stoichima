from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean
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