import React, { useEffect, useState } from 'react';
import axios from 'axios';

// Modal popup for inspecting one ChromaDB collection from the collections
// list. Shows the collection's own details (name, document count, state,
// record index) plus a sample of its contents: the most recent draws for
// that game, fetched from the existing draws endpoint. The modal closes
// back to the list; it never edits or deletes anything.
export default function CollectionDetailModal({ collection, apiBase, onClose }) {
  const [draws, setDraws] = useState([]);
  const [loadingDraws, setLoadingDraws] = useState(true);
  const [drawsError, setDrawsError] = useState(null);

  const name = collection?.name || '';
  const gameKey = String(name).toLowerCase();

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    if (!apiBase || !gameKey) {
      setLoadingDraws(false);
      return () => { cancelled = true; };
    }
    setLoadingDraws(true);
    setDrawsError(null);
    axios.get(`${apiBase}/api/games/${gameKey}/draws?limit=5`, { timeout: 20000 })
      .then((response) => {
        if (!cancelled) {
          setDraws(Array.isArray(response.data?.draws) ? response.data.draws : []);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setDrawsError(error?.response?.data?.detail || error.message || 'Failed to load sample draws.');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingDraws(false);
        }
      });
    return () => { cancelled = true; };
  }, [apiBase, gameKey]);

  return (
    <div
      className="collection-detail-modal-backdrop"
      role="presentation"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1050,
        padding: '1rem',
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Collection details for ${name}`}
        className="card p-3"
        onClick={(event) => event.stopPropagation()}
        style={{ maxWidth: '640px', width: '100%', maxHeight: '85vh', overflowY: 'auto' }}
      >
        <div className="d-flex justify-content-between align-items-start mb-2">
          <h5 className="mb-0">Collection: {String(name).toUpperCase()}</h5>
          <button type="button" className="btn btn-sm btn-outline-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <ul className="list-unstyled small mb-3">
          <li><strong>Documents:</strong> {Number(collection?.count || 0).toLocaleString()}</li>
          <li><strong>State:</strong> {collection?.state || 'unknown'}</li>
          {collection?.record_index != null && (
            <li><strong>Record index:</strong> {collection.record_index}</li>
          )}
        </ul>
        <h6>Sample contents — most recent draws</h6>
        {loadingDraws && <p className="text-muted small mb-0">Loading sample draws…</p>}
        {!loadingDraws && drawsError && (
          <div className="alert alert-warning py-1 px-2 small mb-0">{drawsError}</div>
        )}
        {!loadingDraws && !drawsError && draws.length === 0 && (
          <p className="text-muted small mb-0">No draws ingested yet for this collection.</p>
        )}
        {!loadingDraws && !drawsError && draws.length > 0 && (
          <table className="table table-sm table-dark table-striped mb-0">
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
    </div>
  );
}
