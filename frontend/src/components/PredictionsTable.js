import React, { useState, useEffect } from 'react';

function PredictionsTable({ competition }) {

  const [predictions, setPredictions] = useState([]);
  const [loading, setLoading] = useState(true);
  
  useEffect(() => {
    const fetchData = async () => {
        try {
          const response = await fetch(`${process.env.REACT_APP_API_URL}/api/v1/predictions/${competition}`);
          const data = await response.json();
          setPredictions(data.predictions);
        } catch (error) {
          console.error("Error fetching predictions:", error);
        } finally {
          setLoading(false);
        }
    };
    fetchData();
  }, [competition]);

  const formatDate = (dateStr) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-GB', {
        weekday: 'short',
        day: 'numeric', 
        month: 'short',
        hour: '2-digit',
        minute: '2-digit'
    });
};

const getOutcomeBadge = (outcome) => {
  switch (outcome) {
    case 'H':
      return <span className="bg-green-500 text-white px-2 py-1 rounded">Home Win</span>;
    case 'A':
      return <span className="bg-red-500 text-white px-2 py-1 rounded">Away Win</span>;
    case 'D':
      return <span className="bg-yellow-500 text-white px-2 py-1 rounded">Draw</span>;
    default:
      return null;
  }
};

  if (loading) {
    return <div>Loading...</div>;
  }

return (
    <div className="bg-gray-800 rounded-lg overflow-hidden">
        <table className="w-full">
            <thead className="bg-green-800 text-white">
                <tr className="border-t border-gray-700">
                    <th className="px-4 py-3 text-left">Home Team</th>
                    <th className="px-4 py-3 text-left">Away Team</th>
                    <th className="px-4 py-3 text-left">Match Date</th>
                    <th className="px-4 py-3 text-left">Predicted Outcome</th>
                    <th className="px-4 py-3 text-left">Predicted Over 2.5 Goals</th>
                </tr>
            </thead>
            <tbody>
                {predictions.map((prediction, index) => (
                    <tr key={index} className={`border-t border-gray-700 hover:bg-gray-700 ${index % 2 === 0 ? 'bg-gray-800' : 'bg-gray-750'}`}>
                        <td className="px-4 py-3 text-left">{prediction.home_team}</td>
                        <td className="px-4 py-3 text-left">{prediction.away_team}</td>
                        <td className="px-4 py-3 text-left">{formatDate(prediction.match_date)}</td>
                        <td className="px-4 py-3 text-left">{getOutcomeBadge(prediction.predicted_outcome)}</td>
                        <td className="px-4 py-3 text-left">{prediction.predicted_over_2_5 ? 'Yes' : 'No'}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    </div>
);
}

export default PredictionsTable;
