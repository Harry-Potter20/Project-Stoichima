import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Legend,
  BarChart, Bar, Cell,
} from 'recharts';

const API = process.env.REACT_APP_API_URL;

const CLASS_COLORS = { H: '#22c55e', D: '#eab308', A: '#ef4444' };
const CLASS_LABELS = { H: 'Home Win', D: 'Draw', A: 'Away Win' };

function BrierBadge({ label, value, good }) {
  const color = value == null ? 'text-gray-500'
    : value < good   ? 'text-green-400'
    : value < good * 1.5 ? 'text-yellow-400'
    : 'text-red-400';
  return (
    <div className="bg-gray-700 rounded-lg px-4 py-3 flex flex-col items-center min-w-[110px]">
      <span className="text-gray-400 text-xs mb-1">{label} Brier</span>
      <span className={`text-xl font-bold tabular-nums ${color}`}>
        {value != null ? value.toFixed(3) : '—'}
      </span>
      <span className="text-gray-500 text-xs mt-0.5">lower = better</span>
    </div>
  );
}

function ReliabilityPlot({ curves, title }) {
  if (!curves || !Object.values(curves).some(c => c.reliability?.length)) {
    return <div className="text-gray-500 text-sm text-center py-4">No data yet.</div>;
  }

  // Merge all classes into a single dataset keyed by bin_center
  const binMap = {};
  for (const [cls, data] of Object.entries(curves)) {
    for (const pt of (data.reliability || [])) {
      if (!binMap[pt.bin_center]) binMap[pt.bin_center] = { bin: pt.bin_center };
      binMap[pt.bin_center][cls] = pt.actual;
    }
  }
  const chartData = Object.values(binMap).sort((a, b) => a.bin - b.bin);

  return (
    <div>
      <h4 className="text-gray-400 text-sm font-medium mb-2">{title}</h4>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={chartData} margin={{ top: 5, right: 15, left: -10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="bin"
            type="number"
            domain={[0, 1]}
            tickFormatter={v => `${(v * 100).toFixed(0)}%`}
            tick={{ fill: '#9ca3af', fontSize: 11 }}
          />
          <YAxis
            domain={[0, 1]}
            tickFormatter={v => `${(v * 100).toFixed(0)}%`}
            tick={{ fill: '#9ca3af', fontSize: 11 }}
          />
          <Tooltip
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6 }}
            labelStyle={{ color: '#f9fafb' }}
            labelFormatter={v => `Predicted: ${(v * 100).toFixed(0)}%`}
            formatter={(v, name) => [`${(v * 100).toFixed(1)}%`, CLASS_LABELS[name] || name]}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: '#9ca3af' }} />
          {/* Perfect calibration diagonal */}
          <ReferenceLine
            segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
            stroke="#4b5563"
            strokeDasharray="4 4"
            label={{ value: 'Perfect', fill: '#6b7280', fontSize: 10 }}
          />
          {Object.keys(curves).map(cls => (
            <Line
              key={cls}
              type="monotone"
              dataKey={cls}
              name={cls}
              stroke={CLASS_COLORS[cls] || '#6366f1'}
              strokeWidth={2}
              dot={{ r: 4, fill: CLASS_COLORS[cls] || '#6366f1' }}
              connectNulls={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function SingleReliabilityPlot({ reliability, title, color }) {
  if (!reliability?.length) {
    return <div className="text-gray-500 text-sm text-center py-4">No data yet.</div>;
  }
  const chartData = reliability.map(pt => ({ bin: pt.bin_center, actual: pt.actual, count: pt.count }));
  return (
    <div>
      <h4 className="text-gray-400 text-sm font-medium mb-2">{title}</h4>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData} margin={{ top: 5, right: 15, left: -10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="bin"
            type="number"
            domain={[0, 1]}
            tickFormatter={v => `${(v * 100).toFixed(0)}%`}
            tick={{ fill: '#9ca3af', fontSize: 11 }}
          />
          <YAxis
            domain={[0, 1]}
            tickFormatter={v => `${(v * 100).toFixed(0)}%`}
            tick={{ fill: '#9ca3af', fontSize: 11 }}
          />
          <Tooltip
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6 }}
            labelFormatter={v => `Predicted: ${(v * 100).toFixed(0)}%`}
            formatter={(v) => [`${(v * 100).toFixed(1)}%`, 'Actual rate']}
          />
          <ReferenceLine
            segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
            stroke="#4b5563"
            strokeDasharray="4 4"
          />
          <Line
            type="monotone"
            dataKey="actual"
            stroke={color}
            strokeWidth={2}
            dot={{ r: 4, fill: color }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function FeatureImportanceChart() {
  const [features, setFeatures] = useState(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(false);

  useEffect(() => {
    fetch(`${API}/admin/feature-importance`)
      .then(r => r.json())
      .then(d => { setFeatures((d.features || []).slice(0, 20)); setLoading(false); })
      .catch(() => { setError(true); setLoading(false); });
  }, []);

  if (loading) return <div className="text-gray-400 text-sm py-4 text-center">Loading feature importance…</div>;
  if (error || !features?.length) return (
    <div className="text-gray-500 text-sm text-center py-4">
      Feature importance unavailable — train the model first.
    </div>
  );

  const maxGain = features[0]?.importance ?? 1;
  const chartData = features.map(f => ({
    name: f.feature,
    gain: parseFloat((f.importance ?? 0).toFixed(4)),
    pct:  parseFloat((((f.importance ?? 0) / maxGain) * 100).toFixed(1)),
  }));

  return (
    <div>
      <h4 className="text-gray-400 text-sm font-medium mb-3">Top 20 features by importance</h4>
      <ResponsiveContainer width="100%" height={Math.max(300, chartData.length * 22)}>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ top: 2, right: 60, left: 160, bottom: 2 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" horizontal={false} />
          <XAxis
            type="number"
            dataKey="gain"
            tick={{ fill: '#9ca3af', fontSize: 10 }}
            tickFormatter={v => v.toFixed(3)}
          />
          <YAxis
            type="category"
            dataKey="name"
            width={155}
            tick={{ fill: '#d1d5db', fontSize: 11 }}
          />
          <Tooltip
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6 }}
            formatter={(v, _name, props) => [
              `${v.toFixed(4)} (${props.payload.pct}% of top)`,
              'Importance'
            ]}
          />
          <Bar dataKey="gain" radius={[0, 3, 3, 0]}>
            {chartData.map((_, idx) => (
              <Cell
                key={idx}
                fill={idx === 0 ? '#22c55e' : idx < 5 ? '#16a34a' : idx < 10 ? '#4ade80' : '#6b7280'}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const LEAGUES = [
  { id: 'PL',  label: 'Premier League' },
  { id: 'PD',  label: 'La Liga' },
  { id: 'BL1', label: 'Bundesliga' },
  { id: 'SA',  label: 'Serie A' },
  { id: 'FL1', label: 'Ligue 1' },
];

export default function CalibrationChart() {
  const [selected, setSelected] = useState('PL');
  const [data, setData]         = useState(null);
  const [loading, setLoading]   = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/v1/calibration/${selected}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [selected]);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">Model Calibration</h2>
        <p className="text-gray-400 text-sm">
          Reliability curves show whether predicted probabilities match real-world outcome rates.
          Points on the diagonal = perfectly calibrated. Brier score: lower is better (random ≈ 0.25).
        </p>
      </div>

      <div className="flex gap-2 flex-wrap">
        {LEAGUES.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setSelected(id)}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              selected === id ? 'bg-green-500 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading && <div className="text-gray-400 text-center py-8">Loading…</div>}

      {!loading && data && data.resolved === 0 && (
        <div className="bg-gray-800 rounded-lg p-8 text-center text-gray-500">
          No resolved predictions for {LEAGUES.find(l => l.id === selected)?.label} yet.
          Calibration data appears once predictions have been generated and matches have finished.
        </div>
      )}

      {!loading && data && data.resolved > 0 && (
        <>
          <div className="flex gap-3 flex-wrap">
            <BrierBadge label="Outcome (macro)"  value={data.outcome?.macro_brier}  good={0.19} />
            <BrierBadge label="Over / Under 2.5" value={data.over_2_5?.brier_score} good={0.22} />
            <BrierBadge label="BTTS"             value={data.btts?.brier_score}     good={0.22} />
            <div className="bg-gray-700 rounded-lg px-4 py-3 flex flex-col items-center min-w-[110px]">
              <span className="text-gray-400 text-xs mb-1">Resolved</span>
              <span className="text-xl font-bold text-white">{data.resolved}</span>
              <span className="text-gray-500 text-xs mt-0.5">predictions</span>
            </div>
          </div>

          <div className="bg-gray-800 rounded-lg p-5">
            <ReliabilityPlot
              curves={data.outcome?.per_class}
              title="Match Outcome — H / D / A reliability"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-gray-800 rounded-lg p-5">
              <SingleReliabilityPlot
                reliability={data.over_2_5?.reliability}
                title="Over / Under 2.5 reliability"
                color="#3b82f6"
              />
            </div>
            <div className="bg-gray-800 rounded-lg p-5">
              <SingleReliabilityPlot
                reliability={data.btts?.reliability}
                title="BTTS reliability"
                color="#eab308"
              />
            </div>
          </div>
        </>
      )}

      <div className="bg-gray-800 rounded-lg p-5">
        <FeatureImportanceChart />
      </div>
    </div>
  );
}
