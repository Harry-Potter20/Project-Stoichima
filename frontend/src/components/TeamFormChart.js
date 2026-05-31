import React, { useState, useEffect } from 'react';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer,
} from 'recharts';

const API = process.env.REACT_APP_API_URL;

const RESULT_BG = {
  W: 'bg-green-600',
  D: 'bg-yellow-500',
  L: 'bg-red-600',
};

function resultLabel(result, venue) {
  if (!result) return '?';
  if (result === 'D') return 'D';
  if ((result === 'H' && venue === 'H') || (result === 'A' && venue === 'A')) return 'W';
  return 'L';
}

export default function TeamFormChart({ competition, team }) {
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);

  useEffect(() => {
    if (!team) return;
    setLoading(true);
    setError(null);
    fetch(`${API}/api/v1/form/${competition}/${encodeURIComponent(team)}?n=10`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(d => { setData(d.form); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [competition, team]);

  if (!team)    return null;
  if (loading)  return <div className="text-gray-500 text-xs py-2 text-center">Loading form…</div>;
  if (error)    return <div className="text-red-500 text-xs py-2">{error}</div>;
  if (!data?.length) return <div className="text-gray-500 text-xs py-2 text-center">No form data.</div>;

  const chartData = data.map((m, i) => ({
    match: `M${i + 1}`,
    opponent: m.opponent,
    venue: m.venue,
    GF: m.goals_for,
    GA: m.goals_against,
    xG: m.xg_for ?? undefined,
    pts: m.points,
    res: resultLabel(m.result, m.venue),
  }));

  return (
    <div>
      {/* Result pills */}
      <div className="flex gap-1 mb-3">
        {data.map((m, i) => {
          const lbl = resultLabel(m.result, m.venue);
          return (
            <div key={i} className="text-center">
              <div className={`w-7 h-7 rounded flex items-center justify-center text-xs font-bold text-white ${RESULT_BG[lbl] || 'bg-gray-600'}`}>
                {lbl}
              </div>
              <div className="text-gray-500 text-xs mt-0.5">{m.venue}</div>
            </div>
          );
        })}
      </div>

      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="match" tick={{ fill: '#6b7280', fontSize: 10 }} />
          <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} />
          <Tooltip
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
            labelStyle={{ color: '#f9fafb' }}
            labelFormatter={(_, payload) => payload?.[0]?.payload?.opponent || ''}
            formatter={(v, name) => [v, name]}
          />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <Bar dataKey="GF" fill="#22c55e" radius={[2, 2, 0, 0]} name="Goals For" />
          <Bar dataKey="GA" fill="#ef4444" radius={[2, 2, 0, 0]} name="Goals Against" />
          {data.some(m => m.xg_for != null) && (
            <Line type="monotone" dataKey="xG" stroke="#facc15" strokeWidth={2} dot={false} name="xG" />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
