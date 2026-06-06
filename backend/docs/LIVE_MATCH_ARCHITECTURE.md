# Live Match Tracking — Architecture Sketch (V2)

## Goal

While a match is in progress, continuously update the predicted outcome
distribution as new information arrives (score changes, red cards, xG
accumulation, time remaining). Surface live probability shifts to the
frontend and Telegram, and emit in-play betting alerts when sharper edges
appear during the match.

## Data sources

1. **Live score + events stream**
   - **API-Football**: `/fixtures?live=all` endpoint pushes every 15s during
     live windows. Includes goals, cards, substitutions, half/full-time.
     Free tier: limited to 100 req/day — too tight for 15s polling across
     30+ simultaneous matches. We'll need the paid Pro tier ($30/mo) or
     SofaScore unofficial scraping.
   - **SofaScore HTML scrape**: free, has live xG (shot-by-shot), more
     granular than API-Football, but breaks on UI changes.

2. **Live odds stream**
   - **The Odds API**: `/sports/{key}/odds/?regions=eu,uk&markets=h2h&dateFormat=iso`
     covers ~50 books. Free tier 500 req/month is fine for halftime polls,
     too sparse for minute-by-minute.
   - **Pinnacle in-play API**: best price for live betting. $99/mo.

3. **Internal state**: each running match needs a `LiveMatchState` row.

## Data model additions

```python
class LiveMatchState(Base):
    __tablename__ = "live_match_state"
    match_id           = Column(Integer, ForeignKey("matches.id"), primary_key=True)
    minute             = Column(Integer)
    home_score         = Column(Integer)
    away_score         = Column(Integer)
    home_xg_live       = Column(Float)   # accumulated in-play xG
    away_xg_live       = Column(Float)
    home_red_cards     = Column(Integer, default=0)
    away_red_cards     = Column(Integer, default=0)
    last_event_at      = Column(DateTime)
    status             = Column(String)  # 'NOT_STARTED', 'IN_PLAY', 'HT', 'FT'

class LiveMatchEvent(Base):
    __tablename__ = "live_match_events"
    id          = Column(Integer, primary_key=True)
    match_id    = Column(Integer, ForeignKey("matches.id"), index=True)
    minute      = Column(Integer)
    type        = Column(String)  # 'goal', 'red_card', 'penalty', 'sub', 'var_review'
    team        = Column(String)
    player      = Column(String, nullable=True)
    detail      = Column(JSON, nullable=True)
    fetched_at  = Column(DateTime, default=datetime.utcnow)

class LivePredictionSnapshot(Base):
    __tablename__ = "live_prediction_snapshots"
    id          = Column(Integer, primary_key=True)
    match_id    = Column(Integer, ForeignKey("matches.id"), index=True)
    minute      = Column(Integer)
    home_win    = Column(Float)
    draw        = Column(Float)
    away_win    = Column(Float)
    over_2_5    = Column(Float)
    btts        = Column(Float)
    book_home   = Column(Float)  # live market for CLV comparison
    book_draw   = Column(Float)
    book_away   = Column(Float)
    snapshot_at = Column(DateTime, default=datetime.utcnow)
```

## Live model

The pre-match `MatchOutcomeModel` is **not** suitable in-play because the
score is fixed in the feature set. We need a **time-aware** in-play model
whose output P(home win | minute, score, xG, red_cards) updates as state
evolves.

### Approach (V2.1 — fastest)
**Closed-form Poisson update.** We already have the Dixon-Coles model.
Given pre-match `λ_home`, `λ_away`, the remaining minutes ratio `r = (90 - minute) / 90`,
and current score `(H, A)`:
```
λ_remaining_home = λ_home * r * red_card_factor(home_reds, away_reds)
λ_remaining_away = λ_away * r * red_card_factor(away_reds, home_reds)
P(home win) = Σ_{i,j} P(i goals home rest) * P(j goals away rest) * I[H+i > A+j]
```
Plus a simple red-card multiplier (literature: ~0.5 reduction to attacking
output, ~1.3 boost to opposing). This is **deterministic, runs in <1ms per
match, and uses our existing DC parameters**. Drop-in implementation.

### Approach (V2.2 — better)
**In-play xG-driven update.** Replace remaining-minute Poisson with
xG-rate-driven Poisson: track each team's xG accumulation rate per minute,
project forward. Surface "shot momentum" as a feature.

### Approach (V2.3 — best, requires ML training)
**Sequence-to-prediction LSTM** trained on minute-by-minute event streams
from past matches (Understat 360 dataset or paid `StatsBomb open data`).
Input: 90-second slices of [score, xG, shot count, possession, cards,
formation]. Output: live probability distribution. Significantly more
work; needs ~10k matches of event-level training data.

**Recommendation: ship V2.1 first; revisit V2.3 after WC.**

## Architecture

```
┌─────────────────┐
│ Live ingest job │ ──── every 15s during match window ────┐
│ (APScheduler)   │                                        │
└─────────────────┘                                        ▼
                                                ┌──────────────────────┐
                                                │ LiveMatchState +     │
                                                │ LiveMatchEvent rows  │
                                                └──────────┬───────────┘
                                                           │
                                       ┌───────────────────┴───────────────┐
                                       ▼                                   ▼
                          ┌──────────────────────┐         ┌──────────────────────┐
                          │ Live predictor       │         │ Telegram alerter     │
                          │ (Poisson decay model)│         │ (goal scored / red   │
                          └──────────┬───────────┘         │  card / KO milestone)│
                                     │                     └──────────────────────┘
                                     ▼
                       ┌──────────────────────────┐
                       │ LivePredictionSnapshot   │  ──── pushed to frontend via
                       │ row written every minute │      Server-Sent Events
                       └──────────────────────────┘      (or WebSocket if EventSource
                                                         buffering is a problem)
```

## API additions

| Route | Purpose |
|---|---|
| `GET /api/v1/live` | All currently in-play matches with latest live snapshot |
| `GET /api/v1/live/{match_id}` | Detailed live state + 1-min snapshot history |
| `GET /api/v1/live/{match_id}/stream` | Server-Sent Events stream pushing each snapshot |
| `GET /api/v1/live/{match_id}/events` | Event timeline (goals, cards, subs) |

## Frontend additions

1. **Live tab** in App — shows all in-play matches as cards with:
   - Live score
   - Live probability bar (pulses on update)
   - Goal/red-card events as inline ticker
   - Sparkline of probability over time (last 90 minutes)
2. **Live match modal** when clicking a card:
   - Full event timeline
   - Probability trajectory chart
   - Current best in-play odds vs live model probability
   - One-click "Add in-play bet" if model edge > 4%

## Telegram

New bot commands:
- `/live` — list all in-play matches with live picks
- `/live <home> <away>` — current state + prediction for a specific match

New scheduled alerts:
- Goal scored: ping with new probabilities
- Red card: ping
- Halftime + Fulltime: summary

## Cost / feasibility

- **API-Football Pro ($30/mo)**: ~unlimited polling. **Required for live mode.**
- **The Odds API Free**: enough for 30-min cadence; live odds at higher
  frequency needs paid tier.
- **Sentry/error reporting (optional)**: helpful since the live pipeline is
  failure-prone (network, parse errors, rate limits).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| API-Football quota burn during heavy matchdays | Per-match polling tapered by minute (15s during 80–90', 30s during 0–79', 60s for HT) |
| Live model overconfident on early goals | Damp probability updates by `min(1, minute/30)` weighting against priors |
| Telegram rate limits (30 msg/sec) | Queue + batch in single message per minute |
| Stale state if scheduler crashes | Health-check endpoint + cron heartbeat; failover restart |

## V2 phased rollout

1. **Phase 1 — Foundation (1 week)**: schema, ingest job, basic Poisson live predictor, `/api/v1/live` endpoint.
2. **Phase 2 — UI (1 week)**: Live tab, sparklines, event ticker.
3. **Phase 3 — Alerts (3 days)**: Telegram goal/RC/HT/FT pings.
4. **Phase 4 — In-play betting (1 week)**: live odds integration, edge detection, auto-log to BetLog with `is_inplay=true`.
5. **Phase 5 — Advanced model (post-WC)**: V2.3 sequence model.
