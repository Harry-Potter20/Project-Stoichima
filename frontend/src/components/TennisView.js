import React, { useState, useEffect, useRef } from 'react';
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, ResponsiveContainer, Tooltip,
} from 'recharts';

const API = process.env.REACT_APP_API_URL;

const SURFACES = ['Hard', 'Clay', 'Grass', 'Carpet'];
const SURFACE_COLOR = { Hard: '#3b82f6', Clay: '#ef4444', Grass: '#22c55e', Carpet: '#8b5cf6' };

// ── Player search autocomplete ────────────────────────────────────────────────

function PlayerSearch({ label, value, onChange }) {
  const [query, setQuery] = useState(value || '');
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const timer = useRef(null);

  const handleInput = (v) => {
    setQuery(v);
    clearTimeout(timer.current);
    if (v.length < 2) { setResults([]); return; }
    timer.current = setTimeout(() => {
      fetch(`${API}/api/v1/tennis/players?q=${encodeURIComponent(v)}`)
        .then(r => r.json())
        .then(data => { setResults(data.players || data); setOpen(true); })
        .catch(() => {});
    }, 300);
  };

  const select = (name) => {
    setQuery(name);
    onChange(name);
    setOpen(false);
    setResults([]);
  };

  return (
    <div className="relative">
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      <input
        value={query}
        onChange={e => handleInput(e.target.value)}
        onFocus={() => results.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder="Search player…"
        className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-green-500"
      />
      {open && results.length > 0 && (
        <div className="absolute z-20 mt-1 w-full bg-gray-800 border border-gray-600 rounded shadow-xl max-h-52 overflow-auto">
          {results.map((p, i) => (
            <button
              key={i}
              onMouseDown={() => select(p.name)}
              className="w-full px-3 py-2 text-left text-sm hover:bg-gray-700 flex items-center justify-between"
            >
              <span className="text-white">{p.name}</span>
              <span className="text-gray-400 text-xs">ELO {p.elo_overall}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Win probability display ───────────────────────────────────────────────────

function WinProb({ p1, p2, p1_prob, p2_prob, surface }) {
  const col = SURFACE_COLOR[surface] || '#22c55e';
  return (
    <div className="mt-6 bg-gray-700 rounded-lg p-5">
      <div className="flex justify-between items-center mb-3">
        <span className="font-bold text-white text-lg">{p1}</span>
        <span className="font-bold text-white text-lg">{p2}</span>
      </div>
      <div className="flex h-8 rounded overflow-hidden text-sm font-bold">
        <div
          style={{ width: `${p1_prob * 100}%`, backgroundColor: col }}
          className="flex items-center justify-center text-white transition-all duration-500"
        >
          {(p1_prob * 100).toFixed(1)}%
        </div>
        <div
          style={{ width: `${p2_prob * 100}%`, backgroundColor: '#374151' }}
          className="flex items-center justify-center text-gray-300 transition-all duration-500"
        >
          {(p2_prob * 100).toFixed(1)}%
        </div>
      </div>
    </div>
  );
}

// ── ELO radar chart (per-surface) ────────────────────────────────────────────

function EloRadar({ p1Name, p2Name, p1Elo, p2Elo }) {
  if (!p1Elo && !p2Elo) return null;
  const surfaces = ['Hard', 'Clay', 'Grass', 'Carpet'];
  const data = surfaces.map(s => ({
    surface:  s,
    [p1Name]: p1Elo ? Math.round(p1Elo[`elo_${s.toLowerCase()}`] || 1500) : 1500,
    [p2Name]: p2Elo ? Math.round(p2Elo[`elo_${s.toLowerCase()}`] || 1500) : 1500,
  }));
  return (
    <div className="bg-gray-800 rounded-lg p-4 mt-4">
      <h4 className="text-xs text-gray-400 mb-2">Surface ELO comparison</h4>
      <ResponsiveContainer width="100%" height={200}>
        <RadarChart data={data}>
          <PolarGrid stroke="#374151" />
          <PolarAngleAxis dataKey="surface" tick={{ fill: '#9ca3af', fontSize: 11 }} />
          <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151' }} />
          <Radar name={p1Name} dataKey={p1Name} stroke="#22c55e" fill="#22c55e" fillOpacity={0.15} />
          <Radar name={p2Name} dataKey={p2Name} stroke="#ef4444" fill="#ef4444" fillOpacity={0.15} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Derived markets panel ─────────────────────────────────────────────────────

function ProbPill({ label, value, highlight }) {
  return (
    <div className={`flex justify-between items-center px-3 py-1.5 rounded text-sm ${highlight ? 'bg-green-900/40 text-green-300' : 'bg-gray-700 text-gray-300'}`}>
      <span className="text-gray-400 text-xs">{label}</span>
      <span className="font-bold tabular-nums">{(value * 100).toFixed(1)}%</span>
    </div>
  );
}

function TennisMarkets({ markets, p1, p2 }) {
  const fs  = markets.first_set;
  const tg  = markets.total_games;
  const sh  = markets.set_handicap;

  return (
    <div className="mt-4 space-y-3">
      <div className="text-xs text-gray-500 font-semibold uppercase tracking-wide">Derived Markets</div>

      <div className="grid grid-cols-3 gap-3">
        {/* First set */}
        <div className="bg-gray-800 rounded-lg p-3">
          <div className="text-xs text-gray-400 mb-2 font-semibold">First Set Winner</div>
          <div className="space-y-1">
            <ProbPill label={p1} value={fs.p1_win} highlight={fs.p1_win >= 0.5} />
            <ProbPill label={p2} value={fs.p2_win} highlight={fs.p2_win >= 0.5} />
          </div>
        </div>

        {/* Total games O/U */}
        <div className="bg-gray-800 rounded-lg p-3">
          <div className="text-xs text-gray-400 mb-1 font-semibold">Total Games</div>
          <div className="text-xs text-gray-500 mb-2">Expected: {tg.expected}</div>
          <div className="space-y-1">
            <ProbPill label="O 21.5" value={tg.over_21_5} highlight={tg.over_21_5 >= 0.5} />
            <ProbPill label="O 23.5" value={tg.over_23_5} highlight={tg.over_23_5 >= 0.5} />
            <ProbPill label="O 25.5" value={tg.over_25_5} highlight={tg.over_25_5 >= 0.5} />
          </div>
        </div>

        {/* Set handicap */}
        <div className="bg-gray-800 rounded-lg p-3">
          <div className="text-xs text-gray-400 mb-2 font-semibold">Set Handicap (BO3)</div>
          <div className="space-y-1">
            <ProbPill label={`${p1} 2-0`} value={sh.p1_2_0} highlight={sh.p1_2_0 > sh.p2_2_0} />
            <ProbPill label={`${p1} 2-1`} value={sh.p1_2_1} />
            <ProbPill label={`${p2} 2-0`} value={sh.p2_2_0} highlight={sh.p2_2_0 > sh.p1_2_0} />
            <ProbPill label={`${p2} 2-1`} value={sh.p2_2_1} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Match predictor ───────────────────────────────────────────────────────────

function MatchPredictor() {
  const [p1, setP1]       = useState('');
  const [p2, setP2]       = useState('');
  const [surface, setSurf] = useState('Hard');
  const [isGS, setIsGS]   = useState(false);
  const [isMasters, setIsMasters] = useState(false);
  const [result, setResult]    = useState(null);
  const [p1Elo, setP1Elo]      = useState(null);
  const [p2Elo, setP2Elo]      = useState(null);
  const [loading, setLoading]  = useState(false);
  const [error, setError]      = useState(null);

  const fetchElo = (name, setter) => {
    if (!name) return;
    fetch(`${API}/api/v1/tennis/players/${encodeURIComponent(name)}/elo`)
      .then(r => r.ok ? r.json() : null)
      .then(setter)
      .catch(() => setter(null));
  };

  const predict = () => {
    if (!p1 || !p2 || p1 === p2) return;
    setLoading(true);
    setError(null);
    fetch(`${API}/api/v1/tennis/predict`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        player1: p1, player2: p2, surface,
        is_grand_slam: isGS, is_masters: isMasters,
      }),
    })
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(data => {
        setResult(data);
        fetchElo(p1, setP1Elo);
        fetchElo(p2, setP2Elo);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  return (
    <div>
      <h3 className="text-lg font-bold text-white mb-4">Match Predictor</h3>
      <div className="grid grid-cols-2 gap-4 mb-4">
        <PlayerSearch label="Player 1" value={p1} onChange={v => { setP1(v); setResult(null); }} />
        <PlayerSearch label="Player 2" value={p2} onChange={v => { setP2(v); setResult(null); }} />
      </div>

      <div className="flex gap-2 mb-4 flex-wrap">
        {SURFACES.map(s => (
          <button
            key={s}
            onClick={() => setSurf(s)}
            style={surface === s ? { backgroundColor: SURFACE_COLOR[s] } : {}}
            className={`px-4 py-2 rounded font-semibold text-sm transition-colors ${
              surface === s ? 'text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            {s}
          </button>
        ))}
        <div className="ml-2 flex gap-3 items-center">
          <label className="flex items-center gap-1.5 text-sm text-gray-300 cursor-pointer">
            <input type="checkbox" checked={isGS} onChange={e => setIsGS(e.target.checked)} className="rounded" />
            Grand Slam
          </label>
          <label className="flex items-center gap-1.5 text-sm text-gray-300 cursor-pointer">
            <input type="checkbox" checked={isMasters} onChange={e => setIsMasters(e.target.checked)} className="rounded" />
            Masters
          </label>
        </div>
      </div>

      <button
        onClick={predict}
        disabled={!p1 || !p2 || loading}
        className="w-full py-3 bg-green-600 hover:bg-green-500 disabled:bg-gray-600 text-white font-bold rounded transition-colors"
      >
        {loading ? 'Predicting…' : 'Predict Match'}
      </button>

      {error && <div className="text-red-400 mt-3 text-sm">{error}</div>}

      {result && (
        <>
          <WinProb
            p1={result.player1} p2={result.player2}
            p1_prob={result.p1_win_prob} p2_prob={result.p2_win_prob}
            surface={result.surface}
          />
          <div className="mt-3 flex justify-between text-sm text-gray-400">
            <span>
              Predicted winner:&nbsp;
              <span className="text-green-400 font-bold">{result.predicted_winner}</span>
              &nbsp;({result.confidence}% confidence)
            </span>
            <span className="text-gray-600">{result.model_used}</span>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-500">
            <div>ELO overall: {result.elo.p1_overall} vs {result.elo.p2_overall}</div>
            <div>ELO {result.surface}: {result.elo.p1_surface} vs {result.elo.p2_surface}</div>
          </div>

          {result.markets && <TennisMarkets markets={result.markets} p1={result.player1} p2={result.player2} />}

          <EloRadar p1Name={result.player1} p2Name={result.player2} p1Elo={p1Elo} p2Elo={p2Elo} />
        </>
      )}
    </div>
  );
}

// ── ELO Leaderboard ───────────────────────────────────────────────────────────

function Leaderboard() {
  const [tour, setTour]       = useState('ATP');
  const [surface, setSurface] = useState('overall');
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/v1/tennis/leaderboard?tour=${tour}&surface=${surface}`)
      .then(r => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [tour, surface]);

  return (
    <div>
      <div className="flex gap-2 mb-4 flex-wrap items-center">
        {['ATP', 'WTA'].map(t => (
          <button key={t} onClick={() => setTour(t)}
            className={`px-3 py-1.5 rounded text-sm font-semibold ${tour === t ? 'bg-green-500 text-white' : 'bg-gray-700 text-gray-300'}`}>
            {t}
          </button>
        ))}
        <span className="text-gray-600 mx-1">|</span>
        {['overall', 'hard', 'clay', 'grass'].map(s => (
          <button key={s} onClick={() => setSurface(s)}
            style={surface === s ? { backgroundColor: SURFACE_COLOR[s.charAt(0).toUpperCase() + s.slice(1)] || '#22c55e' } : {}}
            className={`px-3 py-1.5 rounded text-sm font-semibold capitalize ${surface === s ? 'text-white' : 'bg-gray-700 text-gray-300'}`}>
            {s}
          </button>
        ))}
      </div>

      {loading && <div className="text-gray-400 py-8 text-center">Loading leaderboard…</div>}
      {!loading && data && (
        <div className="bg-gray-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-700 text-gray-300">
              <tr>
                <th className="px-3 py-2 text-right w-12">#</th>
                <th className="px-3 py-2 text-left">Player</th>
                <th className="px-3 py-2 text-right">Overall</th>
                <th className="px-3 py-2 text-right">Hard</th>
                <th className="px-3 py-2 text-right">Clay</th>
                <th className="px-3 py-2 text-right">Grass</th>
                <th className="px-3 py-2 text-right">Matches</th>
              </tr>
            </thead>
            <tbody>
              {(data.players || []).map((p, i) => (
                <tr key={i} className={`border-t border-gray-700 ${i % 2 === 0 ? 'bg-gray-800' : ''}`}>
                  <td className="px-3 py-2 text-right text-gray-500">{p.rank}</td>
                  <td className="px-3 py-2 font-medium text-white">{p.name}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-green-400 font-semibold">{p.elo_overall.toFixed(0)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-blue-400">{p.elo_hard.toFixed(0)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-red-400">{p.elo_clay.toFixed(0)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-lime-400">{p.elo_grass.toFixed(0)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-500">{p.matches_played}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data.players?.length && (
            <div className="text-gray-500 py-8 text-center text-sm">
              No data yet — run <code className="text-gray-400">python3 data_collection/tennis_data_collector.py</code> then <code className="text-gray-400">python3 train_tennis.py</code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main TennisView ────────────────────────────────────────────────────────────

const INNER_TABS = [
  { id: 'predictor',    label: 'Match Predictor' },
  { id: 'leaderboard',  label: 'ELO Leaderboard' },
];

export default function TennisView() {
  const [innerTab, setInnerTab] = useState('predictor');

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-white">🎾 Tennis</h2>
        <div className="flex gap-1">
          {INNER_TABS.map(t => (
            <button key={t.id} onClick={() => setInnerTab(t.id)}
              className={`px-4 py-2 rounded font-semibold text-sm transition-colors ${
                innerTab === t.id ? 'bg-green-500 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {innerTab === 'predictor'   && <MatchPredictor />}
      {innerTab === 'leaderboard' && <Leaderboard />}
    </div>
  );
}
