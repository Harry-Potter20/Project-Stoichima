import React, { useState, useMemo, useRef, useEffect } from 'react';
import { useBetSlip } from '../context/BetSlipContext';

const API = `${process.env.REACT_APP_API_URL || 'http://localhost:8000'}/api/v1`;

// Bookmakers typically add ~8% additional margin on acca bets vs singles
const ACCA_BOOK_MARGIN = 0.92;

function kelly(prob, odds) {
  const b = odds - 1;
  if (b <= 0) return 0;
  const k = (prob * b - (1 - prob)) / b;
  return Math.max(0, k * 0.25 * 100); // quarter Kelly as %
}

function ev(prob, odds) {
  return (prob * (odds - 1) - (1 - prob)) * 100;
}

export default function BetBuilder({ bankroll = 1000 }) {
  const { legs, mode, open, removeLeg, clearSlip, setMode, toggle } = useBetSlip();
  const [stakeInput, setStakeInput]   = useState('');
  const [logging, setLogging]         = useState(false);
  const [logMsg, setLogMsg]           = useState('');
  const [notes, setNotes]             = useState('');
  const [tags, setTags]               = useState('');
  const [oddsWarn, setOddsWarn]       = useState(false);
  const [pulsing, setPulsing]         = useState(false);
  const [copied, setCopied]           = useState(false);
  const prevLegCount = useRef(legs.length);

  // Pulse badge when a new leg is added
  useEffect(() => {
    if (legs.length > prevLegCount.current) {
      setPulsing(true);
      const t = setTimeout(() => setPulsing(false), 700);
      prevLegCount.current = legs.length;
      return () => clearTimeout(t);
    }
    prevLegCount.current = legs.length;
  }, [legs.length]);

  const accaOdds = useMemo(
    () => legs.reduce((acc, l) => acc * l.odds, 1),
    [legs]
  );
  const accaProb = useMemo(
    () => legs.reduce((acc, l) => acc * l.prob, 1),
    [legs]
  );

  // Overround-adjusted acca odds: apply bookmaker acca margin compounded over legs
  const accaOddsAdjusted = useMemo(
    () => accaOdds * Math.pow(ACCA_BOOK_MARGIN, legs.length),
    [accaOdds, legs.length]
  );

  const singleStats = useMemo(() => legs.map(l => ({
    ...l,
    kelly_pct: kelly(l.prob, l.odds),
    ev_pct:    ev(l.prob, l.odds),
    stake:     (kelly(l.prob, l.odds) / 100) * bankroll,
  })), [legs, bankroll]);

  const accaStats = {
    odds:          accaOdds,
    oddsAdjusted:  accaOddsAdjusted,
    prob:          accaProb,
    kelly_pct:     kelly(accaProb, accaOddsAdjusted),
    ev_pct:        ev(accaProb, accaOddsAdjusted),
    ev_pct_raw:    ev(accaProb, accaOdds),
    stake: stakeInput
      ? parseFloat(stakeInput)
      : (kelly(accaProb, accaOddsAdjusted) / 100) * bankroll,
  };

  const hasDefaultOdds = legs.some(l => l.odds === 2.0);

  const correlations = useMemo(() => {
    const warnings = new Set();
    for (let i = 0; i < legs.length; i++) {
      for (let j = i + 1; j < legs.length; j++) {
        const a = legs[i], b = legs[j];
        if (a.homeTeam === b.homeTeam && a.awayTeam === b.awayTeam) {
          warnings.add(`${a.homeTeam} v ${a.awayTeam} — multiple selections in the same match are fully correlated`);
        } else {
          const teamsA = new Set([a.homeTeam, a.awayTeam]);
          for (const t of [b.homeTeam, b.awayTeam]) {
            if (teamsA.has(t)) warnings.add(`${t} appears in multiple legs — positively correlated, true combined probability is lower than the product`);
          }
        }
      }
    }
    return [...warnings];
  }, [legs]);

  async function logBet(leg, stakeAmt, kellyPct) {
    const payload = {
      home_team:    leg.homeTeam,
      away_team:    leg.awayTeam,
      competition:  leg.competition || 'MANUAL',
      match_date:   leg.matchDate || new Date().toISOString().slice(0, 10),
      bet_on:       leg.label,
      market:       leg.market,
      decimal_odds: leg.odds,
      model_prob:   leg.prob,
      stake_pct:    kellyPct,
      notes:        notes || undefined,
      tags:         tags || undefined,
    };
    const r = await fetch(`${API}/bets/manual`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(await r.text());
  }

  async function handleLogAll(confirmed = false) {
    if (!legs.length) return;
    if (hasDefaultOdds && !confirmed) {
      setOddsWarn(true);
      return;
    }
    setOddsWarn(false);
    setLogging(true);
    setLogMsg('');
    try {
      if (mode === 'acca') {
        const stats = accaStats;
        for (const leg of legs) {
          await logBet(leg, stats.stake / bankroll * 100, stats.kelly_pct);
        }
        setLogMsg(`✅ Acca logged (${legs.length} legs) — £${stats.stake.toFixed(2)} staked`);
      } else {
        for (const leg of singleStats) {
          await logBet(leg, leg.stake / bankroll * 100, leg.kelly_pct);
        }
        setLogMsg(`✅ ${legs.length} bet(s) logged`);
      }
      clearSlip();
      setNotes('');
      setTags('');
    } catch (e) {
      setLogMsg(`❌ Error: ${e.message}`);
    }
    setLogging(false);
  }

  function copySlip() {
    const modeLabel = mode === 'acca' ? 'ACCUMULATOR' : 'SINGLES';
    const lines = [
      `🎰 Stoichima Bet Slip — ${modeLabel}`,
      `📅 ${new Date().toLocaleDateString('en-GB')}`,
      '',
      ...legs.map((l, i) =>
        `${i + 1}. ${l.homeTeam} v ${l.awayTeam} [${l.competition}]\n   ${l.label} @${l.odds.toFixed(2)} (model: ${(l.prob * 100).toFixed(0)}%)`
      ),
    ];
    if (mode === 'acca') {
      lines.push('');
      lines.push(`Combined odds: ${accaOdds.toFixed(2)}`);
      lines.push(`Adj. odds: ${accaOddsAdjusted.toFixed(2)}`);
      lines.push(`EV (adj.): ${accaStats.ev_pct >= 0 ? '+' : ''}${accaStats.ev_pct.toFixed(1)}%`);
    }
    navigator.clipboard.writeText(lines.join('\n')).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  const hasLegs  = legs.length > 0;
  const slipCount = legs.length;

  return (
    <>
      {/* Floating slip button */}
      <button
        onClick={toggle}
        className={`fixed bottom-4 right-4 z-50 flex items-center gap-2 px-4 py-3 rounded-full font-bold shadow-lg transition-all
          ${hasLegs ? 'bg-green-500 hover:bg-green-400 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'}`}
      >
        <span>🎰 Bet Slip</span>
        {slipCount > 0 && (
          <span className="relative flex items-center justify-center">
            {pulsing && (
              <span className="absolute inline-flex w-full h-full rounded-full bg-white opacity-75 animate-ping" />
            )}
            <span className="relative bg-white text-green-700 rounded-full text-xs font-bold w-5 h-5 flex items-center justify-center">
              {slipCount}
            </span>
          </span>
        )}
      </button>

      {/* Mobile backdrop — above floating button, below panel */}
      {open && (
        <div
          className="fixed inset-0 z-[55] bg-black/50 sm:hidden"
          onClick={toggle}
        />
      )}

      {/* Slide-in panel */}
      <div
        className={`fixed inset-y-0 right-0 z-[60] flex flex-col bg-gray-900 border-l border-gray-700 shadow-2xl transition-transform duration-300
          w-full sm:w-96 ${open ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 bg-gray-800 border-b border-gray-700">
          <div className="flex items-center gap-3">
            <span className="text-green-400 font-bold text-lg">🎰 Bet Slip</span>
            {slipCount > 0 && (
              <span className="bg-green-500 text-white rounded-full text-xs font-bold px-2 py-0.5">{slipCount}</span>
            )}
          </div>
          <div className="flex gap-2">
            {slipCount > 0 && (
              <>
                <button
                  onClick={copySlip}
                  className="text-xs text-blue-400 hover:text-blue-300 border border-blue-700 px-2 py-1 rounded transition-colors"
                  title="Copy slip to clipboard"
                >
                  {copied ? '✓ Copied' : '📋 Copy'}
                </button>
                <button
                  onClick={clearSlip}
                  className="text-xs text-red-400 hover:text-red-300 border border-red-700 px-2 py-1 rounded"
                >Clear</button>
              </>
            )}
            <button onClick={toggle} className="text-gray-400 hover:text-white text-lg leading-none">✕</button>
          </div>
        </div>

        {/* Mode toggle */}
        <div className="flex px-4 pt-3 gap-2">
          {['single', 'acca'].map(m => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 py-1.5 rounded text-sm font-semibold transition-colors ${
                mode === m ? 'bg-green-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
              }`}
            >
              {m === 'single' ? 'Singles' : 'Accumulator'}
            </button>
          ))}
        </div>

        {/* Legs */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
          {!hasLegs && (
            <div className="text-center text-gray-500 py-12">
              <div className="text-4xl mb-3">🎯</div>
              <p>Click any + button in the Predictions or WC tabs to add selections</p>
            </div>
          )}
          {(mode === 'single' ? singleStats : legs).map((leg) => (
            <div key={leg.id} className="bg-gray-800 rounded-lg p-3 border border-gray-700">
              <div className="flex justify-between items-start">
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-gray-400 truncate">{leg.homeTeam} v {leg.awayTeam}</div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-white font-semibold text-sm">{leg.label}</span>
                    <span className={`font-bold ${leg.odds === 2.0 ? 'text-yellow-400' : 'text-green-400'}`}>
                      @{leg.odds.toFixed(2)}
                      {leg.odds === 2.0 && <span className="text-yellow-600 text-xs ml-0.5">*</span>}
                    </span>
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">{leg.market} · {leg.competition}</div>
                </div>
                <button
                  onClick={() => removeLeg(leg.id)}
                  className="text-gray-500 hover:text-red-400 ml-2 flex-shrink-0"
                >✕</button>
              </div>
              {mode === 'single' && (
                <div className="mt-2 pt-2 border-t border-gray-700 grid grid-cols-3 gap-1 text-xs">
                  <div>
                    <span className="text-gray-500">Kelly</span>
                    <div className="text-yellow-400 font-semibold">{leg.kelly_pct?.toFixed(1)}%</div>
                  </div>
                  <div>
                    <span className="text-gray-500">EV</span>
                    <div className={`font-semibold ${leg.ev_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {leg.ev_pct >= 0 ? '+' : ''}{leg.ev_pct?.toFixed(1)}%
                    </div>
                  </div>
                  <div>
                    <span className="text-gray-500">Stake</span>
                    <div className="text-white font-semibold">£{leg.stake?.toFixed(2)}</div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Acca summary */}
        {mode === 'acca' && hasLegs && (
          <div className="px-4 pb-2">
            <div className="bg-gray-800 rounded-lg p-3 border border-green-700 space-y-1">
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Combined odds</span>
                <span className="text-green-400 font-bold">{accaOdds.toFixed(2)}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400 flex items-center gap-1">
                  Adj. odds
                  <span className="text-gray-600 text-xs" title="Discounts for typical ~8% bookmaker acca margin">ⓘ</span>
                </span>
                <span className="text-yellow-400 font-bold">{accaStats.oddsAdjusted.toFixed(2)}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Prob (model)</span>
                <span className="text-white">{(accaProb * 100).toFixed(2)}%</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">EV (raw)</span>
                <span className={accaStats.ev_pct_raw >= 0 ? 'text-green-300' : 'text-red-300'}>
                  {accaStats.ev_pct_raw >= 0 ? '+' : ''}{accaStats.ev_pct_raw.toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between text-sm border-t border-gray-700 pt-1">
                <span className="text-gray-300 font-semibold">EV (adj.)</span>
                <span className={`font-bold ${accaStats.ev_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {accaStats.ev_pct >= 0 ? '+' : ''}{accaStats.ev_pct.toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Kelly stake</span>
                <span className="text-yellow-400">
                  {accaStats.kelly_pct.toFixed(1)}% (£{((accaStats.kelly_pct / 100) * bankroll).toFixed(2)})
                </span>
              </div>
              <input
                type="number"
                placeholder="Custom stake (£)"
                value={stakeInput}
                onChange={e => setStakeInput(e.target.value)}
                className="w-full mt-1 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white placeholder-gray-500"
              />
            </div>
            {legs.length >= 4 && (
              <p className="text-xs text-gray-600 mt-1 text-center">
                Adj. odds apply {Math.round((1 - ACCA_BOOK_MARGIN) * 100)}% margin per leg (standard acca book edge)
              </p>
            )}
          </div>
        )}

        {/* Correlation warning */}
        {correlations.length > 0 && hasLegs && (
          <div className="mx-4 mb-2 bg-orange-900/40 border border-orange-700 rounded-lg p-3 text-xs">
            <p className="text-orange-300 font-semibold mb-1">⚡ Correlated Selections</p>
            {correlations.map((w, i) => <p key={i} className="text-orange-400 mb-0.5">{w}</p>)}
            <p className="text-orange-600 mt-1">Actual EV may be lower than displayed — EV assumes independence between legs.</p>
          </div>
        )}

        {/* Odds warning */}
        {oddsWarn && (
          <div className="mx-4 mb-2 bg-yellow-900 border border-yellow-600 rounded-lg p-3 text-sm">
            <p className="text-yellow-300 font-semibold mb-1">⚠ Some selections have no bookmaker odds</p>
            <p className="text-yellow-500 text-xs mb-2">
              Legs marked with * are using 2.00 as a placeholder — EV and Kelly values may be inaccurate.
              Add real odds or continue logging for tracking purposes only.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => handleLogAll(true)}
                className="flex-1 py-1.5 bg-yellow-700 hover:bg-yellow-600 text-white rounded text-xs font-bold"
              >Log anyway</button>
              <button
                onClick={() => setOddsWarn(false)}
                className="flex-1 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs"
              >Cancel</button>
            </div>
          </div>
        )}

        {/* Notes & Tags */}
        {hasLegs && (
          <div className="px-4 pb-2 space-y-2">
            <input
              type="text"
              placeholder="Notes (optional)"
              value={notes}
              onChange={e => setNotes(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white placeholder-gray-500"
            />
            <input
              type="text"
              placeholder="Tags e.g. value,wc2026"
              value={tags}
              onChange={e => setTags(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white placeholder-gray-500"
            />
          </div>
        )}

        {/* Log button */}
        <div className="px-4 pb-4">
          {logMsg && (
            <div className={`text-sm mb-2 text-center ${logMsg.startsWith('✅') ? 'text-green-400' : 'text-red-400'}`}>
              {logMsg}
            </div>
          )}
          <button
            onClick={() => handleLogAll(false)}
            disabled={!hasLegs || logging}
            className={`w-full py-3 rounded-lg font-bold text-sm transition-colors
              ${hasLegs && !logging
                ? 'bg-green-500 hover:bg-green-400 text-white'
                : 'bg-gray-700 text-gray-500 cursor-not-allowed'}`}
          >
            {logging ? 'Logging…' : `Log ${slipCount} Bet${slipCount !== 1 ? 's' : ''} to BetLog`}
          </button>
        </div>
      </div>
    </>
  );
}
