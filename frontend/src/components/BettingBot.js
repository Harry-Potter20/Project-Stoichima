import React, { useState, useEffect, useCallback } from 'react';

const COMPETITIONS = [
  { id: 'all', label: 'All' },
  { id: 'PL',  label: 'Premier League' },
  { id: 'PD',  label: 'La Liga' },
  { id: 'BL1', label: 'Bundesliga' },
  { id: 'SA',  label: 'Serie A' },
  { id: 'FL1', label: 'Ligue 1' },
  { id: 'WC',  label: 'World Cup' },
  { id: 'EC',  label: 'Euros' },
];

const EDGE_FILTERS = [
  { label: 'Any edge',  min: 0   },
  { label: '≥ 3%',     min: 3   },
  { label: '≥ 5%',     min: 5   },
  { label: '≥ 8%',     min: 8   },
];

const OUTCOME_COLORS = {
  H: { text: 'text-green-400',  bg: 'bg-green-900/40  border-green-700' },
  D: { text: 'text-yellow-400', bg: 'bg-yellow-900/40 border-yellow-700' },
  A: { text: 'text-red-400',    bg: 'bg-red-900/40    border-red-700' },
};
const OUTCOME_LABELS = { H: 'Home Win', D: 'Draw', A: 'Away Win' };

const LEAGUE_BADGES = {
  PL:  { label: 'PL',  color: 'bg-purple-700' },
  PD:  { label: 'LL',  color: 'bg-orange-700' },
  BL1: { label: 'BL',  color: 'bg-red-800' },
  SA:  { label: 'SA',  color: 'bg-blue-800' },
  FL1: { label: 'L1',  color: 'bg-teal-700' },
  WC:  { label: 'WC',  color: 'bg-amber-700' },
  EC:  { label: 'EC',  color: 'bg-indigo-700' },
};

function EdgeBar({ edge }) {
  const capped = Math.min(edge, 15);
  const color  = edge >= 8 ? 'bg-green-400' : edge >= 5 ? 'bg-green-500' : edge >= 3 ? 'bg-yellow-400' : 'bg-gray-500';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-gray-700 rounded h-1.5 overflow-hidden">
        <div className={`h-full rounded ${color}`} style={{ width: `${(capped / 15) * 100}%` }} />
      </div>
      <span className={`text-xs font-bold tabular-nums w-12 text-right ${
        edge >= 5 ? 'text-green-400' : edge >= 3 ? 'text-yellow-400' : 'text-gray-400'
      }`}>
        +{edge}%
      </span>
    </div>
  );
}

function RecommendationCard({ rec }) {
  const oc = OUTCOME_COLORS[rec.bet_on] || OUTCOME_COLORS.H;
  const badge = LEAGUE_BADGES[rec.competition];
  const formatDate = d => new Date(d).toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });

  return (
    <div className={`border rounded-lg p-4 ${oc.bg} flex flex-col gap-3`}>
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            {badge && (
              <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${badge.color} text-white`}>
                {badge.label}
              </span>
            )}
            <span className="text-gray-400 text-xs">{formatDate(rec.match_date)}</span>
          </div>
          <div className="font-semibold text-white">
            {rec.home_team} <span className="text-gray-500 font-normal">vs</span> {rec.away_team}
          </div>
        </div>
        <div className={`text-right shrink-0`}>
          <div className={`text-lg font-bold ${oc.text}`}>{OUTCOME_LABELS[rec.bet_on]}</div>
          <div className="text-2xl font-mono font-bold text-white">{rec.decimal_odds?.toFixed(2)}</div>
        </div>
      </div>

      {/* Edge bar */}
      <EdgeBar edge={rec.edge_pct} />

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div className="bg-gray-800/60 rounded p-2">
          <div className="text-gray-400">Model prob</div>
          <div className="font-bold text-white">{rec.model_prob}%</div>
        </div>
        <div className="bg-gray-800/60 rounded p-2">
          <div className="text-gray-400">Implied prob</div>
          <div className="font-bold text-gray-300">{rec.implied_prob}%</div>
        </div>
        <div className="bg-gray-800/60 rounded p-2">
          <div className="text-gray-400">Kelly stake</div>
          <div className="font-bold text-blue-400">{rec.kelly_pct}%</div>
        </div>
      </div>

      {rec.bookmaker && (
        <div className="text-xs text-gray-500 text-right">{rec.bookmaker}</div>
      )}
    </div>
  );
}

function SummaryPill({ label, value, color = 'gray' }) {
  const colors = {
    green:  'bg-green-900/50 border-green-700 text-green-300',
    yellow: 'bg-yellow-900/50 border-yellow-700 text-yellow-300',
    blue:   'bg-blue-900/50 border-blue-700 text-blue-300',
    gray:   'bg-gray-800 border-gray-600 text-gray-300',
  };
  return (
    <div className={`px-4 py-3 rounded-lg border text-center ${colors[color]}`}>
      <div className="text-xl font-bold">{value}</div>
      <div className="text-xs opacity-70 mt-0.5">{label}</div>
    </div>
  );
}

export default function BettingBot() {
  const [competition, setCompetition] = useState('all');
  const [minEdge, setMinEdge]         = useState(3);
  const [data, setData]               = useState(null);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ competition, min_edge: minEdge });
    fetch(`${process.env.REACT_APP_API_URL}/api/v1/bets/recommendations?${params}`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [competition, minEdge]);

  useEffect(() => { load(); }, [load]);

  const recs = data?.recommendations || [];
  const highEdge = recs.filter(r => r.edge_pct >= 5).length;

  return (
    <div>
      {/* Title + refresh */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold text-white">Betting Bot</h2>
          <p className="text-gray-500 text-sm mt-0.5">
            Auto-recommended bets where model edge exceeds bookmaker margin
          </p>
        </div>
        <button
          onClick={load}
          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-sm font-medium transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-6">
        <div className="flex gap-1 flex-wrap">
          {COMPETITIONS.map(c => (
            <button
              key={c.id}
              onClick={() => setCompetition(c.id)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                competition === c.id
                  ? 'bg-green-500 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
        <div className="border-l border-gray-600 pl-3 flex gap-1">
          {EDGE_FILTERS.map(f => (
            <button
              key={f.min}
              onClick={() => setMinEdge(f.min)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                minEdge === f.min
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="text-gray-400 py-12 text-center">Scanning for edges…</div>}
      {error   && <div className="text-red-400 py-12 text-center">Error: {error}</div>}

      {!loading && !error && data && (
        <>
          {/* Summary row */}
          {recs.length > 0 && (
            <div className="grid grid-cols-4 gap-3 mb-6">
              <SummaryPill label="Active bets"          value={recs.length}                          color="blue"   />
              <SummaryPill label="High confidence (≥5%)" value={highEdge}                            color={highEdge > 0 ? 'green' : 'gray'} />
              <SummaryPill label="Avg edge"             value={`+${data.avg_edge_pct}%`}            color="yellow" />
              <SummaryPill label="Total Kelly exposure" value={`${data.total_kelly_exposure}%`}      color="gray"   />
            </div>
          )}

          {recs.length === 0 ? (
            <div className="bg-gray-800 rounded-lg py-16 text-center">
              <div className="text-4xl mb-3">🤖</div>
              <div className="text-gray-400 font-medium">No qualifying bets found</div>
              <div className="text-gray-600 text-sm mt-1">
                {minEdge > 0
                  ? `No upcoming matches with edge ≥ ${minEdge}% in this selection`
                  : 'Bets are auto-logged when predictions are generated for upcoming matches'}
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {recs.map((rec, i) => <RecommendationCard key={i} rec={rec} />)}
            </div>
          )}

          {/* Disclaimer */}
          <div className="mt-6 text-xs text-gray-600 border-t border-gray-800 pt-4">
            Simulated paper bets only — not financial advice. Kelly stake is 1/4 Kelly of bankroll.
            Bets are auto-logged when model edge &gt; threshold vs bookmaker implied probability.
            Odds snapshot from The Odds API at prediction time.
          </div>
        </>
      )}
    </div>
  );
}
