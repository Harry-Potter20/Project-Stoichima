"""
APScheduler jobs for keeping fixtures and results current.

Jobs:
  refresh_fixtures  — runs at 03:00 UTC daily; pulls new upcoming matches + updates
                      statuses for all active competitions
  resolve_predictions — runs at 04:00 UTC daily; resolves any predictions whose
                        matches have now finished (feeds the accuracy tracker)
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

CLUB_COMPETITIONS   = ["PL", "PD", "BL1", "SA", "FL1"]
INTL_COMPETITIONS   = ["WC", "EC"]
INJURY_COMPETITIONS = ["PL", "PD", "BL1", "SA", "FL1"]  # API-Football supported leagues


def _refresh_fixtures():
    log.info("Scheduler: refreshing fixtures and results…")
    try:
        from app.database import SessionLocal
        from data_collection.collect_historical import (
            collect_historical_data,
            collect_international_data,
        )
        import prediction.cache as pred_cache

        for comp in CLUB_COMPETITIONS:
            try:
                collect_historical_data(comp)
                log.info("  refreshed %s", comp)
            except Exception as e:
                log.warning("  %s refresh failed: %s", comp, e)

        for comp in INTL_COMPETITIONS:
            try:
                collect_international_data(comp)
                log.info("  refreshed %s", comp)
            except Exception as e:
                log.warning("  %s refresh failed: %s", comp, e)

        # Refresh bookmaker odds
        try:
            from data_collection.odds_client import collect_odds
            for comp in CLUB_COMPETITIONS:
                collect_odds(comp)
            log.info("  odds refreshed")
        except Exception as e:
            log.warning("  odds refresh failed: %s", e)

        # Invalidate cached predictions so next request re-runs with fresh data
        pred_cache.invalidate()
        log.info("Scheduler: prediction cache cleared")

        # Immediately resolve any WC/EC predictions whose matches just finished
        try:
            from app.database import SessionLocal
            from app.models import Prediction, Match
            from datetime import datetime as _dt
            with SessionLocal() as db:
                finished = db.query(Match).filter(
                    Match.competition.in_(INTL_COMPETITIONS),
                    Match.status == "FINISHED",
                    Match.result.isnot(None),
                ).all()
                match_lut = {
                    (m.home_team, m.away_team, str(m.match_date)[:10]): m
                    for m in finished
                }
                unresolved = db.query(Prediction).filter(
                    Prediction.competition.in_(INTL_COMPETITIONS),
                    Prediction.actual_outcome.is_(None),
                ).all()
                resolved = 0
                for pred in unresolved:
                    key = (pred.home_team, pred.away_team, str(pred.match_date)[:10])
                    match = match_lut.get(key)
                    if match:
                        pred.actual_outcome  = match.result
                        pred.actual_over_2_5 = match.over_2_5_goals
                        pred.actual_btts     = match.btts
                        pred.resolved_at     = _dt.utcnow()
                        resolved += 1
                db.commit()
                log.info("Scheduler: resolved %d WC/EC predictions post-refresh", resolved)
        except Exception as exc:
            log.warning("Scheduler: WC prediction resolution failed: %s", exc)

    except Exception as e:
        log.error("Scheduler: fixture refresh error: %s", e)


def _send_telegram(text: str) -> None:
    """Fire-and-forget Telegram message. Silently ignored if token not configured."""
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return
        import urllib.request, json as _json
        url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
        payload = _json.dumps({
            "chat_id": s.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _resolve_predictions():
    log.info("Scheduler: resolving pending predictions…")
    try:
        from app.database import SessionLocal
        from app.models import Prediction, BetLog, Match, OddsSnapshot
        from datetime import datetime

        with SessionLocal() as db:
            # ── Batch-fetch finished matches once ────────────────────────────
            finished = db.query(Match).filter(
                Match.status == "FINISHED",
                Match.result.isnot(None),
            ).all()
            match_lut = {
                (m.home_team, m.away_team, str(m.match_date)[:10]): m
                for m in finished
            }

            # ── Resolve Prediction rows ──────────────────────────────────────
            unresolved = db.query(Prediction).filter(
                Prediction.actual_outcome.is_(None)
            ).all()
            resolved_count = 0
            for pred in unresolved:
                key = (pred.home_team, pred.away_team, str(pred.match_date)[:10])
                match = match_lut.get(key)
                if match and match.result is not None:
                    pred.actual_outcome  = match.result
                    pred.actual_over_2_5 = match.over_2_5_goals
                    pred.actual_btts     = match.btts
                    pred.resolved_at     = datetime.utcnow()
                    resolved_count += 1
            db.commit()
            log.info("Scheduler: resolved %d predictions", resolved_count)

            # ── Batch OddsSnapshot for closing line ──────────────────────────
            try:
                from data_collection.odds_client import _norm
                snap_rows = db.query(OddsSnapshot).all()
                snap_map = {(_norm(r.home_team), _norm(r.away_team)): r for r in snap_rows}
            except Exception:
                snap_map = {}

            # ── Resolve BetLog rows ──────────────────────────────────────────
            open_bets = db.query(BetLog).filter(BetLog.actual_result.is_(None)).all()
            bet_resolved = 0
            newly_won: list[BetLog] = []
            for bet in open_bets:
                key = (bet.home_team, bet.away_team, str(bet.match_date)[:10])
                match = match_lut.get(key)
                if not match or match.result is None:
                    continue

                bet.actual_result = match.result
                bet.won = (match.result == bet.bet_on)
                b = bet.decimal_odds - 1
                bet.profit_loss_units = (
                    round(bet.kelly_pct * b, 4) if bet.won else round(-bet.kelly_pct, 4)
                )
                bet.resolved_at = datetime.utcnow()

                # Attach closing odds and compute CLV at settlement time
                if snap_map:
                    try:
                        snap = snap_map.get((_norm(bet.home_team), _norm(bet.away_team)))
                        if snap:
                            bet.closing_home_odds = snap.home_odds
                            bet.closing_draw_odds = snap.draw_odds
                            bet.closing_away_odds = snap.away_odds
                            closing_map = {"H": snap.home_odds, "D": snap.draw_odds, "A": snap.away_odds}
                            closing = closing_map.get(bet.bet_on)
                            if closing and closing > 1 and bet.decimal_odds:
                                bet.clv_pct = round((bet.decimal_odds / closing - 1) * 100, 2)
                    except Exception:
                        pass

                if bet.won:
                    newly_won.append(bet)
                bet_resolved += 1

            db.commit()
            log.info("Scheduler: resolved %d bets", bet_resolved)

            # ── Telegram: notify winning bets + CLV alerts ───────────────────
            try:
                from app.config import get_settings
                clv_threshold = get_settings().clv_alert_threshold
            except Exception:
                clv_threshold = 5.0

            for bet in newly_won:
                pl = bet.profit_loss_units or 0
                _send_telegram(
                    f"✅ *Bet Won!*\n"
                    f"⚽ {bet.home_team} vs {bet.away_team}\n"
                    f"🎯 Bet {bet.bet_on} @{bet.decimal_odds:.2f}\n"
                    f"💰 +{pl:.2f} units | Edge was +{bet.edge_pct}%"
                )

            # CLV alert: model probability vs closing line — measures market sharpness
            for bet in open_bets:
                if bet.actual_result is None:
                    continue
                closing_map = {"H": bet.closing_home_odds, "D": bet.closing_draw_odds, "A": bet.closing_away_odds}
                closing_odds = closing_map.get(bet.bet_on)
                if not closing_odds or closing_odds <= 1 or not bet.decimal_odds:
                    continue
                # CLV = (model_odds / closing_odds - 1) * 100
                clv_pct = round((bet.decimal_odds / closing_odds - 1) * 100, 2)
                if clv_pct >= clv_threshold:
                    _send_telegram(
                        f"📈 *CLV Alert — Beat the Close!*\n"
                        f"⚽ {bet.home_team} vs {bet.away_team}\n"
                        f"🎯 Bet {bet.bet_on} @{bet.decimal_odds:.2f} → Closed @{closing_odds:.2f}\n"
                        f"📊 CLV: +{clv_pct}% | Result: {'✅ Won' if bet.won else '❌ Lost'}"
                    )

    except Exception as e:
        log.error("Scheduler: prediction resolution error: %s", e)


def _poll_lineups():
    """
    Check API-Football for confirmed starting XIs on upcoming matches.
    Lineups drop ~60 min before kickoff — polling every 30 min during matchday hours
    ensures we catch them quickly and invalidate the prediction cache.
    """
    log.info("Scheduler: polling lineups…")
    try:
        from data_collection.api_football_client import collect_lineups_for_upcoming
        from app.database import SessionLocal
        from app.models import ConfirmedLineup, Prediction, Match
        from datetime import datetime, timedelta
        import prediction.cache as pred_cache

        updated = collect_lineups_for_upcoming()
        if not updated:
            log.info("Scheduler: no new lineups")
            return

        # Find which fixtures had lineup changes in the last 30 min — these are
        # the predictions we need to regenerate.
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        with SessionLocal() as db:
            recent_lineups = (
                db.query(ConfirmedLineup)
                .filter(ConfirmedLineup.confirmed_at >= cutoff)
                .all()
            )
            affected_match_ids = {l.match_id for l in recent_lineups if l.match_id}
            n_deleted = 0
            if affected_match_ids:
                affected_matches = (
                    db.query(Match)
                    .filter(Match.id.in_(list(affected_match_ids)))
                    .all()
                )
                # Predictions are stored by (competition, home_team, away_team, match_date),
                # not by match_id directly. Delete by tuple.
                for m in affected_matches:
                    n = (
                        db.query(Prediction)
                        .filter(
                            Prediction.competition == m.competition,
                            Prediction.home_team   == m.home_team,
                            Prediction.away_team   == m.away_team,
                            Prediction.match_date  == m.match_date,
                        )
                        .delete()
                    )
                    n_deleted += n
                    # Push Telegram alert summarising the lineup
                    try:
                        _push_lineup_alert(db, m, recent_lineups)
                    except Exception as exc:
                        log.warning("lineup alert failed: %s", exc)
                db.commit()

        pred_cache.invalidate()
        log.info("Scheduler: %d lineup records updated, %d stored predictions cleared, cache busted",
                 updated, n_deleted)
    except Exception as e:
        log.error("Scheduler: lineup poll error: %s", e)


def _push_lineup_alert(db, match, recent_lineups):
    """Send a Telegram alert when a confirmed lineup drops for a tracked match."""
    from app.config import get_settings
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        return

    # Find the two confirmed lineups for this match
    home_l = next((l for l in recent_lineups if l.match_id == match.id and l.team == match.home_team), None)
    away_l = next((l for l in recent_lineups if l.match_id == match.id and l.team == match.away_team), None)
    if not (home_l or away_l):
        return

    lines = [
        f"📋 *Lineup Confirmed* — [{match.competition}]",
        f"⚽ {match.home_team} v {match.away_team}",
    ]
    if home_l:
        starters = (home_l.starters or "").split(",")[:11]
        lines.append(f"\n*{match.home_team} XI:*")
        lines.append(", ".join(s.strip() for s in starters if s.strip()))
    if away_l:
        starters = (away_l.starters or "").split(",")[:11]
        lines.append(f"\n*{match.away_team} XI:*")
        lines.append(", ".join(s.strip() for s in starters if s.strip()))
    lines.append("\n_Predictions regenerating with confirmed squads — check /match again in 30s_")

    _send_telegram("\n".join(lines))


def _fetch_closing_odds():
    """Capture closing odds for any match kicking off in the next 90 minutes."""
    log.info("Scheduler: capturing closing odds…")
    try:
        from data_collection.odds_client import fetch_closing_odds_for_upcoming, SPORT_KEY_MAP
        total = 0
        for comp in list(SPORT_KEY_MAP.keys()):
            try:
                n = fetch_closing_odds_for_upcoming(comp, within_minutes=90)
                if n:
                    log.info("  %s: %d closing-odds records updated", comp, n)
                    total += n
            except Exception as e:
                log.warning("  closing odds failed for %s: %s", comp, e)
        log.info("Scheduler: closing odds captured for %d bets", total)
    except Exception as e:
        log.error("Scheduler: closing odds error: %s", e)


def _refresh_injuries():
    """
    Pull latest injury/suspension data from API-Football for all club competitions.
    Runs at 06:00 UTC daily — after fixture refresh, before matchday starts.
    Silently skipped if API_FOOTBALL_KEY is not configured.
    """
    log.info("Scheduler: refreshing injury/availability data…")
    try:
        from data_collection.api_football_client import run as run_injuries
        run_injuries(INJURY_COMPETITIONS)
        log.info("Scheduler: injury refresh complete")
    except Exception as e:
        log.error("Scheduler: injury refresh error: %s", e)


def _send_webhook(payload: dict) -> None:
    """POST JSON payload to configured webhook URL (Discord, Slack, n8n, etc.)."""
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.webhook_url:
            return
        import urllib.request, json as _json
        data = _json.dumps(payload).encode()
        req = urllib.request.Request(
            s.webhook_url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _fire_webhooks_for_new_bets():
    """Fire webhook for any pending bets with edge >= webhook_min_edge that haven't been notified."""
    try:
        from app.config import get_settings
        from app.database import SessionLocal
        from app.models import BetLog
        s = get_settings()
        if not s.webhook_url:
            return
        with SessionLocal() as db:
            bets = db.query(BetLog).filter(
                BetLog.won.is_(None),
                BetLog.edge_pct >= s.webhook_min_edge,
                BetLog.tags.is_(None),   # use tags=None as "not yet webhook-notified" sentinel
            ).all()
            for bet in bets:
                _send_webhook({
                    "content": (
                        f"🎯 High-Edge Bet Alert\n"
                        f"⚽ {bet.home_team} vs {bet.away_team}\n"
                        f"📅 {bet.match_date.strftime('%Y-%m-%d') if bet.match_date else '?'}\n"
                        f"🎰 Bet {bet.bet_on} @ {bet.decimal_odds:.2f}\n"
                        f"📊 Edge: +{bet.edge_pct}%  |  Kelly: {bet.kelly_pct}%\n"
                        f"💰 Model: {round(bet.model_prob * 100, 1)}%  vs  "
                        f"Market: {round(bet.implied_prob * 100, 1)}%"
                    ),
                    "bet": {
                        "home_team":    bet.home_team,
                        "away_team":    bet.away_team,
                        "competition":  bet.competition,
                        "match_date":   bet.match_date.isoformat() if bet.match_date else None,
                        "bet_on":       bet.bet_on,
                        "decimal_odds": bet.decimal_odds,
                        "edge_pct":     bet.edge_pct,
                        "kelly_pct":    bet.kelly_pct,
                        "model_prob":   round(bet.model_prob * 100, 1),
                    },
                })
                bet.tags = "webhook_sent"
            db.commit()
    except Exception as e:
        log.error("Scheduler: webhook error: %s", e)


def _elo_mean_reversion():
    """
    Decay international ELO ratings toward 1500 based on inactivity.
    Run monthly — teams that haven't played in > 60 days get pulled toward 1500.
    """
    log.info("Scheduler: international ELO mean-reversion…")
    try:
        from app.database import SessionLocal
        from app.models import EloRating
        from app.config import get_settings
        from datetime import datetime, timedelta
        s = get_settings()
        rate = s.intl_elo_reversion_rate
        cutoff = datetime.utcnow() - timedelta(days=60)
        with SessionLocal() as db:
            # Only international ELO ratings (competition is None)
            intl_elos = db.query(EloRating).filter(
                EloRating.competition.is_(None),
                EloRating.as_of_date < cutoff,
            ).all()
            updated = 0
            for elo in intl_elos:
                if abs(elo.elo - 1500.0) > 1.0:
                    elo.elo = round(elo.elo + (1500.0 - elo.elo) * rate, 2)
                    elo.as_of_date = datetime.utcnow()
                    updated += 1
            db.commit()
            log.info("Scheduler: ELO mean-reversion applied to %d international teams", updated)
    except Exception as e:
        log.error("Scheduler: ELO mean-reversion error: %s", e)


def _send_daily_digest():
    """
    Send an HTML email digest with today's bets, yesterday's results, and running P&L.
    Requires SMTP settings in .env. Silently skipped if not configured.
    """
    log.info("Scheduler: sending daily email digest…")
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.smtp_host or not s.digest_email_to:
            return

        from app.database import SessionLocal
        from app.models import BetLog
        from datetime import datetime, timedelta, date

        today = date.today()
        yesterday = today - timedelta(days=1)

        with SessionLocal() as db:
            pending = db.query(BetLog).filter(
                BetLog.won.is_(None),
                BetLog.match_date >= datetime.combine(today, __import__("datetime").time.min),
            ).all()

            resolved_yesterday = db.query(BetLog).filter(
                BetLog.won.isnot(None),
                BetLog.resolved_at >= datetime.combine(yesterday, __import__("datetime").time.min),
                BetLog.resolved_at < datetime.combine(today, __import__("datetime").time.min),
            ).all()

            all_resolved = db.query(BetLog).filter(BetLog.won.isnot(None)).all()

        total_pl = round(sum(b.profit_loss_units or 0 for b in all_resolved), 2)
        yesterday_pl = round(sum(b.profit_loss_units or 0 for b in resolved_yesterday), 2)

        def _bet_rows(bets):
            rows = ""
            for b in bets:
                won_label = "✅ Won" if b.won else ("❌ Lost" if b.won is False else "⏳ Pending")
                rows += (
                    f"<tr><td>{b.home_team} v {b.away_team}</td>"
                    f"<td>{b.competition}</td>"
                    f"<td>{b.bet_on} @{b.decimal_odds:.2f}</td>"
                    f"<td>+{b.edge_pct}%</td>"
                    f"<td>{won_label}</td>"
                    f"<td>{b.profit_loss_units or '—'}</td></tr>"
                )
            return rows or "<tr><td colspan='6'>None</td></tr>"

        html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;max-width:700px">
<h2 style="color:#16a34a">⚽ Stoichima Daily Digest — {today}</h2>
<h3>Today's Bets ({len(pending)})</h3>
<table border="1" cellpadding="4" style="border-collapse:collapse;width:100%">
<tr><th>Match</th><th>Comp</th><th>Bet</th><th>Edge</th><th>Status</th><th>P&L</th></tr>
{_bet_rows(pending)}
</table>
<h3>Yesterday's Results ({len(resolved_yesterday)}) — P&L: {yesterday_pl:+.2f} units</h3>
<table border="1" cellpadding="4" style="border-collapse:collapse;width:100%">
<tr><th>Match</th><th>Comp</th><th>Bet</th><th>Edge</th><th>Result</th><th>P&L</th></tr>
{_bet_rows(resolved_yesterday)}
</table>
<h3>Running Total P&L: <span style="color:{'#16a34a' if total_pl >= 0 else '#dc2626'}">{total_pl:+.2f} units</span></h3>
<p style="color:#6b7280;font-size:12px">Stoichima prediction system — for tracking and research only.</p>
</body></html>"""

        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Stoichima Digest — {today}"
        msg["From"]    = s.smtp_user
        msg["To"]      = s.digest_email_to
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(s.smtp_host, s.smtp_port) as server:
            server.starttls()
            server.login(s.smtp_user, s.smtp_password)
            server.sendmail(s.smtp_user, s.digest_email_to, msg.as_string())

        log.info("Scheduler: daily digest sent to %s", s.digest_email_to)
    except Exception as e:
        log.error("Scheduler: daily digest error: %s", e)


def _send_weekly_telegram_digest():
    """
    Weekly P&L + accuracy digest sent to Telegram every Sunday at 20:00 UTC.
    Silently skipped if Telegram not configured.
    """
    log.info("Scheduler: sending weekly Telegram digest…")
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return

        from app.database import SessionLocal
        from app.models import BetLog, Prediction
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)

        with SessionLocal() as db:
            all_resolved = db.query(BetLog).filter(BetLog.won.isnot(None)).all()
            week_bets = [b for b in all_resolved if b.resolved_at and b.resolved_at >= week_ago]
            pending   = db.query(BetLog).filter(BetLog.won.is_(None)).count()

            resolved_preds = db.query(Prediction).filter(
                Prediction.actual_outcome.isnot(None)
            ).all()

        if not all_resolved:
            _send_telegram("📊 *Stoichima Weekly Digest*\n\nNo resolved bets yet.")
            return

        total_pl     = round(sum(b.profit_loss_units or 0 for b in all_resolved), 2)
        total_staked = round(sum(b.kelly_pct for b in all_resolved), 2)
        roi          = round(total_pl / total_staked * 100, 1) if total_staked else 0.0
        win_rate     = round(len([b for b in all_resolved if b.won]) / len(all_resolved) * 100, 1)

        week_pl      = round(sum(b.profit_loss_units or 0 for b in week_bets), 2)
        week_wins    = len([b for b in week_bets if b.won])

        # Accuracy from prediction records
        outcome_map = {"H": 0, "D": 1, "A": 2}
        brier_scores = []
        correct = 0
        for p in resolved_preds:
            y = outcome_map.get(p.actual_outcome, -1)
            if y == -1:
                continue
            probs = [p.home_win_prob, p.draw_prob, p.away_win_prob]
            brier_scores.append(sum((pi - (1.0 if i == y else 0.0))**2 for i, pi in enumerate(probs)))
            if p.predicted_outcome == p.actual_outcome:
                correct += 1

        accuracy_str = f"{round(correct / len(resolved_preds) * 100, 1)}%" if resolved_preds else "N/A"
        brier_str    = f"{round(sum(brier_scores) / len(brier_scores), 4)}" if brier_scores else "N/A"

        sign = "+" if total_pl >= 0 else ""
        week_sign = "+" if week_pl >= 0 else ""
        text = (
            f"📊 *Stoichima Weekly Digest*\n"
            f"📅 {now.strftime('%Y-%m-%d')}\n\n"
            f"*This Week* ({len(week_bets)} bets · {week_wins}W)\n"
            f"P&L: *{week_sign}{week_pl} units*\n\n"
            f"*All-Time*\n"
            f"Bets: {len(all_resolved)} resolved | {pending} pending\n"
            f"P&L: *{sign}{total_pl} units* | ROI: {sign}{roi}%\n"
            f"Win Rate: {win_rate}%\n\n"
            f"*Model Accuracy* ({len(resolved_preds)} predictions)\n"
            f"Correct: {accuracy_str} | Brier: {brier_str}\n"
            f"_(Brier baseline: 0.6667 — lower is better)_"
        )
        _send_telegram(text)
        log.info("Scheduler: weekly Telegram digest sent")
    except Exception as e:
        log.error("Scheduler: weekly digest error: %s", e)


def _live_score_refresh():
    """
    Refresh WC/EC scores every 5 min during matchday hours (14:00–23:00 UTC).
    Resolves any predictions that just finished and busts the prediction cache.
    """
    log.info("Scheduler: live WC/EC score refresh…")
    try:
        from data_collection.collect_historical import collect_international_data
        import prediction.cache as pred_cache

        updated = 0
        for comp in INTL_COMPETITIONS:
            try:
                collect_international_data(comp)
                updated += 1
            except Exception as exc:
                log.warning("  live %s refresh failed: %s", comp, exc)

        if updated:
            pred_cache.invalidate()

        # Immediately resolve any predictions that just finished
        try:
            from app.database import SessionLocal
            from app.models import Prediction, Match
            from datetime import datetime as _dt
            with SessionLocal() as db:
                finished = db.query(Match).filter(
                    Match.competition.in_(INTL_COMPETITIONS),
                    Match.status == "FINISHED",
                    Match.result.isnot(None),
                ).all()
                match_lut = {(m.home_team, m.away_team, str(m.match_date)[:10]): m for m in finished}
                unresolved = db.query(Prediction).filter(
                    Prediction.competition.in_(INTL_COMPETITIONS),
                    Prediction.actual_outcome.is_(None),
                ).all()
                n = 0
                for pred in unresolved:
                    key = (pred.home_team, pred.away_team, str(pred.match_date)[:10])
                    m = match_lut.get(key)
                    if m:
                        pred.actual_outcome  = m.result
                        pred.actual_over_2_5 = m.over_2_5_goals
                        pred.actual_btts     = m.btts
                        pred.resolved_at     = _dt.utcnow()
                        n += 1
                if n:
                    db.commit()
                    log.info("Scheduler: live refresh resolved %d predictions", n)
        except Exception as exc:
            log.warning("Scheduler: live resolve failed: %s", exc)

    except Exception as e:
        log.error("Scheduler: live score refresh error: %s", e)


def _retrain_models():
    """Retrain all ML models. Runs in a background thread so the scheduler isn't blocked."""
    import threading, subprocess, os
    log.info("Scheduler: starting monthly model retrain…")

    def _run():
        try:
            result = subprocess.run(
                ["python", "train_models.py"],
                cwd=os.getcwd(),
                capture_output=True, text=True, timeout=7200,
            )
            if result.returncode == 0:
                log.info("Scheduler: retrain complete")
                _send_telegram("🔄 *Models retrained successfully* — monthly update complete")
            else:
                log.error("Scheduler: retrain failed\n%s", result.stderr[-500:])
        except Exception as e:
            log.error("Scheduler: retrain error: %s", e)

    threading.Thread(target=_run, daemon=True).start()


def _refresh_daily_friendlies():
    """
    Daily ingest of FRIENDLY + other API-Football-tracked competitions.
    Runs once quota resets to capture overnight results so /results works
    against fresh data.
    """
    try:
        from data_collection.api_football_daily import run, LEAGUE_MAP
        # Pull the last 2 days (catches anything that finished overnight) + next 4
        from datetime import date as _date, timedelta as _td
        log.info("Scheduler: refreshing FRIENDLY/MAR/BSA/etc. fixtures …")
        result = run(list(LEAGUE_MAP.keys()), days=6)
        log.info("Scheduler: daily fixture refresh — %d merged", result.get("total", 0))

        # Re-resolve predictions for any matches that just transitioned to FINISHED
        _resolve_predictions()

        # Push a morning digest with predictions-vs-actuals so the user sees model
        # performance over their morning coffee.
        try:
            _send_morning_results_digest()
        except Exception as exc:
            log.warning("morning digest failed: %s", exc)
    except Exception as e:
        log.error("Scheduler: friendly refresh error: %s", e)


def _send_morning_results_digest():
    """Telegram digest of yesterday's results + accuracy across all matches that resolved."""
    from app.config import get_settings
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        return

    from app.database import SessionLocal
    from app.models import Prediction
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(hours=36)
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
        log.info("morning digest: no newly-resolved predictions")
        return

    correct = sum(1 for p in resolved if p.predicted_outcome == p.actual_outcome)
    n = len(resolved)
    acc = correct / n * 100
    by_comp = {}
    for p in resolved:
        by_comp.setdefault(p.competition, []).append(p)

    lines = [
        f"☕ *Overnight Results* — Model vs Actual",
        f"_{n} matches resolved · {correct}/{n} correct ({acc:.0f}%)_\n",
    ]
    for comp, preds in by_comp.items():
        comp_correct = sum(1 for p in preds if p.predicted_outcome == p.actual_outcome)
        lines.append(f"\n*{comp}* — {comp_correct}/{len(preds)} correct")
        for p in sorted(preds, key=lambda x: x.match_date):
            mark = "✅" if p.predicted_outcome == p.actual_outcome else "❌"
            top_prob = max(p.home_win_prob, p.draw_prob, p.away_win_prob)
            lines.append(
                f"  {mark} {p.home_team} v {p.away_team}  → predicted *{p.predicted_outcome}* "
                f"({top_prob*100:.0f}%) · actual *{p.actual_outcome}*"
            )

    _send_telegram("\n".join(lines))
    log.info("morning digest: pushed %d match comparisons", n)


def _live_odds_poll():
    """Poll The Odds API for in-play odds and log any edge ≥ 5pp as inplay bets."""
    try:
        from data_collection.live_odds_client import run_live_odds_poll
        result = run_live_odds_poll()
        if result.get("edges_logged"):
            log.info("Live odds poll: %d in-play edges logged", result["edges_logged"])
    except Exception as e:
        log.error("Live odds poll error: %s", e)


def _live_match_poll():
    """
    Poll API-Football for all currently in-play fixtures and update LiveMatchState,
    record events, and write a 1-minute LivePredictionSnapshot per match.
    Silently no-ops if API_FOOTBALL_KEY is missing.
    """
    try:
        from data_collection.live_match_ingest import run_live_poll
        result = run_live_poll()
        if result.get("ok"):
            n = result.get("live_count", 0)
            if n:
                log.info("Live poll: %d in-play, %d snaps, %d events",
                         n, result.get("snapshots_written", 0), result.get("events_added", 0))
    except Exception as e:
        log.error("Live poll error: %s", e)


def _send_daily_wc_digest():
    """
    Morning Telegram digest of today's WC fixtures with model probabilities.
    Runs once at 08:00 UTC — silently skipped if Telegram not configured or no WC matches today.
    """
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return

        from app.database import SessionLocal
        from app.models import Match, Prediction
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        end = now + timedelta(hours=24)

        with SessionLocal() as db:
            todays = (
                db.query(Match)
                .filter(
                    Match.competition.in_(INTL_COMPETITIONS),
                    Match.status.in_(["SCHEDULED", "TIMED"]),
                    Match.match_date >= now,
                    Match.match_date <= end,
                )
                .order_by(Match.match_date)
                .all()
            )
            if not todays:
                return

            pred_lut = {
                (p.home_team, p.away_team, str(p.match_date)[:10]): p
                for p in db.query(Prediction)
                .filter(Prediction.competition.in_(INTL_COMPETITIONS))
                .all()
            }

        lines = [f"🌍 *Today's International Matches* ({now.strftime('%Y-%m-%d')})\n"]
        for m in todays:
            key = (m.home_team, m.away_team, str(m.match_date)[:10])
            p = pred_lut.get(key)
            ko = m.match_date.strftime("%H:%M") if m.match_date else "?"
            if p:
                h, d, a = (
                    f"{(p.home_win_prob or 0)*100:.0f}%",
                    f"{(p.draw_prob or 0)*100:.0f}%",
                    f"{(p.away_win_prob or 0)*100:.0f}%",
                )
                lines.append(
                    f"⏰ {ko} UTC | [{m.competition}]\n"
                    f"⚽ *{m.home_team}* vs *{m.away_team}*\n"
                    f"H {h} · D {d} · A {a} → *{p.predicted_outcome or '?'}*\n"
                )
            else:
                lines.append(
                    f"⏰ {ko} UTC | [{m.competition}]\n"
                    f"⚽ *{m.home_team}* vs *{m.away_team}*\n"
                    f"_(no prediction yet — hit /predictions {m.competition} to generate)_\n"
                )
        _send_telegram("\n".join(lines))
        log.info("Scheduler: daily WC digest sent (%d matches)", len(todays))
    except Exception as e:
        log.error("Scheduler: WC digest error: %s", e)


def _send_tournament_preview(n_matches: int = 8):
    """
    Send a Telegram message listing the next N upcoming WC/EC matches with
    model probabilities. Unlike _send_daily_wc_digest, this ignores the 24h
    window — useful as a manual 'what's coming up' trigger.
    """
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return

        from app.database import SessionLocal
        from app.models import Match, Prediction
        from datetime import datetime

        with SessionLocal() as db:
            upcoming = (
                db.query(Match)
                .filter(
                    Match.competition.in_(INTL_COMPETITIONS),
                    Match.status.in_(["SCHEDULED", "TIMED"]),
                    Match.match_date >= datetime.utcnow(),
                )
                .order_by(Match.match_date)
                .limit(n_matches)
                .all()
            )
            if not upcoming:
                _send_telegram("🌍 *Tournament Preview*\n\nNo upcoming WC/EC matches in the DB.")
                return

            pred_lut = {
                (p.home_team, p.away_team, str(p.match_date)[:10]): p
                for p in db.query(Prediction)
                .filter(Prediction.competition.in_(INTL_COMPETITIONS))
                .all()
            }

        lines = [f"🌍 *Upcoming {len(upcoming)} Matches*\n"]
        for m in upcoming:
            key = (m.home_team, m.away_team, str(m.match_date)[:10])
            p = pred_lut.get(key)
            ko = m.match_date.strftime("%a %b %d %H:%M") if m.match_date else "?"
            if p and p.home_win_prob is not None:
                h = f"{p.home_win_prob*100:.0f}%"
                d = f"{p.draw_prob*100:.0f}%"
                a = f"{p.away_win_prob*100:.0f}%"
                lines.append(
                    f"📅 {ko} UTC | [{m.competition}]\n"
                    f"⚽ *{m.home_team}* vs *{m.away_team}*\n"
                    f"H {h} · D {d} · A {a} → *{p.predicted_outcome or '?'}*\n"
                )
            else:
                lines.append(
                    f"📅 {ko} UTC | [{m.competition}]\n"
                    f"⚽ *{m.home_team}* vs *{m.away_team}*\n"
                    f"_(prediction pending)_\n"
                )
        _send_telegram("\n".join(lines))
        log.info("Scheduler: tournament preview sent (%d matches)", len(upcoming))
    except Exception as e:
        log.error("Scheduler: tournament preview error: %s", e)


def _fire_telegram_for_new_high_edge_bets():
    """
    Per-bet Telegram alert for new high-edge pending bets. Uses the BetLog.tags
    field as a sentinel — `tg_alerted` flag tracks rows already pushed.
    Threshold reuses settings.telegram_min_edge.
    """
    try:
        from app.config import get_settings
        from app.database import SessionLocal
        from app.models import BetLog
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return

        with SessionLocal() as db:
            bets = (
                db.query(BetLog)
                .filter(
                    BetLog.won.is_(None),
                    BetLog.edge_pct >= s.telegram_min_edge,
                )
                .all()
            )
            new_bets = [b for b in bets if not (b.tags and "tg_alerted" in b.tags)]
            for bet in new_bets:
                ko = bet.match_date.strftime("%Y-%m-%d %H:%M") if bet.match_date else "?"
                _send_telegram(
                    f"🎯 *High-Edge Bet* [{bet.competition}]\n"
                    f"⚽ {bet.home_team} vs {bet.away_team}\n"
                    f"⏰ {ko} UTC\n"
                    f"🎰 *{bet.bet_on}* @{bet.decimal_odds:.2f}\n"
                    f"📊 Edge: +{bet.edge_pct}% | Kelly: {bet.kelly_pct}%\n"
                    f"💡 Model: {round((bet.model_prob or 0)*100, 1)}% vs "
                    f"Market: {round((bet.implied_prob or 0)*100, 1)}%"
                )
                bet.tags = (bet.tags or "") + ("," if bet.tags else "") + "tg_alerted"
            if new_bets:
                db.commit()
                log.info("Scheduler: pushed %d high-edge Telegram alerts", len(new_bets))
    except Exception as e:
        log.error("Scheduler: telegram alert error: %s", e)


def _send_prematch_telegram_reminders():
    """
    30 minutes before kickoff, push a Telegram reminder for WC/EC matches with
    a stored prediction. Uses Prediction row's resolved_at NULL as 'still pending'.
    """
    try:
        from app.config import get_settings
        from app.database import SessionLocal
        from app.models import Match, Prediction
        from datetime import datetime, timedelta
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return

        now = datetime.utcnow()
        # Window: matches starting in the next 30–45 minutes (avoids duplicate fires every 15 min)
        soon_lo = now + timedelta(minutes=30)
        soon_hi = now + timedelta(minutes=45)

        with SessionLocal() as db:
            matches = (
                db.query(Match)
                .filter(
                    Match.competition.in_(INTL_COMPETITIONS),
                    Match.status.in_(["SCHEDULED", "TIMED"]),
                    Match.match_date >= soon_lo,
                    Match.match_date <  soon_hi,
                )
                .all()
            )
            if not matches:
                return
            for m in matches:
                p = (
                    db.query(Prediction)
                    .filter(
                        Prediction.competition == m.competition,
                        Prediction.home_team == m.home_team,
                        Prediction.away_team == m.away_team,
                        Prediction.match_date == m.match_date,
                    )
                    .first()
                )
                if not p:
                    continue
                ko = m.match_date.strftime("%H:%M")
                h, d, a = (
                    f"{(p.home_win_prob or 0)*100:.0f}%",
                    f"{(p.draw_prob or 0)*100:.0f}%",
                    f"{(p.away_win_prob or 0)*100:.0f}%",
                )
                _send_telegram(
                    f"⏱️ *Kickoff in ~30 min* [{m.competition}]\n"
                    f"⚽ *{m.home_team}* vs *{m.away_team}*\n"
                    f"⏰ {ko} UTC\n"
                    f"H {h} · D {d} · A {a} → *{p.predicted_outcome or '?'}*\n"
                    f"O2.5: {round((p.over_2_5_prob or 0)*100)}% | BTTS: {round((p.btts_prob or 0)*100)}%"
                )
            log.info("Scheduler: pre-match reminders sent for %d matches", len(matches))
    except Exception as e:
        log.error("Scheduler: prematch reminder error: %s", e)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Refresh fixtures & results every day at 03:00 UTC
    scheduler.add_job(
        _refresh_fixtures,
        trigger=CronTrigger(hour=3, minute=0),
        id="refresh_fixtures",
        name="Daily fixture refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Resolve predictions every day at 04:00 UTC (after fixtures are fresh)
    scheduler.add_job(
        _resolve_predictions,
        trigger=CronTrigger(hour=4, minute=0),
        id="resolve_predictions",
        name="Daily prediction resolution",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Fetch closing odds for matches kicking off within 90 min (every 30 min, matchday hours)
    scheduler.add_job(
        _fetch_closing_odds,
        trigger=CronTrigger(hour="10-23", minute="0,30"),
        id="fetch_closing_odds",
        name="Closing line capture",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Poll for confirmed lineups every 30 min during matchday hours (11:00–23:00 UTC)
    scheduler.add_job(
        _poll_lineups,
        trigger=CronTrigger(hour="11-23", minute="0,30"),
        id="poll_lineups",
        name="Lineup confirmation polling",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Daily FRIENDLY + lower-league refresh at 01:30 UTC — runs after the
    # API-Football quota resets at 00:00 UTC. Pulls overnight results then
    # fires the morning digest comparing predictions vs actuals.
    scheduler.add_job(
        _refresh_daily_friendlies,
        trigger=CronTrigger(hour=1, minute=30),
        id="refresh_friendlies",
        name="Daily FRIENDLY fixture + results refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Refresh injury/availability data daily at 06:00 UTC
    scheduler.add_job(
        _refresh_injuries,
        trigger=CronTrigger(hour=6, minute=0),
        id="refresh_injuries",
        name="Daily injury refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Retrain all models on the 1st of each month at 02:00 UTC
    scheduler.add_job(
        _retrain_models,
        trigger=CronTrigger(day=1, hour=2, minute=0),
        id="monthly_retrain",
        name="Monthly model retrain",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    # Webhook: fire for any new high-edge pending bets every 30 min
    scheduler.add_job(
        _fire_webhooks_for_new_bets,
        trigger=CronTrigger(minute="0,30"),
        id="webhook_alerts",
        name="Webhook high-edge alerts",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # International ELO mean-reversion on the 1st of each month at 01:00 UTC
    scheduler.add_job(
        _elo_mean_reversion,
        trigger=CronTrigger(day=1, hour=1, minute=0),
        id="elo_reversion",
        name="International ELO mean-reversion",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Daily email digest at 07:30 UTC
    scheduler.add_job(
        _send_daily_digest,
        trigger=CronTrigger(hour=7, minute=30),
        id="daily_digest",
        name="Daily email digest",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Live WC/EC score refresh every 5 min during matchday hours (14:00–23:00 UTC)
    scheduler.add_job(
        _live_score_refresh,
        trigger=CronTrigger(hour="14-23", minute="*/5"),
        id="live_score_refresh",
        name="Live WC/EC score refresh",
        replace_existing=True,
        misfire_grace_time=120,
    )

    # Weekly Telegram P&L + accuracy digest — every Sunday at 20:00 UTC
    scheduler.add_job(
        _send_weekly_telegram_digest,
        trigger=CronTrigger(day_of_week="sun", hour=20, minute=0),
        id="weekly_telegram_digest",
        name="Weekly Telegram digest",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Daily WC/EC Telegram digest — 08:00 UTC, day-of fixture list with probabilities
    scheduler.add_job(
        _send_daily_wc_digest,
        trigger=CronTrigger(hour=8, minute=0),
        id="daily_wc_digest",
        name="Daily WC Telegram digest",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Per-bet Telegram alert for high-edge picks — every 30 min during matchday hours
    scheduler.add_job(
        _fire_telegram_for_new_high_edge_bets,
        trigger=CronTrigger(hour="10-23", minute="5,35"),
        id="telegram_high_edge_alerts",
        name="Telegram high-edge bet alerts",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # In-play odds polling every 3 minutes during matchday hours. The Odds API
    # free tier is 500 req/month; this consumes ~260/day during heavy WC days
    # so will exhaust quickly — that's acceptable for the tournament window.
    scheduler.add_job(
        _live_odds_poll,
        trigger=CronTrigger(hour="10-23", minute="*/3"),
        id="live_odds_poll",
        name="Live odds + in-play edge detection",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Live match polling — every minute during matchday hours (10:00–23:00 UTC).
    # Uses API-Football's /fixtures?live=all endpoint. Free tier quota is tight
    # (100 req/day) — this consumes ~13 req/hour during the 13-hour window.
    scheduler.add_job(
        _live_match_poll,
        trigger=CronTrigger(hour="10-23", minute="*"),
        id="live_match_poll",
        name="Live match state polling",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Pre-match Telegram reminders — every 15 min during matchday hours
    # Fires for matches starting in 30–45 min so 15-min cadence catches each match once.
    scheduler.add_job(
        _send_prematch_telegram_reminders,
        trigger=CronTrigger(hour="10-23", minute="*/15"),
        id="prematch_reminders",
        name="Pre-match Telegram reminders",
        replace_existing=True,
        misfire_grace_time=300,
    )

    return scheduler
