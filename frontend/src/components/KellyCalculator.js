import React, { useState, useMemo } from 'react';

function Result({ label, value, color = 'white', sub }) {
  const colorMap = {
    green:  'text-green-400',
    red:    'text-red-400',
    yellow: 'text-yellow-400',
    blue:   'text-blue-400',
    white:  'text-white',
  };
  return (
    <div className="bg-gray-700 rounded-lg p-3 text-center">
      <div className={`text-lg font-bold ${colorMap[color]}`}>{value}</div>
      <div className="text-xs text-gray-400 mt-0.5">{label}</div>
      {sub && <div className="text-xs text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export default function KellyCalculator() {
  const [prob,     setProb]     = useState('');
  const [odds,     setOdds]     = useState('');
  const [bankroll, setBankroll] = useState('1000');

  const p = parseFloat(prob) / 100;
  const o = parseFloat(odds);
  const b = parseFloat(bankroll);

  const results = useMemo(() => {
    if (!p || !o || !b || p <= 0 || p >= 1 || o <= 1 || b <= 0) return null;
    const edge = o - 1;
    const kellFull = (p * edge - (1 - p)) / edge;
    if (kellFull <= 0) return { negative: true };
    const kellQuarter = kellFull * 0.25;
    const kellHalf    = kellFull * 0.5;
    const ev          = (p * (o - 1) - (1 - p)) * 100;
    return {
      negative:     false,
      fullKelly:    (kellFull * 100).toFixed(2),
      quarterKelly: (kellQuarter * 100).toFixed(2),
      halfKelly:    (kellHalf * 100).toFixed(2),
      stakeQuarter: (kellQuarter * b).toFixed(2),
      stakeHalf:    (kellHalf * b).toFixed(2),
      ev:           ev.toFixed(2),
      impliedProb:  (1 / o * 100).toFixed(1),
      edge:         ((p - 1 / o) * 100).toFixed(2),
    };
  }, [p, o, b]);

  return (
    <div className="max-w-lg mx-auto">
      <h2 className="text-xl font-bold text-white mb-1">Kelly Calculator</h2>
      <p className="text-gray-400 text-sm mb-6">
        Enter your model probability, decimal odds, and bankroll to get optimal stake sizes.
      </p>

      <div className="bg-gray-800 rounded-xl border border-gray-700 p-5 space-y-4">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Model Probability (%)</label>
            <input
              type="number"
              min="0" max="100" step="0.1"
              placeholder="e.g. 55"
              value={prob}
              onChange={e => setProb(e.target.value)}
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white placeholder-gray-500 text-sm focus:outline-none focus:border-green-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Decimal Odds</label>
            <input
              type="number"
              min="1.01" step="0.01"
              placeholder="e.g. 2.10"
              value={odds}
              onChange={e => setOdds(e.target.value)}
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white placeholder-gray-500 text-sm focus:outline-none focus:border-green-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Bankroll (£)</label>
            <input
              type="number"
              min="1" step="1"
              placeholder="e.g. 1000"
              value={bankroll}
              onChange={e => setBankroll(e.target.value)}
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white placeholder-gray-500 text-sm focus:outline-none focus:border-green-500"
            />
          </div>
        </div>

        {results === null && (
          <div className="text-center text-gray-500 py-4 text-sm">
            Fill in all three fields to see Kelly stake recommendations.
          </div>
        )}

        {results?.negative && (
          <div className="bg-red-900 border border-red-700 rounded-lg p-4 text-center">
            <div className="text-red-300 font-bold text-lg">No Edge</div>
            <p className="text-red-400 text-sm mt-1">
              Kelly is negative — the model probability does not beat the implied odds. Do not bet.
            </p>
          </div>
        )}

        {results && !results.negative && (
          <>
            <div className="grid grid-cols-3 gap-3">
              <Result
                label="Edge vs. Book"
                value={`${results.edge > 0 ? '+' : ''}${results.edge}%`}
                color={parseFloat(results.edge) > 0 ? 'green' : 'red'}
              />
              <Result
                label="Expected Value"
                value={`${parseFloat(results.ev) > 0 ? '+' : ''}${results.ev}%`}
                color={parseFloat(results.ev) > 0 ? 'green' : 'red'}
              />
              <Result
                label="Implied Prob"
                value={`${results.impliedProb}%`}
                color="blue"
                sub="book's estimate"
              />
            </div>

            <div className="border-t border-gray-700 pt-4">
              <h3 className="text-sm font-semibold text-gray-300 mb-3">Recommended Stakes</h3>
              <div className="grid grid-cols-3 gap-3">
                <Result
                  label="Full Kelly"
                  value={`${results.fullKelly}%`}
                  color="yellow"
                  sub="aggressive"
                />
                <Result
                  label="Half Kelly"
                  value={`${results.halfKelly}% · £${results.stakeHalf}`}
                  color="green"
                  sub="balanced"
                />
                <Result
                  label="Quarter Kelly"
                  value={`${results.quarterKelly}% · £${results.stakeQuarter}`}
                  color="white"
                  sub="conservative ✓"
                />
              </div>
            </div>

            <div className="bg-gray-700/50 rounded-lg p-3 text-xs text-gray-400">
              <strong className="text-gray-300">Recommendation:</strong> Quarter-Kelly (conservative) is used by the Stoichima auto-logger. It reduces variance significantly while capturing most of the EV growth. Full Kelly is mathematically optimal but assumes perfect probability estimates.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
