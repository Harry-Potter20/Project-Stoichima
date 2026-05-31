import React, { useState, useCallback } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

const API = process.env.REACT_APP_API_URL;

const STAGES = [
  { key: 'r32',    label: 'R32',    color: '#6b7280' },
  { key: 'r16',    label: 'R16',    color: '#8b5cf6' },
  { key: 'qf',     label: 'QF',     color: '#3b82f6' },
  { key: 'sf',     label: 'SF',     color: '#f59e0b' },
  { key: 'final',  label: 'Final',  color: '#ef4444' },
  { key: 'winner', label: 'Winner', color: '#22c55e' },
];

const STAGE_COLORS = {
  r32: '#6b7280', r16: '#8b5cf6', qf: '#3b82f6',
  sf: '#f59e0b', final: '#ef4444', winner: '#22c55e',
};

function StageColumn({ stage, teams, highlight = 0 }) {
  const top = teams.slice(0, highlight || teams.length);
  return (
    <div className="flex flex-col gap-1 min-w-[90px]">
      <div className="text-center text-xs font-bold mb-1" style={{ color: STAGE_COLORS[stage.key] }}>
        {stage.label}
      </div>
      {top.map((t, i) => {
        const pct = (t[stage.key] * 100).toFixed(1);
        const isTop = i === 0;
        return (
          <div
            key={t.team}
            className={`rounded px-2 py-1 text-xs transition-all ${
              isTop
                ? 'border font-semibold text-white'
                : 'bg-gray-800 text-gray-400'
            }`}
            style={isTop ? { borderColor: STAGE_COLORS[stage.key], backgroundColor: `${STAGE_COLORS[stage.key]}20` } : {}}
          >
            <div className="truncate text-[11px] max-w-[80px]" title={t.team}>
              {t.team.length > 12 ? t.team.slice(0, 11) + '…' : t.team}
            </div>
            <div className="text-[10px]" style={{ color: isTop ? STAGE_COLORS[stage.key] : '#6b7280' }}>
              {pct}%
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function KnockoutBracket({ competitionId, simProbs }) {
  const [results, setResults]   = useState(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);
  const [n, setN]               = useState(5000);
  const [view, setView]         = useState('funnel');

  const probData = simProbs
    ? Object.entries(simProbs).map(([team, p]) => ({ team, ...p }))
    : results?.probabilities;

  const run = useCallback(() => {
    setLoading(true); setError(null);
    fetch(`${API}/api/v1/tournament/${competitionId}/simulate?n=${n}`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(d => { setResults(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [competitionId, n]);

  const hasSim = simProbs || results;

  const getTop = (stage, limit) =>
    (probData || [])
      .filter(t => t[stage] != null)
      .sort((a, b) => b[stage] - a[stage])
      .slice(0, limit);

  // Funnel: top-10 for winner, top-N at each stage
  const funnelStages = STAGES.map(s => ({
    ...s,
    teams: getTop(s.key, s.key === 'winner' ? 32 : s.key === 'final' ? 16 : s.key === 'sf' ? 12 : 8),
  }));

  // Top-8 contender cards
  const topContenders = getTop('winner', 8);

  // Likelihood chart for top-16
  const chartData = getTop('winner', 16).map(t => ({
    team: t.team.length > 14 ? t.team.slice(0, 13) + '…' : t.team,
    fullName: t.team,
    Win:   parseFloat((t.winner * 100).toFixed(1)),
    Final: parseFloat((t.final  * 100).toFixed(1)),
    SF:    parseFloat((t.sf     * 100).toFixed(1)),
    QF:    parseFloat((t.qf     * 100).toFixed(1)),
  }));

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="flex items-center gap-4 flex-wrap">
        {!hasSim && (
          <div className="flex items-center gap-2">
            <span className="text-gray-400 text-sm">Simulations:</span>
            {[1000, 5000, 10000].map(v => (
              <button key={v} onClick={() => setN(v)}
                className={`px-3 py-1 rounded text-sm ${n===v ? 'bg-green-500 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}>
                {v.toLocaleString()}
              </button>
            ))}
          </div>
        )}
        {!hasSim && (
          <button onClick={run} disabled={loading}
            className="px-5 py-2 bg-green-600 hover:bg-green-500 disabled:bg-gray-600 text-white rounded font-semibold text-sm transition-colors">
            {loading ? 'Simulating…' : 'Run Simulation'}
          </button>
        )}
        {hasSim && (
          <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
            {[{id:'funnel',label:'Stage Funnel'},{id:'cards',label:'Top Contenders'},{id:'chart',label:'Chart'}].map(t => (
              <button key={t.id} onClick={() => setView(t.id)}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${view===t.id ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
                {t.label}
              </button>
            ))}
          </div>
        )}
        {hasSim && simProbs && (
          <span className="text-gray-500 text-xs">Using results from Simulator tab</span>
        )}
      </div>

      {error && <div className="text-red-400 text-sm">{error}</div>}

      {!hasSim && !loading && (
        <div className="bg-gray-800 rounded-lg p-10 text-center text-gray-500">
          <div className="text-3xl mb-3">🏆</div>
          <p>Run the simulation to see bracket progression probabilities.</p>
          <p className="text-xs mt-1">Or run it in the Simulator tab first — results carry over automatically.</p>
        </div>
      )}

      {hasSim && view === 'funnel' && (
        <div className="bg-gray-800 rounded-lg p-5 overflow-x-auto">
          <h3 className="text-gray-300 font-semibold mb-1">Stage Progression — Top Teams at Each Round</h3>
          <p className="text-gray-500 text-xs mb-5">Each column shows teams ranked by probability of reaching that stage</p>
          <div className="flex gap-3 min-w-max">
            {funnelStages.map(s => (
              <StageColumn key={s.key} stage={s} teams={s.teams} highlight={s.key === 'winner' ? 5 : 4} />
            ))}
          </div>
        </div>
      )}

      {hasSim && view === 'cards' && (
        <div>
          <h3 className="text-gray-300 font-semibold mb-4">Top 8 Contenders — Full Bracket Progression</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {topContenders.map((t, i) => (
              <div key={t.team} className={`bg-gray-800 rounded-xl border p-4 ${i === 0 ? 'border-green-600' : 'border-gray-700'}`}>
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <div className="text-xs text-gray-500 mb-0.5">#{i+1}</div>
                    <div className="font-bold text-white text-sm">{t.team}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-green-400 font-bold text-lg">{(t.winner * 100).toFixed(1)}%</div>
                    <div className="text-xs text-gray-500">win</div>
                  </div>
                </div>
                <div className="space-y-1.5">
                  {STAGES.slice(0, 5).map(s => (
                    <div key={s.key} className="flex justify-between items-center">
                      <span className="text-xs text-gray-500 w-10">{s.label}</span>
                      <div className="flex-1 mx-2 h-1.5 bg-gray-700 rounded">
                        <div
                          className="h-1.5 rounded"
                          style={{ width: `${(t[s.key] || 0) * 100}%`, backgroundColor: STAGE_COLORS[s.key] }}
                        />
                      </div>
                      <span className="text-xs text-gray-400 w-10 text-right tabular-nums">
                        {((t[s.key] || 0) * 100).toFixed(1)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {hasSim && view === 'chart' && (
        <div className="bg-gray-800 rounded-lg p-5">
          <h3 className="text-gray-300 font-semibold mb-1">Stage Probability — Top 16</h3>
          <p className="text-gray-500 text-xs mb-4">Stacked bars show cumulative stage probabilities</p>
          <ResponsiveContainer width="100%" height={420}>
            <BarChart data={chartData} layout="vertical" margin={{ top: 0, right: 40, left: 110, bottom: 0 }}>
              <XAxis type="number" tick={{ fill: '#9ca3af', fontSize: 11 }} unit="%" domain={[0, 'auto']} />
              <YAxis type="category" dataKey="team" tick={{ fill: '#d1d5db', fontSize: 11 }} width={100} />
              <Tooltip
                contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
                labelStyle={{ color: '#f9fafb' }}
                formatter={(v, name) => [`${v}%`, name]}
                labelFormatter={(_, payload) => payload?.[0]?.payload?.fullName || ''}
              />
              <Bar dataKey="Win"   fill="#22c55e" radius={[0,4,4,0]} />
              <Bar dataKey="Final" fill="#ef4444" radius={[0,4,4,0]} />
              <Bar dataKey="SF"    fill="#f59e0b" radius={[0,4,4,0]} />
              <Bar dataKey="QF"    fill="#3b82f6" radius={[0,4,4,0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
