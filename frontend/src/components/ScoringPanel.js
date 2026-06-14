import React, { useState, useEffect } from 'react';

const API = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const COMP_LABELS = {
  PL: 'Premier League', PD: 'La Liga', BL1: 'Bundesliga', SA: 'Serie A',
  FL1: 'Ligue 1', WC: 'World Cup', EC: 'Euros',
};

function skillColor(skill) {
  if (skill === null || skill === undefined) return 'text-gray-400';
  if (skill > 0.02) return 'text-green-400';
  if (skill > -0.02) return 'text-yellow-400';
  return 'text-red-400';
}

function Bar({ model, market }) {
  // Lower Brier is better; show both relative to the larger value.
  if (model == null || market == null) return null;
  const max = Math.max(model, market) * 1.1;
  const mw = `${(model / max) * 100}%`;
  const kw = `${(market / max) * 100}%`;
  return (
    <div className="space-y-1 w-40">
      <div className="flex items-center gap-2">
        <span className="text-[10px] w-12 text-gray-400">Model</span>
        <div className="flex-1 bg-gray-700 rounded h-2"><div className="bg-emerald-500 h-2 rounded" style={{ width: mw }} /></div>
        <span className="text-[10px] w-10 text-right tabular-nums">{model.toFixed(3)}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[10px] w-12 text-gray-400">Market</span>
        <div className="flex-1 bg-gray-700 rounded h-2"><div className="bg-sky-500 h-2 rounded" style={{ width: kw }} /></div>
        <span className="text-[10px] w-10 text-right tabular-nums">{market.toFixed(3)}</span>
      </div>
    </div>
  );
}

export default function ScoringPanel() {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/v1/scoring`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-gray-400">Scoring model vs market…</div>;
  if (error)   return <div className="text-red-400">Error: {error}</div>;

  const rows = data?.competitions || [];

  return (
    <div>
      <div className="mb-5">
        <h2 className="text-xl font-bold text-white">Model vs Market</h2>
        <p className="text-sm text-gray-400 mt-1 max-w-2xl">
          The honest scorecard: model probabilities scored against the bookmaker
          <span className="text-gray-300"> closing line</span> on the same resolved matches.
          <span className="text-emerald-400"> Brier skill &gt; 0</span> means we beat the market —
          a real edge. Argmax accuracy is shown for context but is a weak metric for three-way markets.
        </p>
      </div>

      {rows.length === 0 ? (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-6 text-gray-400">
          No resolved matches with closing odds yet. World Cup lines populate as
          results and odds come in.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-700">
          <table className="w-full text-sm">
            <thead className="bg-gray-800 text-gray-400 text-xs uppercase tracking-wide">
              <tr>
                <th className="text-left px-4 py-3">Competition</th>
                <th className="text-left px-4 py-3">Brier (lower = better)</th>
                <th className="text-right px-4 py-3">Skill vs market</th>
                <th className="text-right px-4 py-3">Acc (model / mkt)</th>
                <th className="text-right px-4 py-3">N</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {rows.map(r => (
                <tr key={r.competition} className="bg-gray-900 hover:bg-gray-800/60">
                  <td className="px-4 py-3 font-semibold text-white">
                    {COMP_LABELS[r.competition] || r.competition}
                    {r.n_with_odds < 50 && (
                      <span className="ml-2 text-[10px] text-yellow-500 font-normal">provisional</span>
                    )}
                  </td>
                  <td className="px-4 py-3"><Bar model={r.model_brier} market={r.market_brier} /></td>
                  <td className={`px-4 py-3 text-right font-bold tabular-nums ${skillColor(r.brier_skill_vs_market)}`}>
                    {r.brier_skill_vs_market != null
                      ? `${r.brier_skill_vs_market > 0 ? '+' : ''}${(r.brier_skill_vs_market * 100).toFixed(1)}%`
                      : '—'}
                    {r.beats_market && <span className="ml-1">✓</span>}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-300">
                    {(r.model_accuracy * 100).toFixed(0)}% / {(r.market_accuracy * 100).toFixed(0)}%
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-400">{r.n_with_odds}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-gray-500 mt-4">
        Brier skill = 1 − model_brier / market_brier. A model can be well-calibrated
        (good for goals markets) yet trail the market on 1X2 — this view tells them apart.
      </p>
    </div>
  );
}
