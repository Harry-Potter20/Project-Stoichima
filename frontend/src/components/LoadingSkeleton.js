import React from 'react';

// Reusable loading skeletons — replaces plain "Loading…" text strings.
// Keep variants minimal; add new ones as needed.

export function CardSkeleton({ rows = 5 }) {
  return (
    <div className="space-y-2 animate-pulse">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="bg-gray-800 rounded-lg p-3 border border-gray-700">
          <div className="flex justify-between mb-2">
            <div className="h-4 bg-gray-700 rounded w-1/3" />
            <div className="h-3 bg-gray-700 rounded w-20" />
          </div>
          <div className="h-3 bg-gray-700 rounded w-full mb-1" />
          <div className="h-3 bg-gray-700 rounded w-2/3" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 5 }) {
  return (
    <div className="bg-gray-800 rounded-lg overflow-hidden animate-pulse">
      <div className="bg-gray-700 h-10 border-b border-gray-700" />
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex border-t border-gray-700 px-4 py-3 gap-4">
          {Array.from({ length: cols }).map((_, j) => (
            <div key={j} className="h-4 bg-gray-700 rounded flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ChartSkeleton({ height = 320 }) {
  return (
    <div className="bg-gray-800 rounded-lg p-5 animate-pulse">
      <div className="h-4 bg-gray-700 rounded w-1/3 mb-2" />
      <div className="h-3 bg-gray-700 rounded w-1/4 mb-4" />
      <div className="bg-gray-700 rounded" style={{ height }} />
    </div>
  );
}

const skeletons = { CardSkeleton, TableSkeleton, ChartSkeleton };
export default skeletons;
