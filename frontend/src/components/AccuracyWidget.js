import React, { useState, useEffect } from 'react';

export default function AccuracyWidget({ competition }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`${process.env.REACT_APP_API_URL}/api/v1/accuracy/${competition}`)
      .then(r => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [competition]);

  if (loading) return null;
  if (!data || data.resolved === 0) return (
    <div className="text-gray-500 text-xs px-2">No resolved predictions yet</div>
  );

  const pct = v => `${(v * 100).toFixed(1)}%`;

  return (
    <div className="flex gap-4 text-xs text-gray-300 px-2">
      <span className="text-gray-500">{data.resolved} resolved</span>
      <span>Outcome <span className="text-green-400 font-semibold">{pct(data.accuracy.outcome)}</span></span>
      <span>O/U 2.5 <span className="text-blue-400 font-semibold">{pct(data.accuracy.over_2_5)}</span></span>
      <span>BTTS <span className="text-yellow-400 font-semibold">{pct(data.accuracy.btts)}</span></span>
    </div>
  );
}
