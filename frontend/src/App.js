import React, { useState } from 'react';
import PredictionsTable from './components/PredictionsTable';
import AccuracyWidget from './components/AccuracyWidget';


function App() {
  const [competition, setCompetition] = useState('PL');

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="bg-gray-800 border-b border-gray-700 px-6 py-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-green-400">⚽ Football Predictor</h1>
        <div className="flex items-center gap-3">
          <AccuracyWidget competition={competition} />
          <div className="flex gap-2">
            {[
              { id: 'PL',  label: 'Premier League' },
              { id: 'PD',  label: 'La Liga' },
              { id: 'BL1', label: 'Bundesliga' },
              { id: 'SA',  label: 'Serie A' },
              { id: 'FL1', label: 'Ligue 1' },
            ].map(({ id, label }) => (
              <button
                key={id}
                onClick={() => setCompetition(id)}
                className={`px-3 py-2 rounded font-semibold text-sm ${competition === id ? 'bg-green-500' : 'bg-gray-600 hover:bg-gray-500'}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </nav>
      <main className="max-w-6xl mx-auto px-6 py-8">
        <PredictionsTable competition={competition} />
      </main>
    </div>
  );
}

export default App;