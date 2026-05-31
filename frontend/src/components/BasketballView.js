import React, { useState } from 'react';

const API = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const COMPETITIONS = ['NBA', 'EuroLeague', 'EuroCup', 'NBL', 'BSL'];

function ProbBar({ homeProb, awayProb, homeTeam, awayTeam }) {
  const hp = Math.round(homeProb * 100);
  const ap = Math.round(awayProb * 100);
  return (
    <div className="mt-3">
      <div className="flex text-xs text-gray-400 justify-between mb-1">
        <span>{homeTeam}</span>
        <span>{awayTeam}</span>
      </div>
      <div className="flex rounded overflow-hidden h-5 text-xs font-bold">
        <div
          className="flex items-center justify-center bg-blue-600 text-white transition-all"
          style={{ width: `${hp}%` }}
        >{hp > 15 ? `${hp}%` : ''}</div>
        <div
          className="flex items-center justify-center bg-orange-500 text-white transition-all"
          style={{ width: `${ap}%` }}
        >{ap > 15 ? `${ap}%` : ''}</div>
      </div>
    </div>
  );
}

function ResultCard({ result }) {
  if (!result) return null;
  return (
    <div className="bg-gray-800 rounded-xl p-5 mt-4 border border-gray-700">
      <h3 className="text-white font-bold text-lg mb-3">
        {result.home_team} <span className="text-gray-400 text-sm">vs</span> {result.away_team}
        <span className="ml-2 text-xs text-gray-500 font-normal">[{result.competition}]</span>
      </h3>

      <ProbBar
        homeProb={result.home_win_prob}
        awayProb={result.away_win_prob}
        homeTeam={result.home_team}
        awayTeam={result.away_team}
      />

      <div className="grid grid-cols-2 gap-4 mt-4 text-sm">
        <div className="bg-gray-900 rounded-lg p-3">
          <p className="text-gray-400 text-xs mb-1">Home ELO</p>
          <p className="text-white font-mono text-lg">{result.home_elo}</p>
          <p className="text-gray-500 text-xs">{result.home_matches_played} games</p>
        </div>
        <div className="bg-gray-900 rounded-lg p-3">
          <p className="text-gray-400 text-xs mb-1">Away ELO</p>
          <p className="text-white font-mono text-lg">{result.away_elo}</p>
          <p className="text-gray-500 text-xs">{result.away_matches_played} games</p>
        </div>
        <div className="bg-gray-900 rounded-lg p-3">
          <p className="text-gray-400 text-xs mb-1">Fair Home Odds</p>
          <p className="text-white font-mono text-lg">{result.fair_home_odds}</p>
        </div>
        <div className="bg-gray-900 rounded-lg p-3">
          <p className="text-gray-400 text-xs mb-1">Fair Away Odds</p>
          <p className="text-white font-mono text-lg">{result.fair_away_odds}</p>
        </div>
      </div>

      <p className="text-gray-500 text-xs mt-3">
        Home court advantage: +{result.home_court_bonus} ELO points applied
      </p>
    </div>
  );
}

function RatingsTable({ ratings }) {
  if (!ratings || !ratings.teams.length) return null;
  return (
    <div className="bg-gray-800 rounded-xl p-5 mt-4 border border-gray-700">
      <h3 className="text-white font-bold mb-3">ELO Rankings — {ratings.competition}</h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-400 text-xs border-b border-gray-700">
            <th className="text-left pb-2">#</th>
            <th className="text-left pb-2">Team</th>
            <th className="text-right pb-2">ELO</th>
            <th className="text-right pb-2">Games</th>
          </tr>
        </thead>
        <tbody>
          {ratings.teams.map((t) => (
            <tr key={t.team} className="border-b border-gray-900 hover:bg-gray-700">
              <td className="py-1.5 text-gray-500">{t.rank}</td>
              <td className="py-1.5 text-white">{t.team}</td>
              <td className="py-1.5 text-right font-mono text-blue-400">{t.elo}</td>
              <td className="py-1.5 text-right text-gray-500">{t.matches}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function BasketballView() {
  const [competition, setCompetition] = useState('NBA');
  const [homeTeam, setHomeTeam] = useState('');
  const [awayTeam, setAwayTeam] = useState('');
  const [result, setResult] = useState(null);
  const [ratings, setRatings] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const predict = async () => {
    if (!homeTeam.trim() || !awayTeam.trim()) {
      setError('Enter both team names');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const params = new URLSearchParams({ home_team: homeTeam, away_team: awayTeam, competition });
      const res = await fetch(`${API}/api/v1/basketball/predict?${params}`);
      if (!res.ok) throw new Error(await res.text());
      setResult(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const loadRatings = async () => {
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/v1/basketball/ratings?competition=${competition}`);
      if (!res.ok) throw new Error(await res.text());
      setRatings(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto">
      <h2 className="text-2xl font-bold text-white mb-1">🏀 Basketball Predictor</h2>
      <p className="text-gray-400 text-sm mb-5">ELO-based win probability with home-court advantage</p>

      {/* Competition selector */}
      <div className="flex gap-2 flex-wrap mb-4">
        {COMPETITIONS.map(c => (
          <button
            key={c}
            onClick={() => { setCompetition(c); setResult(null); setRatings(null); }}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              competition === c
                ? 'bg-blue-600 text-white'
                : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
            }`}
          >{c}</button>
        ))}
      </div>

      {/* Input form */}
      <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="text-gray-400 text-xs mb-1 block">Home Team</label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white text-sm placeholder-gray-600 focus:outline-none focus:border-blue-500"
              placeholder="e.g. Boston Celtics"
              value={homeTeam}
              onChange={e => setHomeTeam(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && predict()}
            />
          </div>
          <div>
            <label className="text-gray-400 text-xs mb-1 block">Away Team</label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white text-sm placeholder-gray-600 focus:outline-none focus:border-blue-500"
              placeholder="e.g. LA Lakers"
              value={awayTeam}
              onChange={e => setAwayTeam(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && predict()}
            />
          </div>
        </div>

        <div className="flex gap-3">
          <button
            onClick={predict}
            disabled={loading}
            className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-medium py-2 rounded-lg transition-colors text-sm"
          >{loading ? 'Predicting…' : 'Predict Match'}</button>
          <button
            onClick={loadRatings}
            disabled={loading}
            className="flex-1 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white font-medium py-2 rounded-lg transition-colors text-sm"
          >View Rankings</button>
        </div>

        {error && <p className="text-red-400 text-sm mt-3">{error}</p>}
      </div>

      <ResultCard result={result} />
      <RatingsTable ratings={ratings} />
    </div>
  );
}
