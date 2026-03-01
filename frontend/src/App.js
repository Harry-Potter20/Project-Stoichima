import React, { useState } from 'react';
import PredictionsTable from './components/PredictionsTable';


function App() {
  const [competition, setCompetition] = useState('PL');

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="bg-gray-800 border-b border-gray-700 px-6 py-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-green-400">⚽ Football Predictor</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setCompetition('PL')}
            className={`px-4 py-2 rounded font-semibold ${competition === 'PL' ? 'bg-green-500' : 'bg-gray-600'}`}
          >
            Premier League
          </button>
          <button
            onClick={() => setCompetition('PD')}
            className={`px-4 py-2 rounded font-semibold ${competition === 'PD' ? 'bg-green-500' : 'bg-gray-600'}`}
          >
            La Liga
          </button>
        </div>
      </nav>
      <main className="max-w-6xl mx-auto px-6 py-8">
        <PredictionsTable competition={competition} />
      </main>
    </div>
  );
}

export default App;