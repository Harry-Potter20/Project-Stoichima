import React from 'react';
import { useBetSlip } from '../context/BetSlipContext';

export default function AddToSlipBtn({ leg, small = false }) {
  const { addLeg, legs } = useBetSlip();
  const already = legs.some(l => l.id === leg.id);
  return (
    <button
      onClick={() => !already && addLeg(leg)}
      disabled={already}
      title={already ? 'Already in slip' : `Add ${leg.label} to bet slip`}
      className={`rounded font-bold transition-all
        ${already
          ? 'bg-green-700 text-green-300 cursor-default'
          : 'bg-blue-800 text-blue-300 hover:bg-green-600 hover:text-white active:scale-95'
        } ${small ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2 py-1'}`}
    >
      {already ? '✓' : '+'}
    </button>
  );
}
