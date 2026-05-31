import React, { useState, useEffect } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';

const LEAGUES = [
  { id: 'PL',  label: 'Premier League' },
  { id: 'PD',  label: 'La Liga' },
  { id: 'BL1', label: 'Bundesliga' },
  { id: 'SA',  label: 'Serie A' },
  { id: 'FL1', label: 'Ligue 1' },
  { id: 'WC',  label: 'World Cup' },
  { id: 'EC',  label: 'Euros' },
];

const API = process.env.REACT_APP_API_URL;
const pct = v => v != null ? `${(v * 100).toFixed(1)}%` : '—';
const pctNum = v => v != null ? parseFloat((v * 100).toFixed(1)) : null;

function StatPill({ label, value, color }) {
  const colors = {
    green:  'text-green-400',
    blue:   'text-blue-400',
    yellow: 'text-yellow-400',
  };
  return (
    <div className="bg-gray-700 rounded-lg px-4 py-3 flex flex-col items-center">
      <span className="text-gray-400 text-xs mb-1">{label}</span>
      <span className={`text-2xl font-bold ${colors[color]}`}>{value}</span>
    </div>
  );
}

function PerClassTable({ perClass }) {
  if (!perClass || !Object.keys(perClass).length) return null;
  const rows = [
    { key: 'H', label: 'Home Win', color: 'text-green-400' },
    { key: 'D', label: 'Draw',     color: 'text-yellow-400' },
    { key: 'A', label: 'Away Win', color: 'text-red-400' },
  ];
  return (
    <table className="w-full text-sm mt-4">
      <thead>
        <tr className="text-gray-400 text-xs border-b border-gray-600">
          <th className="text-left py-2">Outcome</th>
          <th className="text-right py-2">Total</th>
          <th className="text-right py-2">Correct</th>
          <th className="text-right py-2">Accuracy</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ key, label, color }) => {
          const d = perClass[key];
          if (!d) return null;
          return (
            <tr key={key} className="border-b border-gray-700">
              <td className={`py-2 font-medium ${color}`}>{label}</td>
              <td className="py-2 text-right text-gray-300">{d.total}</td>
              <td className="py-2 text-right text-gray-300">{d.correct}</td>
              <td className={`py-2 text-right font-semibold ${color}`}>{pct(d.accuracy)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function AccuracyTracker() {
  const [data, setData]       = useState({});
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState('PL');

  useEffect(() => {
    const fetches = LEAGUES.map(({ id }) =>
      fetch(`${API}/api/v1/accuracy/${id}`)
        .then(r => r.json())
        .then(d => [id, d])
        .catch(() => [id, null])
    );
    Promise.all(fetches).then(results => {
      setData(Object.fromEntries(results));
      setLoading(false);
    });
  }, []);

  if (loading) return <div className="text-gray-400 py-16 text-center">Loading accuracy data…</div>;

  // Build chart data from all leagues
  const chartData = LEAGUES.map(({ id, label }) => {
    const d = data[id];
    if (!d || !d.resolved) return { league: label.replace(' ', '\n'), resolved: 0 };
    return {
      league:   label,
      Outcome:  pctNum(d.accuracy?.outcome),
      'O/U 2.5': pctNum(d.accuracy?.over_2_5),
      BTTS:     pctNum(d.accuracy?.btts),
      resolved: d.resolved,
    };
  }).filter(d => d.resolved > 0);

  const selected_data = data[selected];
  const hasData = selected_data && selected_data.resolved > 0;

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">Prediction Accuracy</h2>
        <p className="text-gray-400 text-sm">Accuracy is computed from resolved predictions (finished matches).</p>
      </div>

      {chartData.length > 0 ? (
        <div className="bg-gray-800 rounded-lg p-6">
          <h3 className="text-gray-300 font-semibold mb-4">Accuracy by League (%)</h3>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="league" tick={{ fill: '#9ca3af', fontSize: 12 }} />
              <YAxis domain={[0, 100]} tick={{ fill: '#9ca3af', fontSize: 12 }} unit="%" />
              <Tooltip
                contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
                labelStyle={{ color: '#f9fafb' }}
                formatter={(v) => [`${v}%`]}
              />
              <Legend wrapperStyle={{ color: '#9ca3af', fontSize: 13 }} />
              <Bar dataKey="Outcome"   fill="#22c55e" radius={[4, 4, 0, 0]} />
              <Bar dataKey="O/U 2.5"   fill="#3b82f6" radius={[4, 4, 0, 0]} />
              <Bar dataKey="BTTS"      fill="#eab308" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="bg-gray-800 rounded-lg p-8 text-center text-gray-500">
          No resolved predictions yet. Accuracy data will appear once predictions are generated and matches finish.
        </div>
      )}

      {/* Per-league detail */}
      <div>
        <h3 className="text-gray-300 font-semibold mb-3">League Detail</h3>
        <div className="flex gap-2 mb-4 flex-wrap">
          {LEAGUES.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setSelected(id)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                selected === id
                  ? 'bg-green-500 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {!hasData ? (
          <div className="bg-gray-800 rounded-lg p-6 text-gray-500 text-sm">
            No resolved predictions for {LEAGUES.find(l => l.id === selected)?.label}.
          </div>
        ) : (
          <div className="bg-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-2 mb-4">
              <span className="text-gray-400 text-sm">{selected_data.resolved} resolved predictions</span>
            </div>
            <div className="grid grid-cols-3 gap-3 mb-2">
              <StatPill label="Outcome"   value={pct(selected_data.accuracy?.outcome)}  color="green"  />
              <StatPill label="O/U 2.5"   value={pct(selected_data.accuracy?.over_2_5)} color="blue"   />
              <StatPill label="BTTS"      value={pct(selected_data.accuracy?.btts)}     color="yellow" />
            </div>
            <PerClassTable perClass={selected_data.per_class} />
          </div>
        )}
      </div>
    </div>
  );
}
