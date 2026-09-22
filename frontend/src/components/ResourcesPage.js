import React, { useEffect, useMemo, useState, useCallback } from 'react';
import axios from 'axios';
import { useSearchParams } from 'react-router-dom';
import getApiBase from '../utils/apiBase';
import IngestionProgressPanel from './IngestionProgressPanel';

const RESOURCE_LINKS = [
  { key: 'official_site', label: 'NY Lottery Official Site' },
  { key: 'dataset_landing', label: 'NY Open Data Dataset Page' },
  { key: 'dataset_json', label: 'Open Data JSON API' },
];

export default function ResourcesPage() {
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const focusedGame = searchParams.get('game') || '';
  const API_BASE = useMemo(() => getApiBase(), []);

  const [drawsByGame, setDrawsByGame] = useState({});
  const [drawsLoading, setDrawsLoading] = useState({});
  const [drawsError, setDrawsError] = useState({});
  const [expandedDraws, setExpandedDraws] = useState({});
  const [ingestingGame, setIngestingGame] = useState(null);
  const [ingestErrorByGame, setIngestErrorByGame] = useState({});

  const loadCatalog = useCallback(() => (
    axios.get(`${API_BASE}/api/games`, { timeout: 15000 })
      .then((r) => {
        setCatalog(Array.isArray(r.data?.catalog) ? r.data.catalog : []);
        setError(null);
      })
      .catch((e) => {
        setError(e?.response?.data?.detail || e.message || 'Failed to load resources.');
      })
      .finally(() => setLoading(false))
  ), [API_BASE]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    if (!focusedGame) return;
    const el = document.getElementById(`resource-${focusedGame}`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [focusedGame, catalog]);

  const fetchDraws = useCallback((game) => {
    setDrawsLoading((prev) => ({ ...prev, [game]: true }));
    setDrawsError((prev) => ({ ...prev, [game]: null }));
    return axios.get(`${API_BASE}/api/games/${game}/draws?limit=15`, { timeout: 20000 })
      .then((r) => {
        setDrawsByGame((prev) => ({ ...prev, [game]: r.data?.draws || [] }));
      })
      .catch((e) => {
        setDrawsError((prev) => ({
          ...prev,
          [game]: e?.response?.data?.detail || e.message || 'Failed to load draws.',
        }));
      })
      .finally(() => setDrawsLoading((prev) => ({ ...prev, [game]: false })));
  }, [API_BASE]);

  const toggleDraws = (game) => {
    const willExpand = !expandedDraws[game];
    setExpandedDraws((prev) => ({ ...prev, [game]: willExpand }));
    if (willExpand && !drawsByGame[game]) {
      fetchDraws(game);
    }
  };

  const startIngest = async (game) => {
    setIngestingGame(game);
    setIngestErrorByGame((prev) => ({ ...prev, [game]: null }));
    try {
      const response = await axios.post(`${API_BASE}/api/ingest`, { game });
      const status = String(response?.data?.status || '').toLowerCase();
      if (status !== 'completed' && status !== 'success') {
        setIngestErrorByGame((prev) => ({
          ...prev,
          [game]: response?.data?.message || `Ingestion failed for ${game.toUpperCase()}.`,
        }));
        setIngestingGame(null);
      }
      // On success, IngestionProgressPanel's onComplete handles cleanup + refresh.
    } catch (e) {
      setIngestErrorByGame((prev) => ({
        ...prev,
        [game]: e?.response?.data?.message || e.message || 'Failed to start ingestion.',
      }));
      setIngestingGame(null);
    }
  };

  const handleIngestComplete = (game) => (progress) => {
    setIngestingGame(null);
    if (progress?.status === 'error') {
      setIngestErrorByGame((prev) => ({ ...prev, [game]: progress?.error || 'Ingestion failed.' }));
      return;
    }
    setExpandedDraws((prev) => ({ ...prev, [game]: true }));
    fetchDraws(game);
    loadCatalog();
  };

  return (
    <div className="resources-page">
      <h1>Program Resources</h1>
      <p className="lead">
        Official rules, how-to-play guides, and results pages for every NY Lottery program tracked by PocketPro.
        Digest a program's dataset here and preview the most recent draws that were ingested.
      </p>
      {loading && <p className="text-muted">Loading resources…</p>}
      {error && !loading && <div className="alert alert-warning">{error}</div>}
      {!loading && !error && (
        <div className="resources-grid">
          {catalog.map((entry) => {
            const key = entry.key || entry.id;
            const resources = entry.resources || {};
            const isFocused = focusedGame === key;
            const isIngesting = ingestingGame === key;
            const draws = drawsByGame[key] || [];
            const isExpanded = Boolean(expandedDraws[key]);

            return (
              <div
                key={key}
                id={`resource-${key}`}
                className={`card p-3 resource-card${isFocused ? ' border-neon' : ''}`}
              >
                <div
                  className="resource-card-clickable"
                  role="button"
                  tabIndex={0}
                  onClick={() => setSearchParams({ game: key })}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setSearchParams({ game: key });
                    }
                  }}
                >
                  <div className="resource-card-header">
                    <h5 className="text-neon">{entry.title || entry.name || key}</h5>
                    <span className="badge bg-primary rounded-pill">
                      {Number(entry.draw_count || 0).toLocaleString()} draws
                    </span>
                  </div>
                  <div className="resource-card-meta">
                    {(entry.aliases || []).join(', ') || key}
                  </div>
                  <div className="resource-links">
                    {RESOURCE_LINKS.map(({ key: linkKey, label }) => (
                      resources[linkKey] ? (
                        <a
                          key={linkKey}
                          href={resources[linkKey]}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {label} ↗
                        </a>
                      ) : null
                    ))}
                    {(resources.dataset_endpoints || []).length > 0 && (
                      <a
                        href={resources.dataset_endpoints[0]}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                      >
                        Open Data Dataset ↗
                      </a>
                    )}
                  </div>
                </div>

                <div className="resource-card-actions">
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-light"
                    disabled={isIngesting}
                    onClick={() => startIngest(key)}
                  >
                    {isIngesting ? 'Digesting…' : 'Digest Dataset'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-secondary"
                    onClick={() => toggleDraws(key)}
                  >
                    {isExpanded ? 'Hide Recent Draws' : 'View Recent Draws'}
                  </button>
                </div>

                {ingestErrorByGame[key] && (
                  <div className="alert alert-danger mt-2 mb-0 py-1 px-2 small">
                    {ingestErrorByGame[key]}
                  </div>
                )}

                <IngestionProgressPanel
                  game={key}
                  isActive={isIngesting}
                  onComplete={handleIngestComplete(key)}
                />

                {isExpanded && (
                  <div className="resource-draws mt-3">
                    {drawsLoading[key] && <p className="text-muted small mb-0">Loading recent draws…</p>}
                    {drawsError[key] && !drawsLoading[key] && (
                      <div className="alert alert-warning py-1 px-2 small mb-0">{drawsError[key]}</div>
                    )}
                    {!drawsLoading[key] && !drawsError[key] && draws.length === 0 && (
                      <p className="text-muted small mb-0">No draws ingested yet. Click "Digest Dataset" to pull data.</p>
                    )}
                    {!drawsLoading[key] && draws.length > 0 && (
                      <table className="table table-sm table-dark table-striped mb-0 resource-draws-table">
                        <thead>
                          <tr>
                            <th>Draw Date</th>
                            <th>Session</th>
                            <th>Winning Numbers</th>
                          </tr>
                        </thead>
                        <tbody>
                          {draws.map((draw, index) => (
                            <tr key={`${draw.draw_date || 'draw'}-${index}`}>
                              <td>{draw.draw_date || 'n/a'}</td>
                              <td>{draw.draw_session || '—'}</td>
                              <td>{draw.winning_numbers || 'n/a'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
