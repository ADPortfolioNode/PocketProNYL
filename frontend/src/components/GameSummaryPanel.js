import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';
import getApiBase from '../utils/apiBase';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export default function GameSummaryPanel({ games, refreshKey = 0, initialSummaries = {} }) {
  const [summaries, setSummaries] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const API_BASE = useMemo(() => getApiBase(), []);

  useEffect(() => {
    if (initialSummaries && Object.keys(initialSummaries).length > 0) {
      setSummaries((prev) => ({ ...initialSummaries, ...prev }));
    }
  }, [initialSummaries]);

  useEffect(() => {
    let isMounted = true;
    if (!games || games.length === 0) {
      setSummaries({});
      return () => { isMounted = false; };
    }

    const hasAllFromParent = refreshKey === 0
      && games.every((game) => initialSummaries[game] !== undefined);

    if (hasAllFromParent) {
      setSummaries(initialSummaries);
      return () => { isMounted = false; };
    }

    fetchSummaries(isMounted);
    return () => { isMounted = false; };
  }, [games, API_BASE, refreshKey]);

  const fetchBatchSummaries = async (maxAttempts = 3) => {
    let lastError = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      try {
        const response = await axios.get(`${API_BASE}/api/games/summaries`, { timeout: 20000 });
        const payload = response.data?.summaries || {};
        const counts = {};
        for (const game of games) {
          counts[game] = Number(payload[game]?.draw_count || 0);
        }
        return counts;
      } catch (e) {
        lastError = e;
        if (attempt < maxAttempts) {
          await sleep(attempt * 800);
        }
      }
    }
    throw lastError;
  };

  const fetchSummaries = async (isMounted = true) => {
    if (!isMounted) return;
    setLoading(true);
    setError(null);
    const newSummaries = { ...initialSummaries };

    try {
      const counts = await fetchBatchSummaries();
      if (isMounted) {
        setSummaries({ ...newSummaries, ...counts });
        setLoading(false);
      }
    } catch {
      if (isMounted) {
        const fallback = { ...newSummaries };
        for (const game of games) {
          if (fallback[game] === undefined) fallback[game] = 0;
        }
        setSummaries(fallback);
        setError('Failed to fetch game summaries. Backend may still be warming up.');
        setLoading(false);
      }
    }
  };

  return (
    <div className="card p-3 mb-3">
      <h5>Game Contents</h5>
      {loading && (
        <div className="progress mb-2">
          <div className="progress-bar progress-bar-striped progress-bar-animated w-100" role="progressbar"></div>
        </div>
      )}
      {error && !loading && (
        <div className="alert alert-warning">
          <strong>Warning:</strong> {error}
        </div>
      )}
      {games.length === 0 && !loading && <p>No games available.</p>}
      {!loading && games.length > 0 && (
        <ul className="list-group list-group-flush">
          {games.map(game => (
            <li key={game} className="list-group-item d-flex justify-content-between align-items-center">
              <Link to={`/resources?game=${game}`} className="stretched-link-text">
                {game}
              </Link>
              <span className="badge bg-primary rounded-pill">
                {summaries[game] !== undefined ? `${summaries[game]} draws` : 'Loading...'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}