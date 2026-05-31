"""
Tennis data collector — loads Jeff Sackmann's open ATP/WTA match datasets.

Sources:
  ATP: https://github.com/JeffSackmann/tennis_atp
  WTA: https://github.com/JeffSackmann/tennis_wta

Each year is a single CSV file. We stream directly from GitHub raw content.

Usage:
    cd backend
    python3 data_collection/tennis_data_collector.py            # ATP 2015-2025
    python3 data_collection/tennis_data_collector.py --tour WTA
    python3 data_collection/tennis_data_collector.py --years 2023 2024 2025
"""

import sys
import io
import logging
import argparse
from datetime import datetime

import requests
import pandas as pd
import numpy as np

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from app.database import SessionLocal
from app.models import TennisMatch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Jeff Sackmann's public GitHub repositories
ATP_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
WTA_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"

# Surface normalisation
SURFACE_MAP = {
    "Clay": "Clay", "Hard": "Hard", "Grass": "Grass",
    "Carpet": "Carpet", "Indoor Hard": "Hard", "Outdoor Hard": "Hard",
}


def _build_url(tour: str, year: int) -> str:
    base = ATP_BASE if tour == "ATP" else WTA_BASE
    prefix = "atp" if tour == "ATP" else "wta"
    return f"{base}/{prefix}_matches_{year}.csv"


def _safe_int(val) -> int | None:
    try:
        v = int(float(val))
        return v if not np.isnan(float(val)) else None
    except (TypeError, ValueError):
        return None


def _parse_date(val) -> datetime | None:
    try:
        s = str(int(val))
        return datetime.strptime(s, "%Y%m%d")
    except (TypeError, ValueError):
        return None


def fetch_year(tour: str, year: int) -> pd.DataFrame:
    url = _build_url(tour, year)
    log.info("  fetching %s", url)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text), low_memory=False)


def import_year(tour: str, year: int) -> int:
    try:
        df = fetch_year(tour, year)
    except Exception as e:
        log.warning("  %s %d fetch failed: %s", tour, year, e)
        return 0

    rows = []
    for _, r in df.iterrows():
        # Build a stable sackmann_id from tourney_id + match_num
        sackmann_id = f"{tour}_{r.get('tourney_id', '')}_{r.get('match_num', '')}"

        # player1 = winner, player2 = loser (we store winner=1 always during training)
        rows.append({
            "sackmann_id":    sackmann_id,
            "tour":           tour,
            "tourney_date":   _parse_date(r.get("tourney_date")),
            "tourney_name":   str(r.get("tourney_name", "")),
            "tourney_level":  str(r.get("tourney_level", "")),
            "surface":        SURFACE_MAP.get(str(r.get("surface", "")), str(r.get("surface", ""))),
            "round":          str(r.get("round", "")),
            "player1_name":   str(r.get("winner_name", "")),
            "player2_name":   str(r.get("loser_name", "")),
            "player1_rank":   _safe_int(r.get("winner_rank")),
            "player2_rank":   _safe_int(r.get("loser_rank")),
            "player1_seed":   _safe_int(r.get("winner_seed")),
            "player2_seed":   _safe_int(r.get("loser_seed")),
            "winner":         1,
            # winner serve stats → p1
            "p1_ace":         _safe_int(r.get("w_ace")),
            "p1_df":          _safe_int(r.get("w_df")),
            "p1_svpt":        _safe_int(r.get("w_svpt")),
            "p1_1st_in":      _safe_int(r.get("w_1stIn")),
            "p1_1st_won":     _safe_int(r.get("w_1stWon")),
            "p1_2nd_won":     _safe_int(r.get("w_2ndWon")),
            "p1_bp_saved":    _safe_int(r.get("w_bpSaved")),
            "p1_bp_faced":    _safe_int(r.get("w_bpFaced")),
            # loser serve stats → p2
            "p2_ace":         _safe_int(r.get("l_ace")),
            "p2_df":          _safe_int(r.get("l_df")),
            "p2_svpt":        _safe_int(r.get("l_svpt")),
            "p2_1st_in":      _safe_int(r.get("l_1stIn")),
            "p2_1st_won":     _safe_int(r.get("l_1stWon")),
            "p2_2nd_won":     _safe_int(r.get("l_2ndWon")),
            "p2_bp_saved":    _safe_int(r.get("l_bpSaved")),
            "p2_bp_faced":    _safe_int(r.get("l_bpFaced")),
            "score":          str(r.get("score", "")),
            "minutes":        _safe_int(r.get("minutes")),
        })

    if not rows:
        return 0

    with SessionLocal() as db:
        inserted = 0
        for row in rows:
            if not row["tourney_date"]:
                continue
            existing = db.query(TennisMatch).filter_by(
                sackmann_id=row["sackmann_id"]
            ).first()
            if existing:
                continue
            db.add(TennisMatch(**row))
            inserted += 1
        db.commit()

    log.info("  %s %d → %d new records (of %d)", tour, year, inserted, len(rows))
    return inserted


def run(tour: str = "ATP", years: list[int] | None = None):
    if years is None:
        years = list(range(2010, datetime.utcnow().year + 1))

    log.info("Tennis import: %s, years %d–%d", tour, years[0], years[-1])
    total = 0
    for year in years:
        total += import_year(tour, year)
    log.info("Tennis import complete: %d total new records", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tour", default="ATP", choices=["ATP", "WTA"])
    parser.add_argument("--years", nargs="+", type=int)
    args = parser.parse_args()
    run(tour=args.tour, years=args.years)
