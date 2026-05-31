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


HELP_TEXT = (
    "🤖 *Stoichima Bot Commands*\n\n"
    "/predictions [comp] — upcoming match predictions (e.g. /predictions WC)\n"
    "/bankroll — running P&L and win rate\n"
    "/slip — open value bets (edge ≥ 3%)\n"
    "/help — this message"
)


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
