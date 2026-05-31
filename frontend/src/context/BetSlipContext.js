import React, { createContext, useContext, useReducer, useCallback } from 'react';

const BetSlipContext = createContext(null);

const initialState = {
  legs: [],        // [{id, match, market, label, odds, prob, competition, matchDate}]
  mode: 'single',  // 'single' | 'acca'
  open: false,
};

function reducer(state, action) {
  switch (action.type) {
    case 'ADD_LEG': {
      const exists = state.legs.find(l => l.id === action.leg.id);
      if (exists) return state;
      return { ...state, legs: [...state.legs, action.leg], open: true };
    }
    case 'REMOVE_LEG':
      return { ...state, legs: state.legs.filter(l => l.id !== action.id) };
    case 'CLEAR':
      return { ...state, legs: [] };
    case 'SET_MODE':
      return { ...state, mode: action.mode };
    case 'TOGGLE':
      return { ...state, open: !state.open };
    case 'OPEN':
      return { ...state, open: true };
    case 'CLOSE':
      return { ...state, open: false };
    default:
      return state;
  }
}

export function BetSlipProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  const addLeg = useCallback((leg) => dispatch({ type: 'ADD_LEG', leg }), []);
  const removeLeg = useCallback((id) => dispatch({ type: 'REMOVE_LEG', id }), []);
  const clearSlip = useCallback(() => dispatch({ type: 'CLEAR' }), []);
  const setMode = useCallback((mode) => dispatch({ type: 'SET_MODE', mode }), []);
  const toggle = useCallback(() => dispatch({ type: 'TOGGLE' }), []);

  return (
    <BetSlipContext.Provider value={{ ...state, addLeg, removeLeg, clearSlip, setMode, toggle }}>
      {children}
    </BetSlipContext.Provider>
  );
}

export function useBetSlip() {
  const ctx = useContext(BetSlipContext);
  if (!ctx) throw new Error('useBetSlip must be used within BetSlipProvider');
  return ctx;
}
