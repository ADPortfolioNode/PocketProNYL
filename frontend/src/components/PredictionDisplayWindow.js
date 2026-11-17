import React from 'react';

export default function PredictionDisplayWindow({ ingestStatus, trainStatus, ingestProgress, trainProgress, experiments, predictions }) {
  return (
    <div className="card p-3 mb-3">
      <h5 className="card-title">Suggestion Display Window</h5>

      {/* Ingestion Steps */}
      <div className="mb-3">
        <h6>Ingestion Results</h6>
        <div className="d-flex justify-content-between">
          <span>Status: {ingestStatus}</span>
          <span className={`badge ${ingestStatus === 'completed' ? 'bg-success' : ingestStatus === 'in progress' ? 'bg-warning' : ingestStatus === 'error' ? 'bg-danger' : 'bg-secondary'}`}>{ingestStatus}</span>
        </div>
        {ingestStatus === 'in progress' && (
          <div className="prediction-progress-wrap mt-2">
            <progress className="prediction-progress" value={ingestProgress} max="100" />
          </div>
        )}
        {ingestStatus === 'completed' && <p className="text-success">Data ingestion completed successfully.</p>}
        {ingestStatus === 'error' && <p className="text-danger">Data ingestion failed. Check logs.</p>}
      </div>

      {/* Training Results */}
      <div className="mb-3">
        <h6>Training Results</h6>
        <div className="d-flex justify-content-between">
          <span>Status: {trainStatus}</span>
          <span className={`badge ${trainStatus === 'completed' ? 'bg-success' : trainStatus === 'in progress' ? 'bg-warning' : trainStatus === 'error' ? 'bg-danger' : 'bg-secondary'}`}>{trainStatus}</span>
        </div>
        {trainStatus === 'in progress' && (
          <div className="prediction-progress-wrap mt-2">
            <progress className="prediction-progress" value={trainProgress} max="100" />
          </div>
        )}
        {trainStatus === 'completed' && (
          <div>
            <p className="text-success">Model training completed.</p>
            <ul>
              {experiments.map((exp, index) => (
                <li key={index}>Experiment {index + 1}: {JSON.stringify(exp)}</li>
              ))}
            </ul>
          </div>
        )}
        {trainStatus === 'error' && <p className="text-danger">Model training failed. Check logs.</p>}
      </div>

      {/* Rendering of Suggestions */}
      <div className="mb-3">
        <h6>Suggestions</h6>
        {predictions && Object.keys(predictions).length > 0 ? (
          Object.entries(predictions).map(([game, preds]) => (
            <div key={game} className="mb-2">
              <h6 className="text-capitalize">{game}</h6>
              <ul className="list-group">
                {preds.map((pred, index) => (
                  <li key={index} className="list-group-item">
                    <strong>Suggestion {index + 1}:</strong> {JSON.stringify(pred)}
                  </li>
                ))}
              </ul>
            </div>
          ))
        ) : (
          <p>No suggestions available. Complete ingestion and training first.</p>
        )}
      </div>
    </div>
  );
}
