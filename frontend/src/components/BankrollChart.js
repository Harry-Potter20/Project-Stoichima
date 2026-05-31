import React, { useState, useEffect, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';

const COMPETITIONS = [
  { id: 'all', label: 'All leagues' },
  { id: 'PL',  label: 'Premier League' },
  { id: 'PD',  label: 'La Liga' },
  { id: 'BL1', label: 'Bundesliga' },
  { id: 'SA',  label: 'Serie A' },
  { id: 'FL1', label: 'Ligue 1' },
  { id: 'WC',  label: 'World Cup' },
  { id: 'EC',  label: 'Euros' },
];

function exportCSV(competition) {
  const comp = competition === 'all' ? '' : `?competition=${competition}`;
  const url = `${process.env.REACT_APP_API_URL}/api/v1/bets/export.csv${comp}`;
  const a = document.createElement('a');
  a.href = url;
  a.download = `bets_${competition}_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

function Pill({ label, value, color = 'gray', small = false }) {
  const colors = {
    green:  'bg-green-900 border-green-600 text-green-300',
    red:    'bg-red-900 border-red-600 text-red-300',
    yellow: 'bg-yellow-900 border-yellow-600 text-yellow-300',
    blue:   'bg-blue-900 border-blue-600 text-blue-300',
    gray:   'bg-gray-700 border-gray-600 text-gray-300',
  };
  return (
    <div className={`px-3 py-2 rounded border text-center ${colors[color]} ${small ? 'text-sm' : ''}`}>
      <div className={`font-bold ${small ? 'text-base' : 'text-lg'}`}>{value}</div>
      <div className="text-xs opacity-70">{label}</div>
    </div>
  );
}

function BreakdownTable({ title, rows, keys, labels, keyField = 'label' }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="mb-4">
      <h4 className="text-xs font-semibold text-gray-400 mb-2">{title}</h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-gray-700 text-gray-400">
            <tr>
              <th className="px-3 py-1.5 text-left">{keyField === 'tag' ? 'Tag' : keyField === 'outcome' ? 'Outcome' : 'Competition'}</th>
              {labels.map((l, i) => <th key={i} className="px-3 py-1.5 text-right">{l}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={`border-t border-gray-700 ${i % 2 === 0 ? 'bg-gray-800' : ''}`}>
                <td className="px-3 py-1.5 font-semibold text-white">{r[keyField]}</td>
                {keys.map((k, j) => (
                  <td key={j} className={`px-3 py-1.5 text-right tabular-nums ${
                    k === 'roi_pct' || k === 'total_pl'
                      ? (parseFloat(r[k]) >= 0 ? 'text-green-400' : 'text-red-400')
                      : 'text-gray-300'
                  }`}>
                    {k === 'roi_pct' ? `${r[k] >= 0 ? '+' : ''}${r[k]}%`
                      : k === 'total_pl' ? `${r[k] >= 0 ? '+' : ''}${r[k]}`
                      : k === 'win_rate' ? `${r[k]}%`
                      : r[k]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function BankrollChart() {
  const [competition, setCompetition] = useState('all');
  const [data, setData]               = useState(null);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);
  const [breakdown, setBreakdown]     = useState(null);
  const [tagData, setTagData]         = useState(null);
  const [showAnalytics, setShowAnalytics] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setBreakdown(null);
    setTagData(null);
    setShowAnalytics(false);
    fetch(`${process.env.REACT_APP_API_URL}/api/v1/bankroll/${competition}`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(setData)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [competition]);

  const loadAnalytics = useCallback(() => {
    setShowAnalytics(true);
    const base = process.env.REACT_APP_API_URL;
    const comp = competition === 'all' ? '' : `?competition=${competition}`;
    Promise.all([
      fetch(`${base}/api/v1/bets/market_breakdown${comp}`).then(r => r.json()),
      fetch(`${base}/api/v1/bets/tags`).then(r => r.json()),
    ]).then(([bd, td]) => {
      setBreakdown(bd);
      setTagData(td);
    }).catch(() => {});
  }, [competition]);

  const s = data?.summary;
  const plColor = s ? (s.total_pl_units >= 0 ? 'green' : 'red') : 'gray';
  const roiColor = s ? (s.roi_pct >= 0 ? 'green' : 'red') : 'gray';

  return (
    <div>
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <h2 className="text-xl font-bold text-white">Bankroll Simulator</h2>
        <button
          onClick={() => exportCSV(competition)}
          className="px-3 py-1.5 rounded text-sm font-semibold bg-blue-800 text-blue-300 hover:bg-blue-700 transition-colors"
        >
          ⬇ Export CSV
        </button>
      </div>
      <div className="flex gap-1 flex-wrap mb-6">
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

      {loading && <div className="text-gray-400 py-12 text-center">Loading bankroll data…</div>}
      {error   && <div className="text-red-400 py-12 text-center">Error: {error}</div>}

      {!loading && !error && data && (
        data.count === 0 ? (
          <div className="text-gray-500 py-12 text-center">
            No bets logged yet. Bets are auto-logged when the model finds edge ≥ 3%.
          </div>
        ) : (
          <>
            {s && (
              <div className="grid grid-cols-4 gap-3 mb-6">
                <Pill
                  label="Total P&L (units)"
                  value={`${s.total_pl_units >= 0 ? '+' : ''}${s.total_pl_units}`}
                  color={plColor}
                />
                <Pill label="ROI" value={`${s.roi_pct >= 0 ? '+' : ''}${s.roi_pct}%`} color={roiColor} />
                <Pill label="Win Rate" value={`${s.win_rate_pct}%`} color={s.win_rate_pct >= 50 ? 'green' : 'yellow'} />
                <Pill label="Bets (resolved/total)" value={`${s.resolved}/${s.total_bets}`} color="blue" />
              </div>
            )}

            {data.running.length > 0 && (
              <div className="bg-gray-800 rounded-lg p-4 mb-6">
                <h3 className="text-sm font-semibold text-gray-400 mb-3">Cumulative P&L (units)</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={data.running}>
                    <defs>
                      <linearGradient id="plGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: '#9ca3af', fontSize: 10 }}
                      tickFormatter={d => d ? d.slice(5) : ''}
                    />
                    <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151' }}
                      labelStyle={{ color: '#e5e7eb' }}
                      formatter={(v, _, p) => [`${v > 0 ? '+' : ''}${v} units`, p.payload.match]}
                    />
                    <ReferenceLine y={0} stroke="#6b7280" strokeDasharray="4 2" />
                    <Area
                      type="monotone"
                      dataKey="cumulative_units"
                      stroke="#22c55e"
                      fill="url(#plGradient)"
                      strokeWidth={2}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Analytics toggle */}
            <div className="mb-4 flex justify-end">
              <button
                onClick={loadAnalytics}
                className="text-xs px-3 py-1.5 rounded border border-gray-600 text-gray-400 hover:text-white hover:border-gray-400 transition-colors"
              >
                {showAnalytics ? '▴ Hide Analytics' : '▾ ROI Breakdown & Tag Analytics'}
              </button>
            </div>

            {showAnalytics && (
              <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 mb-6">
                <h3 className="text-sm font-semibold text-gray-300 mb-4">Performance Breakdown</h3>
                {breakdown && (
                  <>
                    <BreakdownTable
                      title="By Outcome (H/D/A)"
                      rows={Object.entries(breakdown.by_outcome || {}).map(([k, v]) => ({ outcome: k, ...v }))}
                      keyField="outcome"
                      keys={['bets', 'won', 'win_rate', 'total_pl', 'roi_pct', 'avg_edge']}
                      labels={['Bets', 'Won', 'Win %', 'P&L', 'ROI', 'Avg Edge']}
                    />
                    <BreakdownTable
                      title="By Competition"
                      rows={Object.entries(breakdown.by_competition || {}).map(([k, v]) => ({ label: k, ...v }))}
                      keyField="label"
                      keys={['bets', 'won', 'win_rate', 'total_pl', 'roi_pct', 'avg_edge']}
                      labels={['Bets', 'Won', 'Win %', 'P&L', 'ROI', 'Avg Edge']}
                    />
                  </>
                )}
                {tagData?.tags?.length > 0 && (
                  <BreakdownTable
                    title="By Tag"
                    rows={tagData.tags}
                    keyField="tag"
                    keys={['bets', 'won', 'win_rate', 'total_pl', 'roi_pct', 'avg_edge']}
                    labels={['Bets', 'Won', 'Win %', 'P&L', 'ROI', 'Avg Edge']}
                  />
                )}
                {breakdown && !tagData?.tags?.length && (
                  <p className="text-gray-600 text-xs mt-2">No tagged bets found. Add tags when logging bets to track performance by strategy.</p>
                )}
              </div>
            )}

            <div className="bg-gray-800 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-700 text-gray-300">
                  <tr>
                    <th className="px-3 py-2 text-left">Date</th>
                    <th className="px-3 py-2 text-left">Match</th>
                    <th className="px-3 py-2 text-center">Bet</th>
                    <th className="px-3 py-2 text-right">Odds</th>
                    <th className="px-3 py-2 text-right">Edge</th>
                    <th className="px-3 py-2 text-right">Kelly %</th>
                    <th className="px-3 py-2 text-center">Result</th>
                    <th className="px-3 py-2 text-right">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {data.bets.map((b, i) => (
                    <tr key={i} className={`border-t border-gray-700 ${i % 2 === 0 ? 'bg-gray-800' : ''}`}>
                      <td className="px-3 py-2 text-gray-400 whitespace-nowrap">{b.match_date}</td>
                      <td className="px-3 py-2 text-white text-xs">{b.home_team} v {b.away_team}</td>
                      <td className="px-3 py-2 text-center">
                        <span className={`font-bold ${b.bet_on === 'H' ? 'text-green-400' : b.bet_on === 'A' ? 'text-red-400' : 'text-yellow-400'}`}>
                          {b.bet_on}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right text-gray-300 tabular-nums">{b.decimal_odds?.toFixed(2)}</td>
                      <td className="px-3 py-2 text-right text-blue-400 tabular-nums font-semibold">+{b.edge_pct}%</td>
                      <td className="px-3 py-2 text-right text-gray-400 tabular-nums">{b.kelly_pct}%</td>
                      <td className="px-3 py-2 text-center">
                        {b.won == null
                          ? <span className="text-gray-500 text-xs">Pending</span>
                          : b.won
                            ? <span className="text-green-400 font-bold">W</span>
                            : <span className="text-red-400 font-bold">L</span>
                        }
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums font-semibold ${
                        b.pl_units == null ? 'text-gray-500' : b.pl_units > 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {b.pl_units == null ? '—' : `${b.pl_units > 0 ? '+' : ''}${b.pl_units}`}
                      </td>
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
