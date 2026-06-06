"""
Telegram Bot webhook — handles slash commands sent to the bot.

Register the webhook once (replace TOKEN and PUBLIC_URL):
  curl -X POST "https://api.telegram.org/bot{TOKEN}/setWebhook" \
       -d "url=https://YOUR_PUBLIC_URL/telegram/webhook"

Supported commands:
  /predictions [WC|PL|PD|...]  — upcoming predictions for a competition
  /bankroll                    — running P&L summary
  /slip                        — today's open bet recommendations
  /help                        — command list
"""

import logging
from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
router = APIRouter()


def _send(chat_id: int, text: str) -> None:
    """Fire-and-forget Telegram sendMessage."""
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.telegram_bot_token:
            return
        import urllib.request, json as _json
        url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
        payload = _json.dumps({
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)


def _cmd_predictions(comp: str) -> str:
    comp = comp.upper() if comp else "WC"
    try:
        from app.database import SessionLocal
        from app.models import Match
        from prediction.predictor import get_predictor
        import pandas as pd

        with SessionLocal() as db:
            upcoming = db.query(Match).filter(
                Match.competition == comp,
                Match.status.in_(["SCHEDULED", "TIMED"]),
            ).order_by(Match.match_date).limit(5).all()

            if not upcoming:
                return f"No upcoming matches for *{comp}*."

            finished = db.query(Match).filter(
                Match.competition == comp,
                Match.status == "FINISHED",
            ).order_by(Match.match_date).all()

            udf = pd.DataFrame([{
                "home_team": m.home_team, "away_team": m.away_team,
                "match_date": m.match_date, "season": m.season,
                "status": m.status, "result": None,
                "home_team_score": None, "away_team_score": None,
                "home_team_xG": None, "away_team_xG": None,
                "over_2_5_goals": None, "btts": None, "referee": None,
                "home_team_yellow_cards": None, "away_team_yellow_cards": None,
                "home_team_red_cards": None, "away_team_red_cards": None,
                "opening_home_odds": None, "opening_draw_odds": None, "opening_away_odds": None,
                "is_neutral_venue": bool(m.is_neutral_venue or False),
                "competition": m.competition,
            } for m in upcoming])

            fdf = pd.DataFrame([{
                "home_team": m.home_team, "away_team": m.away_team,
                "match_date": m.match_date, "season": m.season,
                "result": m.result, "home_team_score": m.home_team_score,
                "away_team_score": m.away_team_score,
                "home_team_xG": m.home_team_xG, "away_team_xG": m.away_team_xG,
                "over_2_5_goals": m.over_2_5_goals, "btts": m.btts, "referee": None,
                "home_team_yellow_cards": None, "away_team_yellow_cards": None,
                "home_team_red_cards": None, "away_team_red_cards": None,
                "opening_home_odds": None, "opening_draw_odds": None, "opening_away_odds": None,
            } for m in finished]) if finished else pd.DataFrame()

            predictor = get_predictor()
            preds = predictor.predict(udf, fdf, db=db, competition=comp)

        lines = [f"⚽ *{comp} — Next 5 Matches*\n"]
        for p in preds[:5]:
            o = p.get("outcome", {})
            date = str(p.get("match_date", ""))[:10]
            h = f"{o.get('home_win_prob', 0)*100:.0f}%"
            d = f"{o.get('draw_prob', 0)*100:.0f}%"
            a = f"{o.get('away_win_prob', 0)*100:.0f}%"
            pred = o.get("predicted", "?")
            lines.append(
                f"📅 {date}\n"
                f"🏠 *{p['home_team']}* vs *{p['away_team']}*\n"
                f"H {h} · D {d} · A {a} → *{pred}*\n"
            )
        return "\n".join(lines)
    except Exception as exc:
        log.error("Telegram /predictions error: %s", exc)
        return f"Error fetching predictions for {comp}: {exc}"


def _cmd_bankroll() -> str:
    try:
        from app.database import SessionLocal
        from app.models import BetLog
        with SessionLocal() as db:
            resolved = db.query(BetLog).filter(BetLog.won.isnot(None)).all()
            pending  = db.query(BetLog).filter(BetLog.won.is_(None)).count()

        if not resolved:
            return "💰 No resolved bets yet. P&L will appear after the first settled bet."

        won   = [b for b in resolved if b.won]
        total_pl    = round(sum(b.profit_loss_units or 0 for b in resolved), 2)
        total_stake = round(sum(b.kelly_pct for b in resolved), 2)
        roi   = round(total_pl / total_stake * 100 if total_stake else 0, 1)
        wr    = round(len(won) / len(resolved) * 100, 1)
        sign  = "+" if total_pl >= 0 else ""
        return (
            f"💰 *Stoichima Bankroll*\n"
            f"Resolved: {len(resolved)} | Win rate: {wr}%\n"
            f"P&L: *{sign}{total_pl} units* | ROI: {sign}{roi}%\n"
            f"Pending: {pending} open bets"
        )
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_slip() -> str:
    try:
        from app.database import SessionLocal
        from app.models import BetLog
        from datetime import date
        today = date.today()
        with SessionLocal() as db:
            bets = db.query(BetLog).filter(
                BetLog.won.is_(None),
                BetLog.edge_pct >= 3.0,
            ).order_by(BetLog.match_date).limit(8).all()

        if not bets:
            return "🎯 No open value bets right now (edge ≥ 3%)."

        lines = ["🎯 *Open Value Bets* (edge ≥ 3%)\n"]
        for b in bets:
            date_str = b.match_date.date().isoformat() if b.match_date else "?"
            lines.append(
                f"📅 {date_str} [{b.competition}]\n"
                f"⚽ {b.home_team} v {b.away_team}\n"
                f"🎰 *{b.bet_on}* @{b.decimal_odds:.2f} | Edge: +{b.edge_pct}% | Kelly: {b.kelly_pct}%\n"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_today() -> str:
    """Show all matches kicking off in the next 24 hours across competitions."""
    try:
        from app.database import SessionLocal
        from app.models import Match, Prediction
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        end = now + timedelta(hours=24)
        with SessionLocal() as db:
            matches = (
                db.query(Match)
                .filter(
                    Match.status.in_(["SCHEDULED", "TIMED"]),
                    Match.match_date >= now,
                    Match.match_date <= end,
                )
                .order_by(Match.match_date)
                .limit(20)
                .all()
            )
            if not matches:
                return "📅 No matches in the next 24h."
            pred_lut = {
                (p.home_team, p.away_team, str(p.match_date)[:10]): p
                for p in db.query(Prediction).all()
            }
        lines = [f"📅 *Next 24h* ({len(matches)} matches)\n"]
        for m in matches:
            key = (m.home_team, m.away_team, str(m.match_date)[:10])
            p = pred_lut.get(key)
            ko = m.match_date.strftime("%H:%M") if m.match_date else "?"
            if p and p.home_win_prob is not None:
                pick = p.predicted_outcome or "?"
                prob = max(p.home_win_prob, p.draw_prob, p.away_win_prob)
                ou = round((p.over_2_5_prob or 0) * 100)
                bt = round((p.btts_prob or 0) * 100)
                lines.append(
                    f"⏰ {ko} [{m.competition}] {m.home_team} v {m.away_team}\n"
                    f"   → *{pick}* ({prob*100:.0f}%) · O2.5 {ou}% · BTTS {bt}%"
                )
            else:
                lines.append(
                    f"⏰ {ko} [{m.competition}] {m.home_team} v {m.away_team}"
                )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_best(n: int = 5) -> str:
    """Top N highest-edge open value bets — like /slip but with more detail."""
    try:
        from app.database import SessionLocal
        from app.models import BetLog
        with SessionLocal() as db:
            bets = (
                db.query(BetLog)
                .filter(BetLog.won.is_(None), BetLog.edge_pct >= 3.0)
                .order_by(BetLog.edge_pct.desc())
                .limit(n)
                .all()
            )
        if not bets:
            return f"🎯 No open value bets (edge ≥ 3%)."
        lines = [f"🎯 *Top {len(bets)} Value Bets*\n"]
        for b in bets:
            date_str = b.match_date.strftime("%Y-%m-%d %H:%M") if b.match_date else "?"
            lines.append(
                f"📅 {date_str} UTC [{b.competition}]\n"
                f"⚽ {b.home_team} vs {b.away_team}\n"
                f"🎰 *{b.bet_on}* @{b.decimal_odds:.2f}\n"
                f"📊 Edge +{b.edge_pct}% | Kelly {b.kelly_pct}%\n"
                f"💡 Model {round((b.model_prob or 0)*100)}% vs Market {round((b.implied_prob or 0)*100)}%\n"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_accuracy(comp: str | None = None) -> str:
    """Overall (or per-competition) prediction accuracy + Brier score."""
    try:
        from app.database import SessionLocal
        from app.models import Prediction
        with SessionLocal() as db:
            q = db.query(Prediction).filter(Prediction.actual_outcome.isnot(None))
            if comp:
                q = q.filter(Prediction.competition == comp.upper())
            resolved = q.all()
        if not resolved:
            scope = comp.upper() if comp else "all competitions"
            return f"📊 No resolved predictions yet for {scope}."

        outcome_map = {"H": 0, "D": 1, "A": 2}
        correct = sum(1 for p in resolved if p.predicted_outcome == p.actual_outcome)
        brier_scores = []
        for p in resolved:
            y = outcome_map.get(p.actual_outcome, -1)
            if y == -1:
                continue
            probs = [p.home_win_prob or 0, p.draw_prob or 0, p.away_win_prob or 0]
            brier_scores.append(sum((pi - (1.0 if i == y else 0.0))**2 for i, pi in enumerate(probs)))

        acc = round(correct / len(resolved) * 100, 1)
        brier = round(sum(brier_scores) / len(brier_scores), 4) if brier_scores else None
        scope = comp.upper() if comp else "All"
        return (
            f"📊 *Accuracy — {scope}*\n"
            f"Resolved: {len(resolved)} predictions\n"
            f"Correct: *{acc}%*\n"
            f"Brier: {brier} (baseline 0.667 — lower is better)"
        )
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_results(hours: int = 36) -> str:
    """
    Recent resolved predictions — pick vs actual outcome, grouped by competition.
    Default window: last 36h (catches yesterday + overnight settlements).
    """
    try:
        from app.database import SessionLocal
        from app.models import Prediction
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        with SessionLocal() as db:
            resolved = (
                db.query(Prediction)
                .filter(
                    Prediction.actual_outcome.isnot(None),
                    Prediction.resolved_at >= cutoff,
                )
                .order_by(Prediction.match_date.desc())
                .all()
            )
        if not resolved:
            return f"📊 No resolved predictions in the last {hours}h."

        correct = sum(1 for p in resolved if p.predicted_outcome == p.actual_outcome)
        n = len(resolved)
        acc = correct / n * 100
        by_comp: dict[str, list] = {}
        for p in resolved:
            by_comp.setdefault(p.competition, []).append(p)

        lines = [
            f"📊 *Results* (last {hours}h)",
            f"_{n} matches · {correct}/{n} correct ({acc:.0f}%)_",
        ]
        for comp, preds in sorted(by_comp.items()):
            comp_correct = sum(1 for p in preds if p.predicted_outcome == p.actual_outcome)
            lines.append(f"\n*{comp}* — {comp_correct}/{len(preds)} correct")
            for p in sorted(preds, key=lambda x: x.match_date)[:10]:
                mark = "✅" if p.predicted_outcome == p.actual_outcome else "❌"
                top_prob = max(p.home_win_prob or 0, p.draw_prob or 0, p.away_win_prob or 0)
                lines.append(
                    f"  {mark} {p.home_team} v {p.away_team} → *{p.predicted_outcome}* "
                    f"({top_prob*100:.0f}%) · actual *{p.actual_outcome}*"
                )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_clv() -> str:
    """Closing Line Value summary across competitions."""
    try:
        from app.database import SessionLocal
        from app.models import BetLog
        from sqlalchemy import distinct
        with SessionLocal() as db:
            comps = [r[0] for r in db.query(distinct(BetLog.competition))
                     .filter(BetLog.clv_pct.isnot(None)).all()]
            rows = []
            for c in comps:
                bets = db.query(BetLog).filter(BetLog.competition == c, BetLog.clv_pct.isnot(None)).all()
                if not bets:
                    continue
                avg = sum(b.clv_pct for b in bets) / len(bets)
                pos = sum(1 for b in bets if b.clv_pct > 0) / len(bets) * 100
                rows.append((c, len(bets), avg, pos))

        if not rows:
            return "📈 No CLV data yet. Need settled bets with closing odds captured."
        rows.sort(key=lambda r: r[2], reverse=True)
        lines = ["📈 *Closing Line Value (CLV) — by Competition*\n"]
        for c, n, avg, pos in rows:
            sign = "+" if avg >= 0 else ""
            lines.append(f"*{c}*: avg CLV *{sign}{avg:.2f}%* | beat close {pos:.0f}% | n={n}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_props(home: str, away: str) -> str:
    """Top scorers / first-scorer / 2+ goals for a specific fixture."""
    try:
        from app.database import SessionLocal
        from app.models import Match
        from models.player_props_model import top_props_for_team
        from prediction.predictor import get_predictor
        INTL = {
            "WC","EC","UNL","AFC","ASIA","CA","GOLD","FRIENDLY",
            "WCQ_EU","WCQ_AF","WCQ_AS","WCQ_SA","WCQ_CC","WCQ_OC",
        }
        with SessionLocal() as db:
            match = (
                db.query(Match)
                .filter(
                    Match.home_team.ilike(f"%{home}%"),
                    Match.away_team.ilike(f"%{away}%"),
                    Match.status.in_(["SCHEDULED", "TIMED"]),
                )
                .order_by(Match.match_date)
                .first()
            )
            if not match:
                return f"🔍 No upcoming fixture found for `{home}` vs `{away}`."

            predictor = get_predictor()
            dc = (
                predictor.dist_model_intl
                if match.competition in INTL and predictor.dist_model_intl is not None
                else predictor.dist_model
            )
            if dc is None:
                return "Player props need a DC model loaded — none available."

            neutral = bool(match.is_neutral_venue)
            lam_h, lam_a = dc.get_lambdas(match.home_team, match.away_team, neutral_venue=neutral)

            home_props = top_props_for_team(db, match.home_team, lam_h, lam_a, top_n=5)
            away_props = top_props_for_team(db, match.away_team, lam_a, lam_h, top_n=5)

        lines = [
            f"🥅 *Top Scorer Props*",
            f"⚽ {match.home_team} v {match.away_team}",
            f"_Expected xG: {lam_h:.2f} – {lam_a:.2f}_",
        ]
        if home_props:
            lines.append(f"\n*{match.home_team}*")
            for p in home_props:
                first = f" · first {p['first_scorer']*100:.0f}%" if p.get("first_scorer") is not None else ""
                lines.append(
                    f"  {p['player']}: any {p['anytime_scorer']*100:.0f}%"
                    f" · 2+ {p['two_plus']*100:.0f}%{first}"
                )
        else:
            lines.append(f"\n*{match.home_team}*: no player data")
        if away_props:
            lines.append(f"\n*{match.away_team}*")
            for p in away_props:
                first = f" · first {p['first_scorer']*100:.0f}%" if p.get("first_scorer") is not None else ""
                lines.append(
                    f"  {p['player']}: any {p['anytime_scorer']*100:.0f}%"
                    f" · 2+ {p['two_plus']*100:.0f}%{first}"
                )
        else:
            lines.append(f"\n*{match.away_team}*: no player data")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_live() -> str:
    """All currently in-play matches with live model probabilities."""
    try:
        from app.database import SessionLocal
        from app.models import LiveMatchState, LivePredictionSnapshot
        with SessionLocal() as db:
            states = (
                db.query(LiveMatchState)
                .filter(LiveMatchState.status.in_(["IN_PLAY", "HT", "IN_PLAY_2H", "AET", "PEN", "SUSPENDED"]))
                .order_by(LiveMatchState.kickoff_at)
                .all()
            )
            if not states:
                return "📺 No matches currently in play."
            lines = [f"📺 *Live Matches* ({len(states)})\n"]
            for s in states:
                latest = (
                    db.query(LivePredictionSnapshot)
                    .filter(LivePredictionSnapshot.match_id == s.match_id)
                    .order_by(LivePredictionSnapshot.minute.desc())
                    .first()
                )
                status_emoji = {"IN_PLAY": "🟢", "IN_PLAY_2H": "🟢", "HT": "⏸", "AET": "⏰", "PEN": "🎯", "SUSPENDED": "⚠️"}.get(s.status, "🟢")
                line = (
                    f"{status_emoji} {s.minute}' [{s.competition}]\n"
                    f"⚽ *{s.home_team}* {s.home_score}-{s.away_score} *{s.away_team}*"
                )
                if latest:
                    h = (latest.home_win_prob or 0) * 100
                    d = (latest.draw_prob or 0) * 100
                    a = (latest.away_win_prob or 0) * 100
                    line += f"\n   H {h:.0f}% · D {d:.0f}% · A {a:.0f}%"
                lines.append(line)
        return "\n\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _cmd_match(home: str, away: str) -> str:
    """
    Detailed market breakdown for a specific fixture.
    Calls the live predictor pipeline so we get fresh DC markets (O/U, BTTS,
    handicap, correct score, double chance) not just the cached 1x2.
    """
    try:
        from app.database import SessionLocal
        from app.models import Match
        from prediction.predictor import get_predictor
        import pandas as pd

        with SessionLocal() as db:
            match = (
                db.query(Match)
                .filter(
                    Match.home_team.ilike(f"%{home}%"),
                    Match.away_team.ilike(f"%{away}%"),
                    Match.status.in_(["SCHEDULED", "TIMED"]),
                )
                .order_by(Match.match_date)
                .first()
            )
            if not match:
                return f"🔍 No upcoming fixture found for `{home}` vs `{away}`."

            finished = db.query(Match).filter(
                Match.competition == match.competition,
                Match.status == "FINISHED",
            ).order_by(Match.match_date).all()

            common_cols = lambda m, with_result: {
                "home_team": m.home_team, "away_team": m.away_team,
                "match_date": m.match_date, "season": m.season,
                "status": m.status,
                "result": (m.result if with_result else None),
                "home_team_score": m.home_team_score if with_result else None,
                "away_team_score": m.away_team_score if with_result else None,
                "home_team_xG": m.home_team_xG if with_result else None,
                "away_team_xG": m.away_team_xG if with_result else None,
                "over_2_5_goals": m.over_2_5_goals if with_result else None,
                "btts": m.btts if with_result else None,
                "referee": None,
                "home_team_yellow_cards": None, "away_team_yellow_cards": None,
                "home_team_red_cards": None, "away_team_red_cards": None,
                "opening_home_odds": None, "opening_draw_odds": None, "opening_away_odds": None,
                "is_neutral_venue": bool(m.is_neutral_venue or False),
                "competition": m.competition,
            }
            udf = pd.DataFrame([common_cols(match, False)])
            fdf = pd.DataFrame([common_cols(m, True) for m in finished]) if finished else pd.DataFrame()

            predictor = get_predictor()
            preds = predictor.predict(udf, fdf, db=db, competition=match.competition)

        if not preds:
            return f"🔍 No prediction generated for `{home}` vs `{away}`."

        p = preds[0]
        o = p["outcome"]
        m = p.get("markets", {}) or {}
        xg = p.get("xg_range", {}) or {}

        h = o["home_win_prob"] * 100
        d = o["draw_prob"] * 100
        a = o["away_win_prob"] * 100

        date_str = (p.get("match_date") or "")[:16].replace("T", " ")
        flag_h = ""  # plain text for Telegram

        lines = [
            f"⚽ *{p['home_team']}* vs *{p['away_team']}*",
            f"📅 {date_str} UTC · [{match.competition}]",
            "",
            f"*1x2* → H {h:.0f}% · D {d:.0f}% · A {a:.0f}%  (pick: *{o.get('predicted','?')}*)",
            f"*xG* {xg.get('home_xg',0):.2f} – {xg.get('away_xg',0):.2f} (total {xg.get('total_xg',0):.2f})",
        ]

        # Over / Under
        ou = m.get("over_under") or {}
        if ou:
            lines.append("")
            lines.append("*Goal Lines*")
            for line in ["1.5", "2.5", "3.5", "4.5"]:
                data = ou.get(line)
                if not data: continue
                ov = data.get("over", 0) * 100
                un = data.get("under", 0) * 100
                lines.append(f"  {line}: O {ov:.0f}% / U {un:.0f}%")

        # BTTS
        btts = m.get("btts")
        if btts:
            lines.append("")
            lines.append(f"*BTTS* — Yes {btts.get('yes',0)*100:.0f}% / No {btts.get('no',0)*100:.0f}%")

        # Double chance
        dc = m.get("double_chance")
        if dc:
            lines.append("")
            lines.append("*Double Chance*")
            lines.append(f"  1X {dc.get('1X',0)*100:.0f}% · X2 {dc.get('X2',0)*100:.0f}% · 12 {dc.get('12',0)*100:.0f}%")

        # Asian handicap — show key lines (-1.5, -0.5, +0.5, +1.5)
        ah = m.get("asian_handicap") or {}
        if ah:
            lines.append("")
            lines.append("*Asian Handicap (home perspective)*")
            for line in ["-2.0", "-1.5", "-1.0", "-0.5", "0.0", "0.5", "1.0", "1.5", "2.0"]:
                data = ah.get(line)
                if not data: continue
                hp = data.get("home", 0) * 100
                ap = data.get("away", 0) * 100
                # Only show the ones that are interesting (between 20%–80%)
                if 0.15 < data.get("home", 0) < 0.85:
                    lines.append(f"  {line}: home {hp:.0f}% / away {ap:.0f}%")

        # Correct score — top 5
        cs = m.get("correct_score") or []
        if cs:
            lines.append("")
            lines.append("*Top Correct Scores*")
            for s in cs[:5]:
                lines.append(f"  {s['home_goals']}-{s['away_goals']} · {s['probability']*100:.1f}%")
            # First to score = P(home scores any) and P(away scores any)
            # Approx from CS matrix (probability home_goals > 0 vs away_goals > 0 first
            # requires temporal model; we surface P(team scores at all) instead).
            p_home_scores = sum(s["probability"] for s in cs if s["home_goals"] > 0)
            p_away_scores = sum(s["probability"] for s in cs if s["away_goals"] > 0)
            lines.append("")
            lines.append("*Scoring Probability*")
            lines.append(f"  {p['home_team']} to score: {p_home_scores*100:.0f}%")
            lines.append(f"  {p['away_team']} to score: {p_away_scores*100:.0f}%")

        # First-to-score market (competing-Poisson derivation)
        fts = m.get("first_to_score") or {}
        if fts:
            lines.append("")
            lines.append("*First to Score*")
            lines.append(f"  {p['home_team']}: {fts.get('home',0)*100:.0f}%")
            lines.append(f"  {p['away_team']}: {fts.get('away',0)*100:.0f}%")
            lines.append(f"  No goal:       {fts.get('no_goal',0)*100:.0f}%")

        # Confidence
        conf = p.get("confidence", {})
        if conf.get("model_agreement") is not None:
            lines.append("")
            lines.append(f"_Model agreement: {conf['model_agreement']*100:.0f}%_")

        return "\n".join(lines)
    except Exception as exc:
        import traceback
        return f"Error: {exc}\n{traceback.format_exc()[-300:]}"


HELP_TEXT = (
    "🤖 *Stoichima Bot Commands*\n\n"
    "*Fixtures & Predictions*\n"
    "/today — all matches in next 24h with picks\n"
    "/predictions [comp] — next 5 matches per competition (default WC)\n"
    "/match home away — lookup a specific fixture (substring OK)\n\n"
    "*Betting*\n"
    "/best [n] — top N value bets (default 5)\n"
    "/slip — open value bets (edge ≥ 3%)\n"
    "/bankroll — P&L, ROI, win rate\n\n"
    "*Model Health*\n"
    "/accuracy [comp] — accuracy + Brier score\n"
    "/clv — closing line value by competition\n\n"
    "/help — show this message"
)


# Telegram bot command list (used by /telegram/register-commands)
BOT_COMMANDS = [
    {"command": "live",        "description": "Matches currently in play with live probabilities"},
    {"command": "today",       "description": "Matches in the next 24h with picks"},
    {"command": "predictions", "description": "Next 5 matches for a competition (e.g. /predictions WC)"},
    {"command": "match",       "description": "Lookup a fixture: /match brazil morocco"},
    {"command": "props",       "description": "Top scorer props: /props brazil morocco"},
    {"command": "results",     "description": "Recent resolved predictions vs actuals"},
    {"command": "best",        "description": "Top N value bets (default 5)"},
    {"command": "slip",        "description": "Open value bets (edge ≥ 3%)"},
    {"command": "bankroll",    "description": "P&L, ROI, win rate"},
    {"command": "accuracy",    "description": "Prediction accuracy + Brier score"},
    {"command": "clv",         "description": "Closing line value by competition"},
    {"command": "help",        "description": "Show command list"},
]


@router.get("/telegram/status")
def telegram_status():
    """Check Telegram bot configuration and webhook status."""
    from app.config import get_settings
    s = get_settings()
    if not s.telegram_bot_token:
        return {"configured": False, "error": "TELEGRAM_BOT_TOKEN not set in .env"}

    import urllib.request, json as _json
    try:
        # getMe — verify token
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/getMe", timeout=5
        ) as r:
            me = _json.loads(r.read()).get("result", {})
        # getWebhookInfo — confirm registered URL
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/getWebhookInfo", timeout=5
        ) as r:
            wh = _json.loads(r.read()).get("result", {})
        return {
            "configured":     True,
            "chat_id":        s.telegram_chat_id or "(not set — outbound alerts disabled)",
            "bot": {
                "username":   me.get("username"),
                "first_name": me.get("first_name"),
            },
            "webhook": {
                "url":            wh.get("url") or "(not registered)",
                "pending_updates": wh.get("pending_update_count", 0),
                "last_error":     wh.get("last_error_message"),
            },
        }
    except Exception as exc:
        return {"configured": True, "error": str(exc)}


@router.post("/telegram/setup")
def telegram_setup(public_url: str):
    """
    Register the Telegram webhook URL. Call once with your public backend URL:
      POST /telegram/setup?public_url=https://your-domain.com
    Webhook will be set to {public_url}/telegram/webhook
    """
    from app.config import get_settings
    s = get_settings()
    if not s.telegram_bot_token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}

    public_url = public_url.rstrip("/")
    webhook_url = f"{public_url}/telegram/webhook"
    import urllib.request, urllib.parse, json as _json
    try:
        data = urllib.parse.urlencode({"url": webhook_url}).encode()
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/setWebhook",
            data=data, timeout=5,
        ) as r:
            res = _json.loads(r.read())
        return {"ok": res.get("ok", False), "webhook": webhook_url, "response": res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/telegram/register-commands")
def telegram_register_commands():
    """
    Register the bot command list with Telegram via setMyCommands.
    Makes commands appear in the / autocomplete menu on the user's phone.
    Idempotent — safe to call repeatedly.
    """
    from app.config import get_settings
    s = get_settings()
    if not s.telegram_bot_token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
    import urllib.request, json as _json
    payload = _json.dumps({"commands": BOT_COMMANDS}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{s.telegram_bot_token}/setMyCommands",
        data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            res = _json.loads(r.read())
        return {"ok": res.get("ok", False), "registered": len(BOT_COMMANDS), "response": res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/telegram/test")
def telegram_test():
    """Send a test message to the configured chat_id to verify outbound delivery."""
    from app.config import get_settings
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set"}
    _send(int(s.telegram_chat_id), "✅ *Stoichima* Telegram test — outbound delivery is working.")
    return {"ok": True, "chat_id": s.telegram_chat_id}


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Receive Telegram updates. Register webhook via:
    POST https://api.telegram.org/bot{TOKEN}/setWebhook?url={PUBLIC_URL}/telegram/webhook
    """
    try:
        data = await request.json()
        message = data.get("message") or data.get("edited_message")
        if not message:
            return {"ok": True}

        chat_id  = message.get("chat", {}).get("id")
        text     = (message.get("text") or "").strip()

        if not chat_id or not text.startswith("/"):
            return {"ok": True}

        parts = text.split()
        cmd   = parts[0].lower().split("@")[0]  # strip bot name suffix

        if cmd == "/predictions":
            comp = parts[1] if len(parts) > 1 else "WC"
            reply = _cmd_predictions(comp)
        elif cmd == "/live":
            reply = _cmd_live()
        elif cmd == "/today":
            reply = _cmd_today()
        elif cmd == "/best":
            try:
                n = int(parts[1]) if len(parts) > 1 else 5
            except ValueError:
                n = 5
            reply = _cmd_best(min(max(n, 1), 10))
        elif cmd == "/accuracy":
            comp = parts[1] if len(parts) > 1 else None
            reply = _cmd_accuracy(comp)
        elif cmd == "/clv":
            reply = _cmd_clv()
        elif cmd == "/match":
            if len(parts) < 3:
                reply = "Usage: `/match <home_team> <away_team>` (e.g. /match brazil morocco)"
            else:
                # Last word is away, all words between /match and last go to home
                home = " ".join(parts[1:-1])
                away = parts[-1]
                reply = _cmd_match(home, away)
        elif cmd == "/results":
            try:    hours = int(parts[1]) if len(parts) > 1 else 36
            except: hours = 36
            reply = _cmd_results(min(max(hours, 1), 168))
        elif cmd == "/props":
            if len(parts) < 3:
                reply = "Usage: `/props <home_team> <away_team>` (e.g. /props brazil morocco)"
            else:
                home = " ".join(parts[1:-1])
                away = parts[-1]
                reply = _cmd_props(home, away)
        elif cmd == "/bankroll":
            reply = _cmd_bankroll()
        elif cmd == "/slip":
            reply = _cmd_slip()
        elif cmd == "/help" or cmd == "/start":
            reply = HELP_TEXT
        else:
            reply = f"Unknown command: `{cmd}`\n\n" + HELP_TEXT

        _send(chat_id, reply)
    except Exception as exc:
        log.error("Telegram webhook error: %s", exc)

    return {"ok": True}
