import React from 'react';
import PredictionDisplay from './PredictionDisplay';
import { isPredictionForGame } from '../utils/suggestionUtils';

export default function AllPredictionsPanel({ predictions }) {
  if (!predictions || predictions.length === 0) {
    return null;
  }

  return (
    <div className="card p-3 mb-3">
      <h5>All Suggestions</h5>
      {predictions.map((item) => {
        const mismatched = Boolean(item.prediction) && !isPredictionForGame(item.prediction, item.game);
        const prediction = item.prediction && !mismatched
          ? { ...item.prediction, game: item.game }
          : null;

        return (
          <div key={item.game} className="mb-3">
            <h6 className="mb-1">{item.game.toUpperCase()}</h6>
            {item.status === 'error' || mismatched ? (
              <div className="alert alert-danger mb-0">
                <strong>Error:</strong> {mismatched
                  ? `Suggestion response was for ${item.prediction.game}, not ${item.game}.`
                  : (item.message || 'Suggestion failed.')}
              </div>
            ) : (
              <PredictionDisplay prediction={prediction} />
            )}
          </div>
        );
      })}
    </div>
  );
}
