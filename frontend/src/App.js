import React, { useState } from 'react';
import PredictionsTable from './components/PredictionsTable';
import AccuracyTracker from './components/AccuracyTracker';
import CalibrationChart from './components/CalibrationChart';
import TournamentView from './components/TournamentView';
import CLVChart from './components/CLVChart';
import BankrollChart from './components/BankrollChart';
import BettingBot from './components/BettingBot';
import TennisView from './components/TennisView';
import BasketballView from './components/BasketballView';
import BetBuilder from './components/BetBuilder';
import KellyCalculator from './components/KellyCalculator';
import { BetSlipProvider } from './context/BetSlipContext';

const TOP_LEAGUES = [
  { id: 'PL',  label: 'Premier League' },
  { id: 'PD',  label: 'La Liga' },
  { id: 'BL1', label: 'Bundesliga' },
  { id: 'SA',  label: 'Serie A' },
  { id: 'FL1', label: 'Ligue 1' },
];

const LOWER_LEAGUES = [
  { id: 'ELC', label: 'Championship' },
  { id: 'BL2', label: '2. Bundesliga' },
  { id: 'PD2', label: 'La Liga 2' },
  { id: 'SB',  label: 'Serie B' },
  { id: 'FL2', label: 'Ligue 2' },
  { id: 'SPL', label: 'Scottish Prem' },
  { id: 'EL1', label: 'League One' },
  { id: 'EL2', label: 'League Two' },
];

const OTHER_EUROPEAN = [
  { id: 'ERE', label: 'Eredivisie' },
  { id: 'JPL', label: 'Belgian Pro' },
  { id: 'PPL', label: 'Primeira Liga' },
  { id: 'TSL', label: 'Süper Lig' },
  { id: 'GSL', label: 'Greek SL' },
];

const TABS = [
  { id: 'predictions',  label: 'Predictions' },
  { id: 'accuracy',     label: 'Accuracy' },
  { id: 'calibration',  label: 'Calibration' },
  { id: 'edge',         label: 'Edge / CLV' },
  { id: 'tournament',   label: 'WC 2026 / Euros' },
  { id: 'tennis',       label: '🎾 Tennis' },
  { id: 'basketball',   label: '🏀 Basketball' },
];

const EDGE_TABS = [
  { id: 'bot',      label: '🤖 Betting Bot' },
  { id: 'clv',      label: 'Closing Line Value' },
  { id: 'bankroll', label: 'Bankroll Simulator' },
  { id: 'kelly',    label: '🧮 Kelly Calculator' },
];

function App() {
  const [tab, setTab]                   = useState('predictions');
  const [edgeTab, setEdgeTab]           = useState('bot');
  const [competition, setCompetition]   = useState('PL');
  const [showLower, setShowLower]       = useState(false);

  return (
    <BetSlipProvider>
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="bg-gray-800 border-b border-gray-700 px-4 md:px-6 py-3 md:py-4">
        <div className="flex items-center justify-between gap-2">
          <h1 className="text-xl md:text-2xl font-bold text-green-400 shrink-0">⚽ Stoichima</h1>
          {/* Desktop tabs */}
          <div className="hidden md:flex gap-1 flex-wrap justify-end">
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-3 py-1.5 rounded font-semibold text-sm transition-colors ${
                  tab === t.id ? 'bg-green-500 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >{t.label}</button>
            ))}
          </div>
          {/* Mobile: scrollable tab strip */}
          <div className="flex md:hidden gap-1 overflow-x-auto pb-0.5 max-w-[calc(100vw-120px)]">
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-2.5 py-1 rounded text-xs font-semibold whitespace-nowrap transition-colors ${
                  tab === t.id ? 'bg-green-500 text-white' : 'bg-gray-700 text-gray-300'
                }`}
              >{t.label}</button>
            ))}
          </div>
        </div>
      </nav>

      {/* ── Predictions tab ─────────────────────────────────────────────── */}
      {tab === 'predictions' && (
        <>
          <div className="bg-gray-800 border-b border-gray-700 px-6 py-3">
            <div className="flex gap-2 flex-wrap items-center">
              {TOP_LEAGUES.map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => setCompetition(id)}
                  className={`px-3 py-1.5 rounded font-semibold text-sm transition-colors ${
                    competition === id
                      ? 'bg-green-500 text-white'
                      : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                  }`}
                >
                  {label}
                </button>
              ))}
              <button
                onClick={() => setShowLower(p => !p)}
                className="px-3 py-1.5 rounded text-sm text-gray-400 hover:text-gray-200 border border-gray-600 hover:border-gray-400 transition-colors"
              >
                {showLower ? '▴ Lower Leagues' : '▾ Lower Leagues'}
              </button>
            </div>
            {showLower && (
              <div className="mt-2 pt-2 border-t border-gray-700 space-y-2">
                <div className="flex gap-2 flex-wrap">
                  <span className="text-gray-600 text-xs self-center">2nd tier</span>
                  {LOWER_LEAGUES.map(({ id, label }) => (
                    <button key={id} onClick={() => setCompetition(id)}
                      className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                        competition === id ? 'bg-blue-500 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200'
                      }`}>
                      {label}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2 flex-wrap">
                  <span className="text-gray-600 text-xs self-center">Other EU</span>
                  {OTHER_EUROPEAN.map(({ id, label }) => (
                    <button key={id} onClick={() => setCompetition(id)}
                      className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                        competition === id ? 'bg-purple-500 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200'
                      }`}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          <main className="max-w-6xl mx-auto px-6 py-8">
            <PredictionsTable competition={competition} />
          </main>
        </>
      )}

      {/* ── Accuracy tab ────────────────────────────────────────────────── */}
      {tab === 'accuracy' && (
        <main className="max-w-6xl mx-auto px-6 py-8">
          <AccuracyTracker />
        </main>
      )}

      {/* ── Calibration tab ─────────────────────────────────────────────── */}
      {tab === 'calibration' && (
        <main className="max-w-6xl mx-auto px-6 py-8">
          <CalibrationChart />
        </main>
      )}

      {/* ── Edge / CLV tab ──────────────────────────────────────────────── */}
      {tab === 'edge' && (
        <>
          <div className="bg-gray-800 border-b border-gray-700 px-6 py-3 flex gap-2">
            {EDGE_TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setEdgeTab(t.id)}
                className={`px-4 py-2 rounded font-semibold text-sm transition-colors ${
                  edgeTab === t.id
                    ? 'bg-green-600 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <main className="max-w-6xl mx-auto px-6 py-8">
            {edgeTab === 'bot'      && <BettingBot />}
            {edgeTab === 'clv'      && <CLVChart />}
            {edgeTab === 'bankroll' && <BankrollChart />}
            {edgeTab === 'kelly'    && <KellyCalculator />}
          </main>
        </>
      )}

      {/* ── Tournament tab ──────────────────────────────────────────────── */}
      {tab === 'tournament' && (
        <main className="max-w-6xl mx-auto px-6 py-8">
          <TournamentView />
        </main>
      )}

      {/* ── Tennis tab ──────────────────────────────────────────────────── */}
      {tab === 'tennis' && (
        <main className="max-w-4xl mx-auto px-6 py-8">
          <TennisView />
        </main>
      )}

      {/* ── Basketball tab ──────────────────────────────────────────────── */}
      {tab === 'basketball' && (
        <main className="max-w-4xl mx-auto px-6 py-8">
          <BasketballView />
        </main>
      )}

      {/* Persistent Bet Builder — visible on all tabs */}
      <BetBuilder bankroll={1000} />
    </div>
    </BetSlipProvider>
  );
}

export default App;
