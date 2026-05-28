import React, { useState, useEffect } from 'react';
import SpreadSelector from './SpreadSelector';

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

  return null;
}

const MARKET_HEADER = {
  outcome:        'Prediction',
  over_under:     'Over / Under',
  asian_handicap: 'Asian Handicap',
  correct_score:  'Correct Score',
  double_chance:  'Double Chance',
  btts:           'BTTS',
};

function PredictionsTable({ competition }) {
  const [predictions, setPredictions] = useState([]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);
  const [market, setMarket]           = useState('outcome');
  const [subOption, setSubOption]     = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`${process.env.REACT_APP_API_URL}/api/v1/predictions/${competition}`)
      .then(r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then(data => setPredictions(data.predictions))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [competition]);

  const formatDate = dateStr => new Date(dateStr).toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });

  if (loading) return <div className="text-gray-400 py-8 text-center">Loading predictions…</div>;
  if (error)   return <div className="text-red-400 py-8 text-center">Error: {error}</div>;
  if (!predictions.length) return <div className="text-gray-400 py-8 text-center">No upcoming matches found.</div>;

  return (
    <div>
      <SpreadSelector market={market} setMarket={setMarket} subOption={subOption} setSubOption={setSubOption} />
      <div className="bg-gray-800 rounded-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-green-800 text-white text-sm">
            <tr>
              <th className="px-4 py-3 text-left">Home</th>
              <th className="px-4 py-3 text-left">Away</th>
              <th className="px-4 py-3 text-left">Date</th>
              <th className="px-4 py-3 text-left">{MARKET_HEADER[market]}</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((p, i) => (
              <tr key={i} className={`border-t border-gray-700 hover:bg-gray-700 ${i % 2 === 0 ? 'bg-gray-800' : 'bg-gray-850'}`}>
                <td className="px-4 py-3 font-medium">{p.home_team}</td>
                <td className="px-4 py-3 text-gray-300">{p.away_team}</td>
                <td className="px-4 py-3 text-gray-400 text-sm whitespace-nowrap">{formatDate(p.match_date)}</td>
                <td className="px-4 py-3 min-w-[180px]">{renderMarketCell(p, market, subOption)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default PredictionsTable;
