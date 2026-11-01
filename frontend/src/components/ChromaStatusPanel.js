import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import chromaStateManager from '../utils/chromaStateManager';
import CollectionDetailModal from './CollectionDetailModal';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export default function ChromaStatusPanel() {
    const [collections, setCollections] = useState([]);
    const [meta, setMeta] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState(null);
    const [apiBase, setApiBase] = useState(null);
    const [selectedCollection, setSelectedCollection] = useState(null);
    const inFlightRef = useRef(false);

    const fetchCollectionsWithRetry = async (baseUrl, maxAttempts = 3) => {
        let lastError = null;
        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            try {
                const response = await axios.get(`${baseUrl}/api/chroma/collections`, { timeout: 12000 });
                return response;
            } catch (err) {
                lastError = err;
                if (attempt < maxAttempts) {
                    await sleep(attempt * 700);
                }
            }
        }
        throw lastError;
    };

    const fetchChromaStatus = async (baseUrl, options = { silent: false }) => {
        const { silent } = options;
        if (baseUrl === null || baseUrl === undefined) {
            setError("API base not configured");
            setLoading(false);
            return;
        }

        if (inFlightRef.current) {
            return;
        }

        inFlightRef.current = true;
        if (silent) {
            setRefreshing(true);
        } else {
            setLoading(true);
        }

        try {
            const response = await fetchCollectionsWithRetry(baseUrl);
            const cols = response.data?.collections || [];
            const responseMeta = response.data?.meta || null;
            setCollections(cols);
            setMeta(responseMeta);
            setError(response.data?.error || null);
        } catch (err) {
            console.error("Failed to fetch ChromaDB collections status:", err);
            const errorMsg = err.response?.status === 404
                ? "ChromaDB not responding. Please check backend connection."
                : err.code === 'ECONNABORTED'
                ? "Request timeout. Backend may still be starting up."
                : "Failed to fetch ChromaDB collections status right now.";
            setError(errorMsg);
        } finally {
            inFlightRef.current = false;
            setLoading(false);
            setRefreshing(false);
        }
    };

    // Initialize API base
    useEffect(() => {
        const base = getApiBase();
        setApiBase(base);
        fetchChromaStatus(base, { silent: false });
    }, []);

    // Refresh only when ingestion/upload updates collections
    useEffect(() => {
        if (apiBase === null || apiBase === undefined) return;
        const unsubscribe = chromaStateManager.subscribe(() => {
            fetchChromaStatus(apiBase, { silent: true });
        });
        return () => unsubscribe();
    }, [apiBase]);

    return (
        <div className="card p-3 mb-3 h-100">
            <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="mb-0">ChromaDB Collections Status</h5>
                {refreshing && !loading && !error && (
                    <span className="small text-muted">Refreshing...</span>
                )}
                {error && (
                    <button 
                        onClick={() => fetchChromaStatus(apiBase, { silent: false })}
                        className="btn btn-primary btn-sm"
                    >
                        Retry
                    </button>
                )}
            </div>
            {loading && (
                <div className="text-center">
                    <div className="spinner-border text-primary" role="status">
                        <span className="visually-hidden">Loading...</span>
                    </div>
                    <p>Loading collections...</p>
                </div>
            )}
            {error && collections.length === 0 && (
                <div className="alert alert-danger mb-0">
                    <strong>Error:</strong> {error}
                </div>
            )}
            {error && collections.length > 0 && (
                <div className="alert alert-warning mb-3">
                    <strong>Warning:</strong> {error} Showing last known collections.
                </div>
            )}
            {!loading && !error && collections.length === 0 && (
                <p className="text-muted">No ChromaDB collections found. Run ingestion to populate.</p>
            )}
            {meta && (
                <div className="small text-muted mb-2">
                    Record index: {meta.record_index_basis || 'configured order'}
                </div>
            )}
            {collections.length > 0 && (
                <ul className="list-group list-group-flush">
                    {collections.map((collection, index) => (
                        <li
                            key={`${collection.name}-${index}`}
                            className="list-group-item d-flex justify-content-between align-items-center"
                            role="button"
                            tabIndex={0}
                            title="View collection details"
                            onClick={() => setSelectedCollection(collection)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    setSelectedCollection(collection);
                                }
                            }}
                        >
                            <div className="d-flex flex-column">
                                <span className="fw-bold">{collection.name.toUpperCase()}</span>
                                <small className="text-muted">Index #{collection.record_index || (index + 1)} · Click to view</small>
                            </div>
                            <div className="d-flex align-items-center gap-2">
                                {(collection.state === 'timeout' || collection.state === 'error') && (
                                    <span className="badge bg-warning text-dark rounded-pill">{collection.state}</span>
                                )}
                                <span className="badge bg-info rounded-pill">
                                    {Number(collection.count || 0).toLocaleString()} docs
                                </span>
                            </div>
                        </li>
                    ))}
                </ul>
            )}
            {selectedCollection && (
                <CollectionDetailModal
                    collection={selectedCollection}
                    apiBase={apiBase}
                    onClose={() => setSelectedCollection(null)}
                />
            )}
        </div>
    );
}
