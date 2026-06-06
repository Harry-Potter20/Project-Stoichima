import React, { useState, useEffect, useCallback } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { flagFor } from './countryFlags';

const API = process.env.REACT_APP_API_URL;
const POLL_INTERVAL_MS = 30_000;

function StatusPill({ status, minute }) {
  const map = {
    IN_PLAY:    { dot: '🟢', label: `${minute}'` },
    IN_PLAY_2H: { dot: '🟢', label: `${minute}'` },
    HT:         { dot: '⏸', label: 'HT' },
    AET:        { dot: '⏰', label: `${minute}' AET` },
    PEN:        { dot: '🎯', label: 'Pens' },
    SUSPENDED:  { dot: '⚠️', label: 'Susp.' },
    FT:         { dot: '🏁', label: 'FT' },
  };
  const s = map[status] || { dot: '⚽', label: status };
  return (
    <span className="text-xs font-semibold text-green-400">
      {s.dot} {s.label}
    </span>
  );
}

function ProbBar({ home, draw, away }) {
  const fmt = v => `${(v * 100).toFixed(0)}%`;
  return (
    <div className="w-full">
      <div className="flex h-2 rounded overflow-hidden">
        <div style={{ width: `${home * 100}%` }} className="bg-green-500" />
        <div style={{ width: `${draw * 100}%` }} className="bg-yellow-500" />
        <div style={{ width: `${away * 100}%` }} className="bg-red-500" />
      </div>
      <div className="flex justify-between text-[10px] text-gray-400 mt-0.5">
        <span className="text-green-400">{fmt(home)}</span>
        <span className="text-yellow-400">{fmt(draw)}</span>
        <span className="text-red-400">{fmt(away)}</span>
      </div>
    </div>
  );
}

function LiveMatchCard({ match, onClick }) {
  const pred = match.live_prediction;
  return (
    <button
      onClick={() => onClick(match.match_id)}
      className="bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg p-3 text-left w-full transition-colors"
    >
      <div className="flex justify-between items-center mb-2">
        <StatusPill status={match.status} minute={match.minute} />
        <span className="text-xs text-gray-500">{match.competition}</span>
      </div>
      <div className="text-sm font-semibold text-white flex items-center justify-between gap-2">
        <span>{flagFor(match.home_team)} {match.home_team}</span>
        <span className="text-2xl font-bold tabular-nums text-green-400">
          {match.score.home}–{match.score.away}
        </span>
        <span className="text-right">{match.away_team} {flagFor(match.away_team)}</span>
      </div>
      {(match.red_cards?.home || match.red_cards?.away) ? (
        <div className="text-[10px] text-red-400 mt-1">
          🟥 {match.red_cards.home || 0} – {match.red_cards.away || 0}
        </div>
      ) : null}
      {pred && (
        <div className="mt-2">
          <ProbBar home={pred.home_win_prob} draw={pred.draw_prob} away={pred.away_win_prob} />
          <div className="flex gap-3 text-[10px] text-gray-500 mt-1">
            <span>O2.5: <span className="text-gray-300">{(pred.over_2_5_prob * 100).toFixed(0)}%</span></span>
            <span>BTTS: <span className="text-gray-300">{(pred.btts_prob * 100).toFixed(0)}%</span></span>
            <span>xG total: <span className="text-gray-300">{pred.expected_total_goals?.toFixed(1)}</span></span>
          </div>
        </div>
      )}
    </button>
  );
}

function ProbabilityTrajectory({ history }) {
  if (!history?.length) return null;
  const data = history.map(h => ({
    minute: h.minute,
    Home: Math.round(h.home_win_prob * 100),
    Draw: Math.round(h.draw_prob * 100),
    Away: Math.round(h.away_win_prob * 100),
  }));
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <h4 className="text-sm font-semibold text-gray-300 mb-2">Win Probability Over Time</h4>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="minute" tick={{ fill: '#9ca3af', fontSize: 10 }} unit="'" />
          <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} unit="%" domain={[0, 100]} />
          <Tooltip contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Line type="monotone" dataKey="Home" stroke="#22c55e" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="Draw" stroke="#eab308" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="Away" stroke="#ef4444" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function EventTimeline({ events }) {
  if (!events?.length) return null;
  const emoji = {
    goal: '⚽', own_goal: '⚽(OG)', penalty_goal: '⚽(P)',
    yellow: '🟨', red: '🟥', sub: '🔄', var_check: '📺',
  };
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <h4 className="text-sm font-semibold text-gray-300 mb-2">Event Timeline</h4>
      <div className="space-y-1 max-h-72 overflow-y-auto">
        {events.map((e, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <span className="text-gray-500 w-8 font-mono">{e.minute}'</span>
            <span className="w-6 text-center">{emoji[e.type] || '·'}</span>
            <span className="text-gray-300 flex-1">
              {e.team && <span className="text-gray-500 mr-1">{flagFor(e.team)}</span>}
              {e.player || e.type}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function LiveMatchDetail({ matchId, onClose }) {
  const [data, setData] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    Promise.all([
      fetch(`${API}/api/v1/live/${matchId}`).then(r => r.json()),
      fetch(`${API}/api/v1/live/${matchId}/events`).then(r => r.json()),
    ]).then(([m, e]) => { setData(m); setEvents(e.events || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, [matchId]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [load]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700 sticky top-0 bg-gray-900">
          <h3 className="font-bold text-white">Live Match Detail</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-lg">✕</button>
        </div>
        {loading ? (
          <div className="py-12 text-center text-gray-400">Loading…</div>
        ) : !data ? (
          <div className="py-12 text-center text-gray-500">No live data</div>
        ) : (
          <div className="p-5 space-y-4">
            <div className="bg-gray-800 rounded-lg p-4 flex items-center justify-between">
              <div>
                <div className="text-gray-500 text-xs">{data.competition}</div>
                <div className="text-lg font-bold text-white">
                  {flagFor(data.home_team)} {data.home_team}
                </div>
              </div>
              <div className="text-center">
                <div className="text-4xl font-bold text-green-400 tabular-nums">
                  {data.score.home}–{data.score.away}
                </div>
                <StatusPill status={data.status} minute={data.minute} />
              </div>
              <div className="text-right">
                <div className="text-gray-500 text-xs">&nbsp;</div>
                <div className="text-lg font-bold text-white">
                  {data.away_team} {flagFor(data.away_team)}
                </div>
              </div>
            </div>
            {data.live_prediction && (
              <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <h4 className="text-sm font-semibold text-gray-300 mb-2">Current Win Probability</h4>
                <ProbBar
                  home={data.live_prediction.home_win_prob}
                  draw={data.live_prediction.draw_prob}
                  away={data.live_prediction.away_win_prob}
                />
                <div className="flex gap-4 text-xs text-gray-400 mt-3">
                  <span>O2.5: <span className="text-white font-semibold">{(data.live_prediction.over_2_5_prob*100).toFixed(0)}%</span></span>
                  <span>BTTS: <span className="text-white font-semibold">{(data.live_prediction.btts_prob*100).toFixed(0)}%</span></span>
                  <span>xG remaining: <span className="text-white font-semibold">{data.live_prediction.expected_total_goals?.toFixed(2)}</span></span>
                </div>
              </div>
            )}
            <ProbabilityTrajectory history={data.history} />
            <EventTimeline events={events} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function LiveView() {
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailId, setDetailId] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const load = useCallback(() => {
    fetch(`${API}/api/v1/live`)
      .then(r => r.json())
      .then(d => { setMatches(d.matches || []); setLoading(false); setLastUpdate(new Date()); })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">📺 Live Matches</h2>
          {lastUpdate && (
            <p className="text-xs text-gray-500">
              Auto-refresh every 30s · last: {lastUpdate.toLocaleTimeString()}
            </p>
          )}
        </div>
        <button onClick={load} className="text-xs px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded">
          Refresh now
        </button>
      </div>

      {loading && matches.length === 0 ? (
        <div className="text-center py-12 text-gray-400">Loading live matches…</div>
      ) : matches.length === 0 ? (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-12 text-center">
          <div className="text-5xl mb-3">⚽</div>
          <div className="text-gray-300 font-semibold mb-1">No matches in play right now</div>
          <div className="text-gray-500 text-sm">
            Live tracking activates during World Cup matchday hours.
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {matches.map(m => (
            <LiveMatchCard key={m.match_id} match={m} onClick={setDetailId} />
          ))}
        </div>
      )}

      {detailId && <LiveMatchDetail matchId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}
