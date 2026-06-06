import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import SpreadSelector from './SpreadSelector';
import AddToSlipBtn from './AddToSlipBtn';
import KnockoutBracket from './KnockoutBracket';
import { flagFor } from './countryFlags';
import { CardSkeleton } from './LoadingSkeleton';

const API = process.env.REACT_APP_API_URL;

const COMPETITIONS = [
  { id: 'WC', label: 'FIFA World Cup 2026' },
  { id: 'EC', label: 'UEFA Euro 2024' },
];

const STAGE_ORDER = ['group', 'r32', 'r16', 'qf', 'sf', 'final', 'winner'];
const STAGE_LABELS = { group: 'QGS', r32: 'R32', r16: 'R16', qf: 'QF', sf: 'SF', final: 'Final', winner: 'Win' };

// ─── Shared sub-components ────────────────────────────────────────────────────

function XgRangeBar({ xgRange }) {
  if (!xgRange?.home_xg) return null;
  return (
    <div className="flex gap-2 text-xs text-gray-500 mt-0.5">
      <span>xG <span className="text-green-400">{xgRange.home_xg}</span>–<span className="text-red-400">{xgRange.away_xg}</span></span>
      <span className="text-gray-700">· {xgRange.total_xg}</span>
    </div>
  );
}

function ConfidenceTag({ confidence }) {
  if (!confidence) return null;
  const ag = confidence.model_agreement;
  if (ag == null) return null;
  const label = ag >= 0.8 ? 'High' : ag >= 0.5 ? 'Med' : 'Low';
  const cls   = ag >= 0.8 ? 'text-green-400' : ag >= 0.5 ? 'text-yellow-400' : 'text-red-400';
  return <span className={`text-[10px] font-semibold ${cls}`}>{label} conf.</span>;
}

function ComponentBreakdown({ components }) {
  if (!components) return null;
  const labels = { outcome_model: 'XGB', bilstm: 'BiLSTM', player_form: 'Players' };
  return (
    <div className="text-[10px] text-gray-500 mt-0.5 leading-tight">
      <span className="text-gray-600">Model agreement:</span>{' '}
      {Object.entries(components).map(([k, v]) => {
        const top = Math.max(v.H || 0, v.D || 0, v.A || 0);
        return (
          <span key={k} className="mr-1.5">
            <span className="text-gray-500">{labels[k] || k}</span>
            <span className="text-gray-400"> {Math.round(top * 100)}%</span>
          </span>
        );
      })}
    </div>
  );
}

function ProbBar({ home, draw, away }) {
  const fmt = v => `${(v * 100).toFixed(0)}%`;
  return (
    <div className="w-full min-w-[140px]">
      <div className="flex h-3 rounded overflow-hidden">
        <div style={{ width: `${home * 100}%` }} className="bg-green-500" />
        <div style={{ width: `${draw * 100}%` }} className="bg-yellow-500" />
        <div style={{ width: `${away * 100}%` }} className="bg-red-500" />
      </div>
      <div className="flex justify-between text-xs text-gray-400 mt-0.5">
        <span className="text-green-400">{fmt(home)}</span>
        <span className="text-yellow-400">{fmt(draw)}</span>
        <span className="text-red-400">{fmt(away)}</span>
      </div>
    </div>
  );
}

// ─── H2H Panel ───────────────────────────────────────────────────────────────

function H2HPanel({ homeTeam, awayTeam, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/api/v1/h2h/${encodeURIComponent(homeTeam)}/${encodeURIComponent(awayTeam)}`)
      .then(r => r.json()).then(setData).catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [homeTeam, awayTeam]);

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/60" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h3 className="font-bold text-white">⚔️ Head-to-Head</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-lg">✕</button>
        </div>
        {loading ? (
          <div className="py-10 text-center text-gray-400">Loading H2H data…</div>
        ) : !data || !data.meetings ? (
          <div className="py-10 text-center text-gray-500">No historical meetings found.</div>
        ) : (
          <div className="p-5 space-y-4">
            <div className="flex justify-between items-center">
              <span className="font-semibold text-white">{flagFor(homeTeam)} {homeTeam}</span>
              <span className="text-gray-500 text-sm">{data.meetings} meetings</span>
              <span className="font-semibold text-white">{awayTeam} {flagFor(awayTeam)}</span>
            </div>
            <div className="grid grid-cols-3 gap-3 text-center text-sm">
              <div className="bg-green-900/40 rounded p-2">
                <div className="text-green-400 font-bold text-xl">{data.summary.home_wins}</div>
                <div className="text-gray-400 text-xs">{homeTeam.split(' ').slice(-1)[0]} wins</div>
              </div>
              <div className="bg-gray-700/50 rounded p-2">
                <div className="text-yellow-400 font-bold text-xl">{data.summary.draws}</div>
                <div className="text-gray-400 text-xs">Draws</div>
              </div>
              <div className="bg-red-900/40 rounded p-2">
                <div className="text-red-400 font-bold text-xl">{data.summary.away_wins}</div>
                <div className="text-gray-400 text-xs">{awayTeam.split(' ').slice(-1)[0]} wins</div>
              </div>
            </div>
            <div className="text-center text-gray-400 text-sm">
              Avg goals per match: <span className="text-white font-semibold">{data.summary.avg_goals}</span>
            </div>
            <div className="space-y-1 max-h-52 overflow-y-auto">
              {data.records.map((r, i) => (
                <div key={i} className="flex items-center justify-between text-xs bg-gray-800 rounded px-3 py-1.5">
                  <span className="text-gray-500 w-20">{r.match_date}</span>
                  <span className="text-gray-400 text-xs">{r.competition}</span>
                  <span className="font-semibold text-white">{r.home_team} <span className="text-gray-500">vs</span> {r.away_team}</span>
                  <span className={`font-bold w-8 text-center ${
                    r.perspective === 'W' ? 'text-green-400' : r.perspective === 'L' ? 'text-red-400' : 'text-yellow-400'
                  }`}>{r.score}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Market renderer ──────────────────────────────────────────────────────────

const MARKET_HEADER = {
  outcome: 'H / D / A', over_under: 'Over / Under', asian_handicap: 'Asian Handicap',
  correct_score: 'Correct Score', double_chance: 'Double Chance', btts: 'BTTS', odds_edge: 'Odds / Edge',
};

function renderTournamentMarket(p, market, subOption) {
  const m = p.markets || {};
  const o = p.outcome || p;
  if (market === 'outcome') return <ProbBar home={o.home_win_prob ?? 0} draw={o.draw_prob ?? 0} away={o.away_win_prob ?? 0} />;
  if (market === 'over_under') {
    const line = subOption || '2.5';
    const data = m.over_under?.[line];
    if (!data) return <span className="text-gray-500 text-xs">—</span>;
    return <div className="text-xs"><span className="text-green-400">O{line}: {(data.over*100).toFixed(1)}%</span><span className="text-gray-400 mx-1">/</span><span className="text-red-400">U{line}: {(data.under*100).toFixed(1)}%</span></div>;
  }
  if (market === 'asian_handicap') {
    const line = subOption || '-0.5';
    const data = m.asian_handicap?.[line];
    if (!data) return <span className="text-gray-500 text-xs">—</span>;
    return <div className="text-xs"><span className="text-green-400">Home: {(data.home*100).toFixed(1)}%</span><span className="text-gray-400 mx-1">/</span><span className="text-red-400">Away: {(data.away*100).toFixed(1)}%</span></div>;
  }
  if (market === 'correct_score') {
    return (
      <div className="text-xs space-y-0.5">
        {(m.correct_score || []).slice(0, 3).map((s, i) => (
          <div key={i} className="flex gap-1"><span className="font-mono text-white">{s.home_goals}-{s.away_goals}</span><span className="text-gray-400">{(s.probability*100).toFixed(1)}%</span></div>
        ))}
      </div>
    );
  }
  if (market === 'double_chance') {
    const dc = m.double_chance;
    if (!dc) return <span className="text-gray-500 text-xs">—</span>;
    return <div className="text-xs space-y-0.5"><div><span className="text-gray-400">1X </span><span className="text-green-400">{(dc['1X']*100).toFixed(1)}%</span></div><div><span className="text-gray-400">X2 </span><span className="text-blue-400">{(dc['X2']*100).toFixed(1)}%</span></div><div><span className="text-gray-400">12 </span><span className="text-yellow-400">{(dc['12']*100).toFixed(1)}%</span></div></div>;
  }
  if (market === 'btts') {
    const yes = m.btts?.yes ?? 0;
    const color = yes >= 0.5 ? 'bg-green-600' : 'bg-red-600';
    return <span className={`px-2 py-0.5 rounded text-xs font-semibold text-white ${color}`}>{yes >= 0.5 ? 'Yes' : 'No'} {(yes*100).toFixed(1)}%</span>;
  }
  if (market === 'odds_edge') {
    const odds = p.odds; const edge = p.edge;
    if (!odds) return <span className="text-gray-600 text-xs">No odds</span>;
    const bestKey = edge ? Object.entries(edge).reduce((a,b) => b[1]>a[1]?b:a)[0] : null;
    return (
      <div className="space-y-1">
        <div className="flex gap-2 font-mono text-xs"><span className="text-green-400">{odds.home?.toFixed(2)}</span><span className="text-yellow-400">{odds.draw?.toFixed(2)}</span><span className="text-red-400">{odds.away?.toFixed(2)}</span></div>
        {edge && bestKey && edge[bestKey] > 0 && <div className="text-xs font-semibold text-green-400">{bestKey.toUpperCase()} +{edge[bestKey]}%</div>}
      </div>
    );
  }
  return null;
}

// ─── STATUS_DOT ──────────────────────────────────────────────────────────────

const STATUS_DOT = {
  qualified:     { dot: '●', cls: 'text-green-400',  row: 'bg-green-900/20' },
  in_contention: { dot: '◐', cls: 'text-yellow-400', row: 'bg-yellow-900/10' },
  eliminated:    { dot: '○', cls: 'text-red-500',    row: '' },
};

// ─── Group Standings (with What-If + Group Winner Probs) ─────────────────────

function applyOverrides(teams, upcoming, overrides) {
  const stats = {};
  for (const t of teams) {
    stats[t.team] = { ...t, played: t.played, won: t.won, drawn: t.drawn, lost: t.lost, gf: t.gf, ga: t.ga, gd: t.gd, pts: t.pts };
  }
  for (const m of upcoming) {
    const key = `${m.home_team}|${m.away_team}`;
    const forced = overrides[key];
    if (!forced) continue;
    const h = stats[m.home_team]; const a = stats[m.away_team];
    if (!h || !a) continue;
    h.played++; a.played++;
    if (forced === 'H') { h.won++; h.pts += 3; a.lost++; }
    else if (forced === 'D') { h.drawn++; h.pts++; a.drawn++; a.pts++; }
    else { a.won++; a.pts += 3; h.lost++; }
  }
  return Object.values(stats).sort((a, b) => b.pts - a.pts || b.gd - a.gd || b.gf - a.gf);
}

function GroupStandings({ competitionId, simProbs }) {
  const [data, setData]           = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [showUpcoming, setShowUpcoming] = useState(false);
  const [overrides, setOverrides] = useState({});
  const [whatIfMode, setWhatIfMode] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/v1/tournament/${competitionId}/groups`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [competitionId]);

  const toggle = (match, result) => {
    const key = `${match.home_team}|${match.away_team}`;
    setOverrides(prev => ({ ...prev, [key]: prev[key] === result ? undefined : result }));
  };

  if (loading) return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="bg-gray-800 rounded-lg p-3 border border-gray-700 animate-pulse">
          <div className="h-4 bg-gray-700 rounded w-20 mb-3" />
          {Array.from({ length: 4 }).map((_, j) => (
            <div key={j} className="h-3 bg-gray-700 rounded w-full mb-2" />
          ))}
        </div>
      ))}
    </div>
  );
  if (error)   return <div className="text-red-400 text-center py-8">{error}</div>;
  if (!data)   return null;

  const { groups, upcoming_matches: upcoming = [] } = data;
  const sortedGroups = Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3 text-sm">
          <span className="text-green-400">● Qualified</span>
          <span className="text-yellow-400">◐ In contention</span>
          <span className="text-red-400">○ Eliminated</span>
        </div>
        <div className="flex gap-2">
          {upcoming.length > 0 && (
            <button onClick={() => setWhatIfMode(p => !p)}
              className={`text-xs border rounded px-2 py-1 transition-colors ${whatIfMode ? 'bg-blue-700 border-blue-500 text-white' : 'border-gray-600 text-gray-400 hover:text-white'}`}>
              {whatIfMode ? '✓ What-If ON' : '🔮 What-If'}
            </button>
          )}
          {upcoming.length > 0 && (
            <button onClick={() => setShowUpcoming(p => !p)}
              className="text-xs text-gray-400 hover:text-white border border-gray-600 rounded px-2 py-1">
              {showUpcoming ? 'Hide' : 'Show'} upcoming ({upcoming.length})
            </button>
          )}
          {Object.keys(overrides).filter(k => overrides[k]).length > 0 && (
            <button onClick={() => setOverrides({})}
              className="text-xs text-red-400 hover:text-red-300 border border-red-700 rounded px-2 py-1">
              Reset overrides
            </button>
          )}
        </div>
      </div>

      {whatIfMode && upcoming.length > 0 && (
        <div className="bg-blue-900/20 border border-blue-700 rounded-lg p-3">
          <p className="text-blue-300 text-xs font-semibold mb-2">🔮 What-If: force a result to see how standings change</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {upcoming.map((m, i) => {
              const key = `${m.home_team}|${m.away_team}`;
              const forced = overrides[key];
              return (
                <div key={i} className="bg-gray-800 rounded p-2">
                  <div className="text-xs text-gray-500 mb-1">Group {m.group} · {m.match_date?.slice(0, 10)}</div>
                  <div className="text-xs text-white mb-1.5">{m.home_team} <span className="text-gray-500">v</span> {m.away_team}</div>
                  <div className="flex gap-1">
                    {['H', 'D', 'A'].map(r => (
                      <button key={r} onClick={() => toggle(m, r)}
                        className={`flex-1 py-0.5 rounded text-xs font-bold transition-colors ${
                          forced === r
                            ? r === 'H' ? 'bg-green-600 text-white' : r === 'D' ? 'bg-yellow-600 text-white' : 'bg-red-600 text-white'
                            : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                        }`}>{r}</button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {sortedGroups.map(([grp, teams]) => {
          const displayTeams = whatIfMode && Object.values(overrides).some(Boolean)
            ? applyOverrides(teams, upcoming.filter(m => m.group === grp), overrides)
            : [...teams].sort((a, b) => b.pts - a.pts || b.gd - a.gd || b.gf - a.gf);

          const matchesPlayed = displayTeams.reduce((s, t) => s + (t.played || 0), 0) / 2;
          const upcomingInGroup = upcoming.filter(m => m.group === grp).length;
          const totalMatches = matchesPlayed + upcomingInGroup;
          const progress = totalMatches > 0 ? matchesPlayed / totalMatches : 0;

          return (
            <div key={grp} className="bg-gray-800 rounded-lg overflow-hidden border border-gray-700">
              <div className="bg-gray-750 px-3 py-2 flex items-center justify-between border-b border-gray-700">
                <span className="font-bold text-sm text-green-400">Group {grp}</span>
                <span className="text-xs text-gray-500">
                  {Math.round(matchesPlayed)}/{Math.round(totalMatches)} played
                </span>
              </div>
              {totalMatches > 0 && (
                <div className="h-1 bg-gray-700">
                  <div
                    className={`h-1 transition-all duration-500 ${progress >= 1 ? 'bg-green-500' : 'bg-blue-500'}`}
                    style={{ width: `${progress * 100}%` }}
                  />
                </div>
              )}
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-500 border-b border-gray-700">
                    <th className="px-2 py-1 text-left">Team</th>
                    <th className="px-1 py-1 text-center">P</th>
                    <th className="px-1 py-1 text-center">W</th>
                    <th className="px-1 py-1 text-center">D</th>
                    <th className="px-1 py-1 text-center">L</th>
                    <th className="px-1 py-1 text-center">GD</th>
                    <th className="px-1 py-1 text-center font-bold text-white">Pts</th>
                    {simProbs && <th className="px-1 py-1 text-center text-blue-400">Win%</th>}
                  </tr>
                </thead>
                <tbody>
                  {displayTeams.map((t, rank) => {
                    const st = STATUS_DOT[t.status] || STATUS_DOT.in_contention;
                    const winProb = simProbs?.[t.team]?.winner;
                    return (
                      <tr key={t.team} className={`border-b border-gray-700/50 ${st.row}`}>
                        <td className="px-2 py-1.5 font-medium max-w-[110px] truncate">
                          <span className={`mr-1 ${st.cls}`}>{st.dot}</span>
                          <span className="mr-1">{flagFor(t.team)}</span>{t.team}
                        </td>
                        <td className="px-1 py-1.5 text-center text-gray-400">{t.played}</td>
                        <td className="px-1 py-1.5 text-center text-gray-400">{t.won}</td>
                        <td className="px-1 py-1.5 text-center text-gray-400">{t.drawn}</td>
                        <td className="px-1 py-1.5 text-center text-gray-400">{t.lost}</td>
                        <td className="px-1 py-1.5 text-center text-gray-400">{t.gd >= 0 ? `+${t.gd}` : t.gd}</td>
                        <td className="px-1 py-1.5 text-center font-bold text-white">{t.pts}</td>
                        {simProbs && (
                          <td className="px-1 py-1.5 text-center text-blue-300 text-xs">
                            {winProb != null ? `${(winProb*100).toFixed(1)}%` : '—'}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>

      {showUpcoming && upcoming.length > 0 && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">Remaining Group Fixtures</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {upcoming.map((m, i) => (
              <div key={i} className="bg-gray-900 rounded p-2 text-xs">
                <div className="text-gray-500 mb-0.5">Group {m.group} · {m.match_date?.slice(0,10)}</div>
                <div className="text-white font-medium">{m.home_team} <span className="text-gray-500">vs</span> {m.away_team}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Tournament Predictions (filtering + CS panel + H2H) ─────────────────────

function TournamentPredictions({ competitionId }) {
  const [preds, setPreds]       = useState(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [market, setMarket]     = useState('outcome');
  const [subOption, setSubOption] = useState(null);
  const [expandedCS, setExpandedCS]   = useState(null);
  const [h2hMatch, setH2hMatch]       = useState(null);
  const [groupFilter, setGroupFilter] = useState('all');
  const [teamSearch, setTeamSearch]   = useState('');
  const [sortBy, setSortBy]           = useState('date');

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/v1/tournament/${competitionId}/predictions`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(d => { setPreds(d.predictions); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [competitionId]);

  function makeLeg(p, label, mkt, odds, prob) {
    return {
      id: `${p.home_team}|${p.away_team}|${p.match_date}|${label}`,
      homeTeam: p.home_team, awayTeam: p.away_team,
      competition: competitionId, matchDate: p.match_date?.slice(0, 10),
      label, market: mkt, odds: odds || 2.0, prob: prob || 0.33,
    };
  }

  const groups = useMemo(() => {
    if (!preds) return [];
    const gs = new Set(preds.map(p => p.tournament_group).filter(Boolean));
    return ['all', ...Array.from(gs).sort()];
  }, [preds]);

  const filtered = useMemo(() => {
    if (!preds) return [];
    let rows = preds;
    if (groupFilter !== 'all') rows = rows.filter(p => p.tournament_group === groupFilter);
    if (teamSearch.trim()) {
      const q = teamSearch.toLowerCase();
      rows = rows.filter(p => p.home_team.toLowerCase().includes(q) || p.away_team.toLowerCase().includes(q));
    }
    if (sortBy === 'confidence') {
      rows = [...rows].sort((a, b) => (b.confidence?.model_agreement ?? 0) - (a.confidence?.model_agreement ?? 0));
    } else if (sortBy === 'edge') {
      const bestEdge = p => Math.max(0, ...[p.edge?.home, p.edge?.draw, p.edge?.away].filter(Boolean));
      rows = [...rows].sort((a, b) => bestEdge(b) - bestEdge(a));
    }
    return rows;
  }, [preds, groupFilter, teamSearch, sortBy]);

  const formatDate = d => new Date(d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

  if (loading) return <CardSkeleton rows={8} />;
  if (error)   return <div className="text-red-400 text-center py-8">{error}</div>;
  if (!preds?.length) return <div className="text-gray-400 text-center py-8">No upcoming matches.</div>;

  return (
    <div>
      {/* Filter bar */}
      <div className="flex flex-wrap gap-2 mb-3 items-center">
        <input
          type="text" placeholder="Search team…" value={teamSearch} onChange={e => setTeamSearch(e.target.value)}
          className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm text-white placeholder-gray-500 w-36"
        />
        {groups.length > 1 && (
          <select value={groupFilter} onChange={e => setGroupFilter(e.target.value)}
            className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm text-white">
            {groups.map(g => <option key={g} value={g}>{g === 'all' ? 'All groups' : `Group ${g}`}</option>)}
          </select>
        )}
        <select value={sortBy} onChange={e => setSortBy(e.target.value)}
          className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm text-white">
          <option value="date">Sort: Date</option>
          <option value="confidence">Sort: Confidence</option>
          <option value="edge">Sort: Edge</option>
        </select>
        <span className="text-gray-500 text-xs ml-auto">{filtered.length} matches</span>
      </div>

      <SpreadSelector market={market} setMarket={setMarket} subOption={subOption} setSubOption={setSubOption} />

      {/* Mobile cards */}
      <div className="md:hidden space-y-3">
        {filtered.map((p, i) => {
          const o = p.outcome || p;
          const cs = p.markets?.correct_score || [];
          const isCSOpen = expandedCS === i;
          return (
            <div key={i} className="bg-gray-800 rounded-lg border border-gray-700 p-3">
              <div className="flex justify-between items-start mb-1">
                <div>
                  <button onClick={() => setH2hMatch(p)} className="font-semibold text-white text-sm hover:text-green-400 text-left">
                    <span className="mr-1">{flagFor(p.home_team)}</span>{p.home_team}
                  </button>
                  <div className="text-gray-400 text-xs">vs <button onClick={() => setH2hMatch(p)} className="hover:text-green-400"><span className="mr-1">{flagFor(p.away_team)}</span>{p.away_team}</button></div>
                  <XgRangeBar xgRange={p.xg_range} />
                </div>
                <div className="text-right">
                  <div className="text-gray-500 text-xs">{formatDate(p.match_date)}</div>
                  <div className="text-gray-500 text-xs">{p.tournament_stage || '—'}</div>
                  <ConfidenceTag confidence={p.confidence} />
                </div>
              </div>
              <div className="mt-2">{renderTournamentMarket(p, market, subOption)}</div>
              <ComponentBreakdown components={p.component_probas} />
              <div className="flex gap-1.5 mt-2 flex-wrap">
                {[{ label: 'H', prob: o.home_win_prob }, { label: 'D', prob: o.draw_prob }, { label: 'A', prob: o.away_win_prob }].map(ol => (
                  <AddToSlipBtn key={ol.label} leg={makeLeg(p, ol.label, '1x2', null, ol.prob)} small />
                ))}
                {p.markets?.over_under?.['2.5']?.over != null && <AddToSlipBtn leg={makeLeg(p, 'O2.5', 'over_under', null, p.markets.over_under['2.5'].over)} small />}
                {cs.length > 0 && (
                  <button onClick={() => setExpandedCS(isCSOpen ? null : i)}
                    className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${isCSOpen ? 'bg-purple-700 text-white' : 'bg-gray-700 text-gray-400 hover:bg-purple-700 hover:text-white'}`}>CS</button>
                )}
              </div>
              {isCSOpen && cs.length > 0 && (
                <div className="mt-2 pt-2 border-t border-gray-700">
                  <div className="text-xs text-gray-400 mb-1 font-semibold">Top Correct Scores</div>
                  <div className="grid grid-cols-4 gap-1">
                    {cs.slice(0, 8).map((s, j) => (
                      <div key={j} className="bg-gray-900 rounded px-1.5 py-1 text-center">
                        <div className="font-mono text-white text-xs font-bold">{s.home_goals}-{s.away_goals}</div>
                        <div className="text-gray-400 text-[10px]">{(s.probability*100).toFixed(1)}%</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Desktop table */}
      <div className="hidden md:block bg-gray-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-700 text-gray-400 text-xs">
            <tr>
              <th className="px-4 py-3 text-left">Home</th>
              <th className="px-4 py-3 text-left">Away</th>
              <th className="px-4 py-3 text-left">Stage</th>
              <th className="px-4 py-3 text-left">Date</th>
              <th className="px-4 py-3 text-left">{MARKET_HEADER[market]}</th>
              <th className="px-3 py-3 text-left">Add</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((p, i) => {
              const o = p.outcome || p;
              const cs = p.markets?.correct_score || [];
              const isCSOpen = expandedCS === i;
              return (
                <React.Fragment key={i}>
                  <tr className={`border-t border-gray-700 hover:bg-gray-700/50 ${i%2===0 ? 'bg-gray-800' : 'bg-gray-850'}`}>
                    <td className="px-4 py-2 font-medium">
                      <button onClick={() => setH2hMatch(p)} className="hover:text-green-400 transition-colors text-left">
                        <span className="mr-1.5">{flagFor(p.home_team)}</span>{p.home_team}
                      </button>
                      <XgRangeBar xgRange={p.xg_range} />
                    </td>
                    <td className="px-4 py-2 text-gray-300">
                      <button onClick={() => setH2hMatch(p)} className="hover:text-green-400 transition-colors">
                        <span className="mr-1.5">{flagFor(p.away_team)}</span>{p.away_team}
                      </button>
                    </td>
                    <td className="px-4 py-2 text-gray-400 text-xs">{p.tournament_stage || '—'}</td>
                    <td className="px-4 py-2 text-xs">
                      <div className="text-gray-400 whitespace-nowrap">{formatDate(p.match_date)}</div>
                      <ConfidenceTag confidence={p.confidence} />
                    </td>
                    <td className="px-4 py-2 min-w-[180px]">{renderTournamentMarket(p, market, subOption)}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-col gap-1">
                        {[{ label: 'H', prob: o.home_win_prob }, { label: 'D', prob: o.draw_prob }, { label: 'A', prob: o.away_win_prob }].map(ol => (
                          <div key={ol.label} className="flex items-center gap-1">
                            <span className="text-xs text-gray-500 w-3">{ol.label}</span>
                            <AddToSlipBtn leg={makeLeg(p, ol.label, '1x2', null, ol.prob)} small />
                          </div>
                        ))}
                        {p.markets?.over_under?.['2.5']?.over != null && <AddToSlipBtn leg={makeLeg(p, 'O2.5', 'over_under', null, p.markets.over_under['2.5'].over)} small />}
                        {cs.length > 0 && (
                          <button onClick={() => setExpandedCS(isCSOpen ? null : i)}
                            className={`text-[10px] px-1.5 py-0.5 rounded font-bold mt-0.5 ${isCSOpen ? 'bg-purple-700 text-white' : 'bg-gray-700 text-gray-400 hover:bg-purple-700 hover:text-white'}`}>CS</button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {isCSOpen && cs.length > 0 && (
                    <tr className="bg-gray-900 border-t border-purple-900/50">
                      <td colSpan={6} className="px-4 py-3">
                        <div className="text-xs text-gray-400 mb-2 font-semibold">Correct Score Probabilities — {p.home_team} vs {p.away_team}</div>
                        <div className="grid grid-cols-8 gap-1.5">
                          {cs.slice(0, 12).map((s, j) => (
                            <div key={j} className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-center hover:border-purple-600 transition-colors">
                              <div className="font-mono text-white text-sm font-bold">{s.home_goals}-{s.away_goals}</div>
                              <div className="text-gray-400 text-xs mt-0.5">{(s.probability*100).toFixed(1)}%</div>
                            </div>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {h2hMatch && (
        <H2HPanel
          homeTeam={h2hMatch.home_team}
          awayTeam={h2hMatch.away_team}
          onClose={() => setH2hMatch(null)}
        />
      )}
    </div>
  );
}

// ─── Simulation Results ───────────────────────────────────────────────────────

function SimulationResults({ competitionId, onSimComplete }) {
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);
  const [n, setN]             = useState(5000);

  const run = useCallback(() => {
    setLoading(true); setError(null);
    fetch(`${API}/api/v1/tournament/${competitionId}/simulate?n=${n}`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(d => {
        setResults(d); setLoading(false);
        // Lift probabilities to parent for group winner column
        if (onSimComplete) {
          const map = {};
          for (const t of d.probabilities) map[t.team] = t;
          onSimComplete(map);
        }
      })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [competitionId, n, onSimComplete]);

  const chartData = results
    ? results.probabilities.slice(0, 20).map(t => ({
        team: t.team.length > 14 ? t.team.slice(0, 13) + '…' : t.team,
        fullName: t.team,
        Win:   parseFloat((t.winner * 100).toFixed(1)),
        Final: parseFloat((t.final  * 100).toFixed(1)),
        SF:    parseFloat((t.sf     * 100).toFixed(1)),
      }))
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-gray-400 text-sm">Simulations:</span>
          {[1000, 5000, 10000].map(v => (
            <button key={v} onClick={() => setN(v)}
              className={`px-3 py-1 rounded text-sm ${n===v ? 'bg-green-500 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}>{v.toLocaleString()}</button>
          ))}
        </div>
        <button onClick={run} disabled={loading}
          className="px-5 py-2 bg-green-600 hover:bg-green-500 disabled:bg-gray-600 text-white rounded font-semibold text-sm transition-colors">
          {loading ? 'Simulating…' : 'Run Simulation'}
        </button>
        {results && <span className="text-gray-500 text-xs">Results shown in Groups tab too</span>}
      </div>

      {error && <div className="text-red-400 text-sm">{error}</div>}

      {results && (
        <>
          <div className="bg-gray-800 rounded-lg p-5">
            <h3 className="text-gray-300 font-semibold mb-1">Win Probability — Top 20</h3>
            <p className="text-gray-500 text-xs mb-4">Based on {results.simulations.toLocaleString()} Monte Carlo simulations</p>
            <ResponsiveContainer width="100%" height={380}>
              <BarChart data={chartData} layout="vertical" margin={{ top: 0, right: 40, left: 100, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" horizontal={false} />
                <XAxis type="number" tick={{ fill: '#9ca3af', fontSize: 11 }} unit="%" domain={[0, 'auto']} />
                <YAxis type="category" dataKey="team" tick={{ fill: '#d1d5db', fontSize: 12 }} width={90} />
                <Tooltip contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }} labelStyle={{ color: '#f9fafb' }}
                  formatter={(v, name) => [`${v}%`, name]} labelFormatter={(_, payload) => payload?.[0]?.payload?.fullName || ''} />
                <Bar dataKey="Win"   fill="#22c55e" radius={[0,4,4,0]} />
                <Bar dataKey="Final" fill="#3b82f6" radius={[0,4,4,0]} />
                <Bar dataKey="SF"    fill="#6366f1" radius={[0,4,4,0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="bg-gray-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-700 text-gray-400 text-xs">
                <tr>
                  <th className="px-4 py-3 text-left">#</th>
                  <th className="px-4 py-3 text-left">Team</th>
                  {STAGE_ORDER.map(s => <th key={s} className="px-3 py-3 text-right">{STAGE_LABELS[s]}</th>)}
                </tr>
              </thead>
              <tbody>
                {results.probabilities.map((t, i) => (
                  <tr key={t.team} className={`border-t border-gray-700 hover:bg-gray-700 ${i%2===0 ? 'bg-gray-800' : ''}`}>
                    <td className="px-4 py-2 text-gray-500 text-xs">{i+1}</td>
                    <td className="px-4 py-2 font-medium">{t.team}</td>
                    {STAGE_ORDER.map(s => (
                      <td key={s} className={`px-3 py-2 text-right text-xs tabular-nums ${s==='winner' ? 'font-bold text-green-400' : s==='final' ? 'text-blue-400' : 'text-gray-300'}`}>
                        {t[s] != null ? `${(t[s]*100).toFixed(1)}%` : '—'}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!results && !loading && (
        <div className="bg-gray-800 rounded-lg p-10 text-center text-gray-500">
          Click "Run Simulation" to compute Monte Carlo stage probabilities for all teams.
        </div>
      )}
    </div>
  );
}

// ─── Kickoff countdown banner ────────────────────────────────────────────────

const KICKOFF_DATES = {
  WC: new Date('2026-06-11T19:00:00Z'),
  EC: new Date('2024-06-14T19:00:00Z'),
};

function CountdownBanner({ competition }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);

  const target = KICKOFF_DATES[competition];
  if (!target) return null;
  const diffMs = target.getTime() - now;
  if (diffMs <= 0) {
    return (
      <div className="bg-green-900/30 border border-green-700 rounded-lg px-4 py-2.5 mb-3 flex items-center gap-3">
        <span className="text-2xl">🟢</span>
        <div className="text-sm">
          <div className="font-bold text-green-300">Tournament is live</div>
          <div className="text-green-500 text-xs">Predictions are updating in real time</div>
        </div>
      </div>
    );
  }
  const days = Math.floor(diffMs / 86400000);
  const hours = Math.floor((diffMs % 86400000) / 3600000);
  const label =
    competition === 'WC' ? 'FIFA World Cup 2026' :
    competition === 'EC' ? 'UEFA Euro 2024'      : 'Tournament';
  return (
    <div className="bg-gradient-to-r from-blue-900/40 to-purple-900/40 border border-blue-700/50 rounded-lg px-4 py-2.5 mb-3 flex items-center gap-3">
      <span className="text-2xl">⏱️</span>
      <div className="text-sm">
        <div className="font-bold text-blue-200">{label} kicks off in {days}d {hours}h</div>
        <div className="text-blue-400 text-xs">{target.toUTCString()}</div>
      </div>
    </div>
  );
}

// ─── Main TournamentView ──────────────────────────────────────────────────────

const VIEW_TABS = [
  { id: 'groups',      label: 'Groups' },
  { id: 'predictions', label: 'Upcoming Matches' },
  { id: 'simulate',    label: 'Simulator' },
  { id: 'bracket',     label: '🏆 Bracket' },
];

export default function TournamentView() {
  const [competition, setCompetition] = useState('WC');
  const [view, setView]               = useState('groups');
  const [simProbs, setSimProbs]       = useState(null);

  const handleSimComplete = useCallback((probs) => setSimProbs(probs), []);

  return (
    <div className="space-y-6">
      <CountdownBanner competition={competition} />
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-2">
          {COMPETITIONS.map(c => (
            <button key={c.id} onClick={() => { setCompetition(c.id); setSimProbs(null); }}
              className={`px-4 py-2 rounded font-semibold text-sm transition-colors ${competition===c.id ? 'bg-green-500 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}>
              {c.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
          {VIEW_TABS.map(t => (
            <button key={t.id} onClick={() => setView(t.id)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${view===t.id ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {view === 'groups'      && <GroupStandings      key={competition} competitionId={competition} simProbs={simProbs} />}
      {view === 'predictions' && <TournamentPredictions key={competition} competitionId={competition} />}
      {view === 'simulate'    && <SimulationResults    key={competition} competitionId={competition} onSimComplete={handleSimComplete} />}
      {view === 'bracket'     && <KnockoutBracket      key={competition} competitionId={competition} simProbs={simProbs} />}
    </div>
  );
}
