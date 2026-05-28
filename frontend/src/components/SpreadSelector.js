import React from 'react';

const MARKETS = [
  { key: 'outcome',         label: 'Match Result' },
  { key: 'over_under',      label: 'Goal Line' },
  { key: 'asian_handicap',  label: 'Asian Handicap' },
  { key: 'correct_score',   label: 'Correct Score' },
  { key: 'double_chance',   label: 'Double Chance' },
  { key: 'btts',            label: 'BTTS' },
];

const GOAL_LINES   = ['1.5', '2.5', '3.5', '4.5'];
const AH_LINES     = ['-2.0', '-1.5', '-1.0', '-0.5', '0.0', '0.5', '1.0', '1.5', '2.0'];

export default function SpreadSelector({ market, setMarket, subOption, setSubOption }) {
  return (
    <div className="flex flex-wrap gap-2 mb-4 items-center">
      {MARKETS.map(m => (
        <button
          key={m.key}
          onClick={() => { setMarket(m.key); setSubOption(null); }}
          className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
            market === m.key
              ? 'bg-green-500 text-white'
              : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
          }`}
        >
          {m.label}
        </button>
      ))}

      {market === 'over_under' && (
        <div className="flex gap-1 ml-2 border-l border-gray-600 pl-2">
          {GOAL_LINES.map(line => (
            <button
              key={line}
              onClick={() => setSubOption(line)}
              className={`px-2 py-1 rounded text-xs font-medium ${
                subOption === line ? 'bg-blue-500 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {line}
            </button>
          ))}
        </div>
      )}

      {market === 'asian_handicap' && (
        <div className="flex flex-wrap gap-1 ml-2 border-l border-gray-600 pl-2">
          {AH_LINES.map(line => (
            <button
              key={line}
              onClick={() => setSubOption(line)}
              className={`px-2 py-1 rounded text-xs font-medium ${
                subOption === line ? 'bg-blue-500 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {parseFloat(line) > 0 ? `+${line}` : line}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
