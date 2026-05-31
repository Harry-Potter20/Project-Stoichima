import React, { useState, useEffect } from 'react';
import SpreadSelector from './SpreadSelector';
import TeamFormChart from './TeamFormChart';
import AddToSlipBtn from './AddToSlipBtn';

function ProbBar({ home, draw, away }) {
  const fmt = v => `${(v * 100).toFixed(0)}%`;
  return (
    <div className="w-full">
      <div className="flex h-4 rounded overflow-hidden text-xs font-bold">
        <div style={{ width: `${home * 100}%` }} className="bg-green-500 flex items-center justify-center text-white truncate px-1">{fmt(home)}</div>
        <div style={{ width: `${draw * 100}%` }} className="bg-yellow-500 flex items-center justify-center text-white truncate px-1">{fmt(draw)}</div>
        <div style={{ width: `${away * 100}%` }} className="bg-red-500 flex items-center justify-center text-white truncate px-1">{fmt(away)}</div>
      </div>
      <div className="flex justify-between text-xs text-gray-400 mt-0.5">
        <span>H</span><span>D</span><span>A</span>
      </div>
    </div>
  );
}

function Badge({ label, color }) {
  const colors = {
    green:  'bg-green-600 text-white',
    red:    'bg-red-600 text-white',
    yellow: 'bg-yellow-500 text-black',
    blue:   'bg-blue-600 text-white',
    gray:   'bg-gray-600 text-white',
  };
  return <span className={`px-2 py-0.5 rounded text-xs font-semibold ${colors[color] || colors.gray}`}>{label}</span>;
}

function renderMarketCell(prediction, market, subOption) {
  const m = prediction.markets;

  if (market === 'outcome') {
    const { home_win_prob: h, draw_prob: d, away_win_prob: a } = prediction.outcome;
    return <ProbBar home={h} draw={d} away={a} />;
  }

  if (market === 'over_under') {
    const line = subOption || '2.5';
    const data = m.over_under?.[line];
    if (!data) return <span className="text-gray-500 text-xs">—</span>;
    const over = (data.over * 100).toFixed(1);
    const under = (data.under * 100).toFixed(1);
    return (
      <div className="text-xs">
        <span className="text-green-400">O{line}: {over}%</span>
        <span className="text-gray-400 mx-1">/</span>
        <span className="text-red-400">U{line}: {under}%</span>
      </div>
    );
  }

  if (market === 'asian_handicap') {
    const line = subOption || '-0.5';
    const data = m.asian_handicap?.[line];
    if (!data) return <span className="text-gray-500 text-xs">—</span>;
    return (
      <div className="text-xs">
        <span className="text-green-400">Home: {(data.home * 100).toFixed(1)}%</span>
        <span className="text-gray-400 mx-1">/</span>
        <span className="text-red-400">Away: {(data.away * 100).toFixed(1)}%</span>
      </div>
    );
  }

  if (market === 'correct_score') {
    const top3 = (m.correct_score || []).slice(0, 3);
    return (
      <div className="text-xs space-y-0.5">
        {top3.map((s, i) => (
          <div key={i} className="flex gap-1 items-center">
            <span className="text-white font-mono">{s.home_goals}-{s.away_goals}</span>
            <span className="text-gray-400">{(s.probability * 100).toFixed(1)}%</span>
          </div>
        ))}
      </div>
    );
  }

  if (market === 'double_chance') {
    const dc = m.double_chance;
    if (!dc) return null;
    return (
      <div className="text-xs space-y-0.5">
        <div><span className="text-gray-400">1X </span><span className="text-green-400">{(dc['1X'] * 100).toFixed(1)}%</span></div>
        <div><span className="text-gray-400">X2 </span><span className="text-blue-400">{(dc['X2'] * 100).toFixed(1)}%</span></div>
        <div><span className="text-gray-400">12 </span><span className="text-yellow-400">{(dc['12'] * 100).toFixed(1)}%</span></div>
      </div>
    );
  }

  if (market === 'btts') {
    const yes = m.btts?.yes ?? 0;
    const color = yes >= 0.5 ? 'green' : 'red';
    return <Badge label={`${yes >= 0.5 ? 'Yes' : 'No'} ${(yes * 100).toFixed(1)}%`} color={color} />;
  }

  if (market === 'odds_edge') {
    return <OddsEdgeCell prediction={prediction} />;
  }

  return null;
}

function OddsEdgeCell({ prediction }) {
  const odds = prediction.odds;
  const edge = prediction.edge;
  const kelly = prediction.kelly;

  if (!odds) return <span className="text-gray-600 text-xs">No odds</span>;

  const outcomes = [
    { label: 'H', odds: odds.home, edge: edge?.home, kelly: kelly?.home, color: 'text-green-400' },
    { label: 'D', odds: odds.draw, edge: edge?.draw, kelly: kelly?.draw, color: 'text-yellow-400' },
    { label: 'A', odds: odds.away, edge: edge?.away, kelly: kelly?.away, color: 'text-red-400'   },
  ];

  return (
    <div className="space-y-1">
      {outcomes.map(({ label, odds: o, edge: e, kelly: k, color }) => (
        <div key={label} className="flex items-center gap-2 text-xs">
          <span className={`font-bold w-3 ${color}`}>{label}</span>
          <span className="text-gray-300 tabular-nums w-10">{o?.toFixed(2) ?? '—'}</span>
          {e != null && (
            <span className={`tabular-nums font-semibold ${e >= 2 ? 'text-green-400' : e >= 0 ? 'text-gray-400' : 'text-red-400'}`}>
              {e >= 0 ? '+' : ''}{e}%
            </span>
          )}
          {k != null && k > 0 && (
            <span className="text-blue-400 tabular-nums">Kelly {k}%</span>
          )}
        </div>
      ))}
      {odds.bookmaker && (
        <div className="text-gray-600 text-xs">{odds.bookmaker}</div>
      )}
    </div>
  );
}

function OddsMovement({ odds, openingOdds }) {
  if (!odds || !openingOdds) return null;
  const pairs = [
    { label: 'H', current: odds.home, opening: openingOdds.home, color: 'text-green-400' },
    { label: 'D', current: odds.draw, opening: openingOdds.draw, color: 'text-yellow-400' },
    { label: 'A', current: odds.away, opening: openingOdds.away, color: 'text-red-400' },
  ].filter(p => p.current && p.opening);

  if (!pairs.length) return null;

  return (
    <div className="flex gap-2 flex-wrap mt-1">
      {pairs.map(({ label, current, opening, color }) => {
        const move = current / opening;
        const shortened = move < 0.95;
        const drifted   = move > 1.05;
        if (!shortened && !drifted) return null;
        return (
          <span key={label} className={`text-xs font-mono ${color}`} title={`Opening: ${opening.toFixed(2)} → Now: ${current.toFixed(2)}`}>
            {label} {shortened ? '▼' : '▲'} {opening.toFixed(2)}→{current.toFixed(2)}
          </span>
        );
      })}
    </div>
  );
}

function SignalBadges({ signals }) {
  if (!signals) return null;
  const badges = [];
  if (signals.home_steam || signals.away_steam) {
    const side = signals.home_steam && signals.away_steam ? 'Both' : signals.home_steam ? 'Home' : 'Away';
    const pct  = signals.max_steam_pct != null ? ` ${signals.max_steam_pct.toFixed(0)}%` : '';
    badges.push(
      <span key="steam" title="Sharp money line movement detected"
        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-semibold bg-orange-600 text-white">
        🔥 {side} steam{pct}
      </span>
    );
  }
  if ((signals.match_importance ?? 0) > 0.5) {
    badges.push(
      <span key="importance" title={`Match importance: ${(signals.match_importance * 100).toFixed(0)}%`}
        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-semibold bg-red-700 text-white">
        ⚔️ 6-pointer
      </span>
    );
  }
  if (signals.home_newly_promoted || signals.away_newly_promoted) {
    const side = signals.home_newly_promoted && signals.away_newly_promoted ? 'Both' : signals.home_newly_promoted ? 'Home' : 'Away';
    badges.push(
      <span key="promoted" title="Newly promoted team — stats regressed toward league average"
        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-semibold bg-blue-600 text-white">
        ↑ {side} promoted
      </span>
    );
  }
  if (!badges.length) return null;
  return <div className="flex gap-1 flex-wrap mt-1">{badges}</div>;
}

const FEATURE_LABELS = {
  home_form:              'Home form',
  away_form:              'Away form',
  home_goals_scored_avg:  'Home goals scored (avg)',
  home_goals_conceded_avg:'Home goals conceded (avg)',
  away_goals_scored_avg:  'Away goals scored (avg)',
  away_goals_conceded_avg:'Away goals conceded (avg)',
  h2h_home_win_rate:      'H2H home win rate',
  h2h_draw_rate:          'H2H draw rate',
  h2h_goals_avg:          'H2H goals avg',
  home_xg_avg:            'Home xG (avg)',
  away_xg_avg:            'Away xG (avg)',
  home_shot_quality_avg:  'Home shot quality',
  away_shot_quality_avg:  'Away shot quality',
  xg_diff:                'xG difference',
  shot_quality_diff:      'Shot quality diff',
  home_days_rest:         'Home rest (days)',
  away_days_rest:         'Away rest (days)',
  rest_advantage:         'Rest advantage',
  home_elo:               'Home ELO',
  away_elo:               'Away ELO',
  elo_diff:               'ELO difference',
  // New features
  home_ha_score:          'Home advantage (rolling)',
  away_ha_score:          'Away advantage (rolling)',
  season_progress:        'Season progress',
  mkt_open_home_prob:     'Opening line (home)',
  mkt_open_draw_prob:     'Opening line (draw)',
  mkt_open_away_prob:     'Opening line (away)',
  ref_avg_yellow:             'Referee yellow cards (avg)',
  ref_avg_red:                'Referee red cards (avg)',
  ref_home_win_rate:          'Referee home win rate',
  // New features
  home_win_streak:            'Home win streak',
  away_win_streak:            'Away win streak',
  home_unbeaten:              'Home unbeaten run',
  away_unbeaten:              'Away unbeaten run',
  home_draw_rate:             'Home draw rate',
  away_draw_rate:             'Away draw rate',
  home_corners_avg:           'Home corners (avg)',
  away_corners_avg:           'Away corners (avg)',
  home_table_pos:             'Home table position',
  away_table_pos:             'Away table position',
  home_pts_gap_top4:          'Home gap to top-4',
  away_pts_gap_top4:          'Away gap to top-4',
  home_pts_gap_rel:           'Home gap to relegation',
  away_pts_gap_rel:           'Away gap to relegation',
  mkt_close_home_prob:        'Closing line (home)',
  mkt_close_draw_prob:        'Closing line (draw)',
  mkt_close_away_prob:        'Closing line (away)',
  home_odds_move:             'Home odds movement',
  away_odds_move:             'Away odds movement',
  home_steam:                 'Home steam move',
  away_steam:                 'Away steam move',
  home_newly_promoted:        'Home: newly promoted',
  away_newly_promoted:        'Away: newly promoted',
  home_title_pressure:        'Home title pressure',
  away_title_pressure:        'Away title pressure',
  home_relegation_danger:     'Home relegation danger',
  away_relegation_danger:     'Away relegation danger',
  match_importance:           'Match importance',
  home_days_since_mgr_change: 'Home days since manager change',
  away_days_since_mgr_change: 'Away days since manager change',
  congestion_diff:            'Fixture congestion diff',
};

function ShapPanel({ features, predictedOutcome }) {
  if (!features || features.length === 0) {
    return <div className="text-gray-500 text-xs py-2">SHAP explanations not available for this prediction.</div>;
  }
  const maxShap = Math.max(...features.map(f => Math.abs(f.shap)));
  return (
    <div>
      <div className="text-xs text-gray-400 mb-2">
        Top drivers for predicted outcome&nbsp;
        <span className={`font-bold ${predictedOutcome === 'H' ? 'text-green-400' : predictedOutcome === 'A' ? 'text-red-400' : 'text-yellow-400'}`}>
          {predictedOutcome === 'H' ? 'Home Win' : predictedOutcome === 'A' ? 'Away Win' : 'Draw'}
        </span>
      </div>
      <div className="space-y-1.5">
        {features.map((f, i) => {
          const pct = maxShap > 0 ? Math.abs(f.shap) / maxShap * 100 : 0;
          const positive = f.shap > 0;
          return (
            <div key={i} className="flex items-center gap-2">
              <div className="w-40 text-xs text-gray-300 truncate shrink-0">
                {FEATURE_LABELS[f.feature] || f.feature}
              </div>
              <div className="flex-1 flex items-center gap-1">
                <div className="flex-1 bg-gray-700 rounded h-3 overflow-hidden">
                  <div
                    style={{ width: `${pct}%` }}
                    className={`h-full rounded ${positive ? 'bg-green-500' : 'bg-red-500'}`}
                  />
                </div>
                <span className={`text-xs tabular-nums w-14 text-right ${positive ? 'text-green-400' : 'text-red-400'}`}>
                  {positive ? '+' : ''}{f.shap.toFixed(3)}
                </span>
              </div>
              <div className="text-xs text-gray-500 w-14 text-right tabular-nums shrink-0">
                {f.raw_value}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


function XgRangeBar({ xgRange }) {
  if (!xgRange || !xgRange.home_xg) return null;
  return (
    <div className="flex gap-3 text-xs mt-1.5 text-gray-400">
      <span>xG: <span className="text-green-400">{xgRange.home_xg}</span> – <span className="text-red-400">{xgRange.away_xg}</span></span>
      <span className="text-gray-600">Total: {xgRange.total_xg}</span>
    </div>
  );
}

function DataQualityBadge({ dataQuality }) {
  if (!dataQuality?.low_data) return null;
  return (
    <span
      title={dataQuality.warnings?.join('\n')}
      className="ml-1 text-[10px] bg-yellow-800 text-yellow-300 rounded px-1 py-0.5 cursor-help"
    >⚠ Low data</span>
  );
}

function ConfidenceTag({ confidence }) {
  if (!confidence) return null;
  const agreement = confidence.model_agreement;
  if (agreement == null) return null;
  const label = agreement >= 0.8 ? 'High' : agreement >= 0.5 ? 'Med' : 'Low';
  const cls = agreement >= 0.8 ? 'text-green-400' : agreement >= 0.5 ? 'text-yellow-400' : 'text-red-400';
  return <span className={`text-[10px] font-semibold ${cls}`}>{label} conf.</span>;
}

const MARKET_HEADER = {
  outcome:        'Prediction',
  over_under:     'Over / Under',
  asian_handicap: 'Asian Handicap',
  correct_score:  'Correct Score',
  double_chance:  'Double Chance',
  btts:           'BTTS',
  odds_edge:      'Odds / Edge',
};

function PredictionsTable({ competition }) {
  const [predictions, setPredictions]       = useState([]);
  const [nextFixtureDate, setNextFixtureDate] = useState(null);
  const [loading, setLoading]               = useState(true);
  const [error, setError]                   = useState(null);
  const [market, setMarket]                 = useState('outcome');
  const [subOption, setSubOption]           = useState(null);
  const [expandedTeam, setExpandedTeam]     = useState(null);
  const [expandedShap, setExpandedShap]     = useState(null);
  function makeLeg(p, label, market, odds, prob) {
    return {
      id: `${p.home_team}|${p.away_team}|${p.match_date}|${label}`,
      homeTeam: p.home_team,
      awayTeam: p.away_team,
      competition,
      matchDate: p.match_date?.slice(0, 10),
      label,
      market,
      odds: odds || 2.0,
      prob: prob || 0.33,
    };
  }

  const toggleTeam = team =>
    setExpandedTeam(prev => (prev === team ? null : team));

  const toggleShap = idx =>
    setExpandedShap(prev => (prev === idx ? null : idx));

  useEffect(() => {
    setLoading(true);
    setError(null);
    setNextFixtureDate(null);
    fetch(`${process.env.REACT_APP_API_URL}/api/v1/predictions/${competition}`)
      .then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then(data => {
        setPredictions(data.predictions || []);
        if (data.next_fixture_date) setNextFixtureDate(data.next_fixture_date);
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [competition]);

  const formatDate = dateStr => new Date(dateStr).toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });

  if (loading) return <div className="text-gray-400 py-8 text-center">Loading predictions…</div>;
  if (error)   return <div className="text-red-400 py-8 text-center">Error: {error}</div>;
  if (!predictions.length) {
    const dateStr = nextFixtureDate
      ? new Date(nextFixtureDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
      : null;
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 py-14 text-center space-y-2">
        <div className="text-3xl">📅</div>
        <p className="text-gray-400 font-medium">No upcoming fixtures right now</p>
        {dateStr
          ? <p className="text-gray-500 text-sm">Next scheduled match: <span className="text-green-400 font-semibold">{dateStr}</span></p>
          : <p className="text-gray-600 text-sm">Check back when the new season starts</p>
        }
        <p className="text-gray-700 text-xs">WC 2026 predictions are available in the <span className="text-gray-500">WC 2026 / Euros</span> tab</p>
      </div>
    );
  }

  return (
    <div>
      <SpreadSelector market={market} setMarket={setMarket} subOption={subOption} setSubOption={setSubOption} />

      {/* Mobile card view */}
      <div className="md:hidden space-y-3">
        {predictions.map((p, i) => {
          const o = p.outcome || {};
          const odds = p.odds || {};
          const outcomeLegs = [
            { label: 'H', prob: o.home_win_prob, odds: odds.home },
            { label: 'D', prob: o.draw_prob,     odds: odds.draw },
            { label: 'A', prob: o.away_win_prob, odds: odds.away },
          ];
          return (
            <div key={i} className="bg-gray-800 rounded-lg border border-gray-700 p-3">
              <div className="flex justify-between items-start mb-1">
                <div>
                  <div className="font-semibold text-white text-sm flex items-center gap-1">
                    {p.home_team}
                    <DataQualityBadge dataQuality={p.data_quality} />
                  </div>
                  <div className="text-gray-400 text-xs">vs {p.away_team}</div>
                </div>
                <div className="text-right">
                  <div className="text-gray-500 text-xs">{formatDate(p.match_date)}</div>
                  <ConfidenceTag confidence={p.confidence} />
                </div>
              </div>
              <SignalBadges signals={p.signals} />
              <div className="mt-2">{renderMarketCell(p, market, subOption)}</div>
              <XgRangeBar xgRange={p.xg_range} />
              {p.odds && <OddsMovement odds={p.odds} openingOdds={p.opening_odds} />}
              {/* Quick add-to-slip buttons */}
              <div className="flex gap-1 mt-2 flex-wrap">
                {outcomeLegs.map(ol => (
                  <AddToSlipBtn
                    key={ol.label}
                    leg={makeLeg(p, ol.label, '1x2', ol.odds, ol.prob)}
                    small
                  />
                ))}
                {p.markets?.btts?.yes != null && (
                  <AddToSlipBtn leg={makeLeg(p, 'BTTS Yes', 'btts', null, p.markets.btts.yes)} small />
                )}
                {p.markets?.over_under?.['2.5']?.over != null && (
                  <AddToSlipBtn leg={makeLeg(p, 'Over 2.5', 'over_under', null, p.markets.over_under['2.5'].over)} small />
                )}
              </div>
              <div className="flex gap-2 mt-2">
                <button onClick={() => toggleTeam(p.home_team)}
                  className="text-xs text-gray-400 hover:text-green-400">📊 {p.home_team} form</button>
                <button onClick={() => toggleShap(i)}
                  className="text-xs text-gray-400 hover:text-purple-400">🧠 Why?</button>
              </div>
              {expandedShap === i && (
                <div className="mt-3 pt-3 border-t border-gray-700">
                  <ShapPanel features={p.shap_features} predictedOutcome={p.outcome?.predicted} />
                </div>
              )}
              {(expandedTeam === p.home_team || expandedTeam === p.away_team) && (
                <div className="mt-3 pt-3 border-t border-gray-700">
                  <TeamFormChart competition={competition} team={expandedTeam} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Desktop table view */}
      <div className="hidden md:block bg-gray-800 rounded-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-green-800 text-white text-sm">
            <tr>
              <th className="px-4 py-3 text-left">Home</th>
              <th className="px-4 py-3 text-left">Away</th>
              <th className="px-4 py-3 text-left">Date</th>
              <th className="px-4 py-3 text-left">{MARKET_HEADER[market]}</th>
              <th className="px-4 py-3 text-left">Add</th>
              <th className="px-4 py-3 text-left">Why?</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((p, i) => {
              const o = p.outcome || {};
              const odds = p.odds || {};
              return (
                <React.Fragment key={i}>
                  <tr className={`border-t border-gray-700 hover:bg-gray-700 ${i % 2 === 0 ? 'bg-gray-800' : 'bg-gray-850'}`}>
                    <td className="px-4 py-3 font-medium">
                      <div className="flex items-center gap-1.5">
                        <button onClick={() => toggleTeam(p.home_team)}
                          className="hover:text-green-400 transition-colors text-left">{p.home_team}</button>
                        <DataQualityBadge dataQuality={p.data_quality} />
                      </div>
                      <SignalBadges signals={p.signals} />
                      {p.odds && <OddsMovement odds={p.odds} openingOdds={p.opening_odds} />}
                      <XgRangeBar xgRange={p.xg_range} />
                    </td>
                    <td className="px-4 py-3 text-gray-300">
                      <div className="flex items-center gap-1">
                        <button onClick={() => toggleTeam(p.away_team)}
                          className="hover:text-green-400 transition-colors text-left">{p.away_team}</button>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-gray-400 text-sm whitespace-nowrap">
                      {formatDate(p.match_date)}
                      <div className="mt-0.5"><ConfidenceTag confidence={p.confidence} /></div>
                    </td>
                    <td className="px-4 py-3 min-w-[180px]">{renderMarketCell(p, market, subOption)}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-1">
                        {[
                          { label: 'H', prob: o.home_win_prob, odds: odds.home, market: '1x2' },
                          { label: 'D', prob: o.draw_prob,     odds: odds.draw, market: '1x2' },
                          { label: 'A', prob: o.away_win_prob, odds: odds.away, market: '1x2' },
                        ].map(ol => (
                          <div key={ol.label} className="flex items-center gap-1">
                            <span className="text-xs text-gray-500 w-3">{ol.label}</span>
                            <AddToSlipBtn leg={makeLeg(p, ol.label, ol.market, ol.odds, ol.prob)} small />
                          </div>
                        ))}
                        {p.markets?.over_under?.['2.5']?.over != null && (
                          <AddToSlipBtn leg={makeLeg(p, 'O2.5', 'over_under', null, p.markets.over_under['2.5'].over)} small />
                        )}
                        {p.markets?.btts?.yes != null && (
                          <AddToSlipBtn leg={makeLeg(p, 'BTTS', 'btts', null, p.markets.btts.yes)} small />
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => toggleShap(i)}
                        className={`px-2 py-1 rounded text-xs font-semibold border transition-colors ${
                          expandedShap === i
                            ? 'bg-purple-600 border-purple-500 text-white'
                            : 'bg-gray-700 border-gray-600 text-gray-400 hover:border-purple-500 hover:text-purple-300'
                        }`}
                      >Why?</button>
                    </td>
                  </tr>
                  {expandedShap === i && (
                    <tr className="bg-gray-900 border-t border-purple-900">
                      <td colSpan={6} className="px-6 py-4">
                        <ShapPanel
                          features={p.shap_features}
                          predictedOutcome={p.outcome?.predicted}
                        />
                      </td>
                    </tr>
                  )}
                  {(expandedTeam === p.home_team || expandedTeam === p.away_team) && (
                    <tr className="bg-gray-900 border-t border-gray-700">
                      <td colSpan={6} className="px-6 py-4">
                        <div className="text-xs text-green-400 font-semibold mb-2">
                          {expandedTeam} — Last 10 matches
                        </div>
                        <TeamFormChart competition={competition} team={expandedTeam} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default PredictionsTable;

