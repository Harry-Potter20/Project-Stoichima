import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';

const COMPETITIONS = [
  { id: 'PL',  label: 'Premier League' },
  { id: 'PD',  label: 'La Liga' },
  { id: 'BL1', label: 'Bundesliga' },
  { id: 'SA',  label: 'Serie A' },
  { id: 'FL1', label: 'Ligue 1' },
  { id: 'WC',  label: 'World Cup' },
  { id: 'EC',  label: 'Euros' },
];

function StatPill({ label, value, color = 'gray' }) {
  const colors = {
    green:  'bg-green-900 border-green-600 text-green-300',
    red:    'bg-red-900 border-red-600 text-red-300',
    yellow: 'bg-yellow-900 border-yellow-600 text-yellow-300',
    gray:   'bg-gray-700 border-gray-600 text-gray-300',
  };
  return (
    <div className={`px-4 py-2 rounded border text-center ${colors[color]}`}>
      <div className="text-lg font-bold">{value}</div>
      <div className="text-xs opacity-70">{label}</div>
    </div>
  );
}

export default function CLVChart() {
  const [competition, setCompetition] = useState('PL');
  const [data, setData]               = useState(null);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`${process.env.REACT_APP_API_URL}/api/v1/clv/${competition}`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(setData)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [competition]);

  const clvColor = (v) => v > 1 ? 'green' : v < 0 ? 'red' : 'yellow';

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-white">Closing Line Value</h2>
        <div className="flex gap-1">
          {COMPETITIONS.map(c => (
            <button
              key={c.id}
              onClick={() => setCompetition(c.id)}
              className={`px-3 py-1.5 rounded text-sm font-semibold transition-colors ${
                competition === c.id
                  ? 'bg-green-500 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="text-gray-400 py-12 text-center">Loading CLV data…</div>}
      {error   && <div className="text-red-400 py-12 text-center">Error: {error}</div>}

      {!loading && !error && data && (
        data.count === 0 ? (
          <div className="text-gray-500 py-12 text-center">
            {data.note || 'No resolved predictions with bookmaker odds yet.'}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3 mb-6">
              <StatPill
                label="Avg CLV"
                value={`${data.summary.avg_clv_pct > 0 ? '+' : ''}${data.summary.avg_clv_pct}%`}
                color={clvColor(data.summary.avg_clv_pct)}
              />
              <StatPill
                label="% Positive CLV"
                value={`${data.summary.pct_positive}%`}
                color={data.summary.pct_positive >= 50 ? 'green' : 'yellow'}
              />
              <StatPill
                label="Win Rate (best edge)"
                value={`${data.summary.win_rate_on_best_edge}%`}
                color={data.summary.win_rate_on_best_edge >= 45 ? 'green' : 'red'}
              />
            </div>

            <div className="bg-gray-800 rounded-lg p-4 mb-6">
              <h3 className="text-sm font-semibold text-gray-400 mb-3">Cumulative CLV over time</h3>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={data.running}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis
                    dataKey="date"
                    tick={{ fill: '#9ca3af', fontSize: 10 }}
                    tickFormatter={d => d ? d.slice(5) : ''}
                  />
                  <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} unit="%" />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151' }}
                    labelStyle={{ color: '#e5e7eb' }}
                    formatter={(v) => [`${v}%`, 'Cumulative CLV']}
                  />
                  <ReferenceLine y={0} stroke="#6b7280" strokeDasharray="4 2" />
                  <Line
                    type="monotone"
                    dataKey="cumulative_clv"
                    stroke="#22c55e"
                    dot={false}
                    strokeWidth={2}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="bg-gray-800 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-700 text-gray-300">
                  <tr>
                    <th className="px-3 py-2 text-left">Date</th>
                    <th className="px-3 py-2 text-left">Match</th>
                    <th className="px-3 py-2 text-center">Bet</th>
                    <th className="px-3 py-2 text-right">CLV %</th>
                    <th className="px-3 py-2 text-center">Result</th>
                    <th className="px-3 py-2 text-center">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {data.records.map((r, i) => (
                    <tr key={i} className={`border-t border-gray-700 ${i % 2 === 0 ? 'bg-gray-800' : 'bg-gray-850'}`}>
                      <td className="px-3 py-2 text-gray-400 whitespace-nowrap">{r.match_date}</td>
                      <td className="px-3 py-2 text-white">{r.home_team} v {r.away_team}</td>
                      <td className="px-3 py-2 text-center">
                        <span className={`font-bold ${r.bet_outcome === 'H' ? 'text-green-400' : r.bet_outcome === 'A' ? 'text-red-400' : 'text-yellow-400'}`}>
                          {r.bet_outcome}
                        </span>
                      </td>
                      <td className={`px-3 py-2 text-right font-mono font-semibold ${r.clv_pct > 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {r.clv_pct > 0 ? '+' : ''}{r.clv_pct}%
                      </td>
                      <td className="px-3 py-2 text-center">
                        {r.won == null
                          ? <span className="text-gray-500">—</span>
                          : r.won
                            ? <span className="text-green-400 font-bold">W</span>
                            : <span className="text-red-400 font-bold">L</span>
                        }
                      </td>
                      <td className="px-3 py-2 text-center text-gray-500 text-xs">{r.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )
      )}
    </div>
  );
}
