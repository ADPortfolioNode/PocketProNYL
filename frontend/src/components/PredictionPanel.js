import React, { useState } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import GamePrediction from './GamePrediction';
import AllPredictionsPanel from './AllPredictionsPanel';

const ALL_GAMES_VALUE = '__all_games__';

export default function PredictionPanel({ games = [], disabled = false }) {
  const [selectedGame, setSelectedGame] = useState(games.length > 0 ? games[0] : '');
  const [recentK, setRecentK] = useState(10);
  const [loadingAll, setLoadingAll] = useState(false);
  const [allPredictions, setAllPredictions] = useState([]);
  const [allError, setAllError] = useState('');

  React.useEffect(() => {
    if (games.length > 0) {
      setSelectedGame(games[0]);
    }
  }, [games]);

  const normalizePredictionEntry = (game, payload) => {
    if (payload?.status === 'error' || payload?.error) {
      return {
        game,
        status: 'error',
        message: payload?.message || payload?.error || 'Suggestion failed.',
        prediction: null,
      };
    }

    return {
      game,
      status: 'success',
      message: '',
      prediction: payload,
    };
  };

  const predictAllGamesSequentially = async (apiBase) => {
    const normalized = [];
    for (const game of games) {
      try {
        const response = await axios.post(`${apiBase}/api/predict`, {
          game,
          recent_k: parseInt(recentK, 10),
        }, { timeout: 600000 });
        normalized.push(normalizePredictionEntry(game, response.data || {}));
      } catch (error) {
        normalized.push({
          game,
          status: 'error',
          message: error?.response?.data?.detail
            || error?.response?.data?.message
            || error?.message
            || 'Suggestion request failed.',
          prediction: null,
        });
      }
    }
    return normalized;
  };

  const predictAllGames = async () => {
    if (!games.length) return;

    setLoadingAll(true);
    setAllError('');
    setAllPredictions([]);

    try {
      const apiBase = getApiBase();
      let normalized = [];

      try {
        const response = await axios.post(`${apiBase}/api/predict_all`, {
          games,
          recent_k: parseInt(recentK, 10),
        }, { timeout: 900000 });

        const payload = response.data || {};
        if (Array.isArray(payload.results) && payload.results.length > 0) {
          normalized = payload.results.map((item) => ({
            game: item.game,
            status: item.status === 'success' ? 'success' : 'error',
            message: item.message || '',
            prediction: item.prediction || null,
          }));
        } else if (payload.status === 'error') {
          throw new Error(payload.message || 'Batch suggestion request failed.');
        }
      } catch (batchError) {
        const status = batchError?.response?.status;
        if (status === 404 || status === 405) {
          normalized = await predictAllGamesSequentially(apiBase);
        } else {
          throw batchError;
        }
      }

      setAllPredictions(normalized);

      const failedCount = normalized.filter((item) => item.status === 'error').length;
      if (failedCount === normalized.length) {
        setAllError('All game suggestions failed. Check backend status and trained models.');
      } else if (failedCount > 0) {
        setAllError(`${failedCount} game suggestion(s) failed. Showing successful results.`);
      }
    } catch (error) {
      setAllError(error?.response?.data?.message || error?.message || 'Failed to run all-game suggestions.');
    } finally {
      setLoadingAll(false);
    }
  };

  return (
    <>
      {disabled ? (
        <div className="alert alert-warning mb-0">
          <strong>Suggestion unavailable:</strong> Load game draw history first (run ingestion), then try again.
        </div>
      ) : games.length > 0 ? (
        <>
          <select className="form-select mb-3" value={selectedGame} onChange={e => setSelectedGame(e.target.value)}>
            <option value={ALL_GAMES_VALUE}>All Games</option>
            {games.map(game => (
              <option key={game} value={game}>{game}</option>
            ))}
          </select>

          {selectedGame === ALL_GAMES_VALUE ? (
            <div className="card p-3 mb-3">
              <h5>Suggestions for All Games</h5>
              <p>Runs one batch suggestion request for every game (processed sequentially on the server).</p>
              <div className="input-group mb-2">
                <input
                  type="number"
                  className="form-control"
                  value={recentK}
                  onChange={(e) => setRecentK(e.target.value)}
                  disabled={loadingAll}
                  min="1"
                />
                <button className="btn btn-success" onClick={predictAllGames} disabled={loadingAll}>
                  {loadingAll ? 'Running...' : 'Suggest All Games'}
                </button>
              </div>

              {loadingAll && (
                <div className="progress">
                  <div className="progress-bar progress-bar-striped progress-bar-animated w-100" role="progressbar"></div>
                </div>
              )}

              {allError && !loadingAll && (
                <div className="alert alert-warning mt-3 mb-0">
                  <strong>Notice:</strong> {allError}
                </div>
              )}
            </div>
          ) : (
            selectedGame && <GamePrediction game={selectedGame} />
          )}

          {selectedGame === ALL_GAMES_VALUE && allPredictions.length > 0 && (
            <AllPredictionsPanel predictions={allPredictions} />
          )}
        </>
      ) : (
        <p>No games available for suggestions.</p>
      )}
    </>
  );
}
