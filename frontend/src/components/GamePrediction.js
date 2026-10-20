import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import { hasSuggestionResponse, isPredictionForGame } from '../utils/suggestionUtils';
import PredictionDisplay from './PredictionDisplay';
import './PredictionDisplay.css';

export default function GamePrediction({ game }) {
  const [recentK, setRecentK] = useState(10);
  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // The game this card currently shows. A reply for an earlier game must never
  // land under a different game's heading.
  const activeGameRef = useRef(game);

  useEffect(() => {
    activeGameRef.current = game;
    setPrediction(null);
    setError(null);
    setLoading(false);
  }, [game]);

  const submit = async () => {
    const requestedGame = game;
    setLoading(true);
    setError(null);
    setPrediction(null);
    try {
      const apiBase = getApiBase();
      const r = await axios.post(`${apiBase}/api/predict`, {
        game: game,
        recent_k: parseInt(recentK, 10)
      }, { timeout: 600000 });
      const payload = r.data || {};
      if (activeGameRef.current !== requestedGame) {
        return;
      }
      if (!isPredictionForGame(payload, requestedGame)) {
        setError(`Suggestion response was for ${payload.game}, not ${requestedGame}.`);
        return;
      }
      if (payload.status === 'error') {
        setError(payload.message || 'Suggestion failed');
        return;
      }
      if (!hasSuggestionResponse(payload)) {
        setError('Suggestion response did not include suggested numbers or a system message.');
        return;
      }
      setPrediction(payload);
    } catch (e) {
      if (activeGameRef.current !== requestedGame) return;
      setError(e.response?.data?.detail || e.message || 'Suggestion failed');
    } finally {
      if (activeGameRef.current === requestedGame) setLoading(false);
    }
  };

  return (
    <div className="card p-3 mb-3">
      <h5>Suggestion for {game}</h5>
      <p>Enter the number of recent draws to consider for suggestions (e.g., 10)</p>
      <div className="input-group mb-2">
        <input type="number" className="form-control" value={recentK} onChange={e => setRecentK(e.target.value)} disabled={loading} min="1" />
        <button className="btn btn-success" onClick={submit} disabled={loading}>
          {loading ? 'Loading...' : 'Suggest'}
        </button>
      </div>
      {loading && (
        <div className="progress">
          <div className="progress-bar progress-bar-striped progress-bar-animated w-100" role="progressbar"></div>
        </div>
      )}
      {error && !loading && (
        <div className="alert alert-danger">
          <strong>Error:</strong> {error}
        </div>
      )}
      {prediction && !loading && !error && (
        prediction.error ? (
          <div className="alert alert-danger">
            <strong>Error:</strong> {prediction.error}
          </div>
        ) : (
          <PredictionDisplay prediction={prediction} />
        )
      )}
    </div>
  );
}
