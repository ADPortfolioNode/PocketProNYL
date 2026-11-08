import React, { useState, useEffect } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import { startPolling } from '../utils/polling';
import ProgressiveProgressBar from './ProgressiveProgressBar';
import WorkflowChart from './WorkflowChart';

/**
 * IngestionProgressPanel
 * Shows real-time progress during manual ingestion
 * Polls the /api/ingest_progress endpoint
 */
export default function IngestionProgressPanel({ game, isActive, onComplete }) {
    const [progress, setProgress] = useState(null);
    const [error, setError] = useState(null);
    const [startTime, setStartTime] = useState(null);
    const [streamMode, setStreamMode] = useState('unknown'); // 'stream' | 'polling'
    const apiBase = getApiBase();

    useEffect(() => {
        if (!isActive || !game || apiBase === null || apiBase === undefined) {
            setProgress(null);
            setStartTime(null);
            return;
        }

        // Set start time when ingestion begins
        if (!startTime) {
            setStartTime(Date.now());
        }

        const base = apiBase === null || apiBase === undefined ? '' : apiBase;
        const streamUrl = `${base}/api/ingest_stream?game=${encodeURIComponent(game)}`;

        let eventSource;
        let pollInterval;

        const handleStreamData = (data) => {
            if (!data) return;
            // If the stream sends a map for all games, extract the per-game payload
            const payload = (data && data.status) ? data : (data[game] || data);
            if (!payload) return;
            setProgress(payload);
            setError(null);
            if (payload.status === 'completed' || payload.status === 'error') {
                onComplete?.(payload);
            }
        };

        if (typeof EventSource !== 'undefined') {
            try {
                eventSource = new EventSource(streamUrl);
                setStreamMode('stream');
                const onStreamEvent = (e) => {
                    try {
                        const parsed = JSON.parse(e.data);
                        handleStreamData(parsed);
                    } catch (err) {
                        console.error('Failed to parse SSE payload', err);
                    }
                };
                eventSource.onmessage = onStreamEvent;
                eventSource.addEventListener('progress', onStreamEvent);
                eventSource.addEventListener('complete', onStreamEvent);
                eventSource.onerror = (err) => {
                    console.error('SSE error', err);
                    setError('Stream error');
                    setStreamMode('polling');
                    if (eventSource) {
                        try { eventSource.close(); } catch (e) {}
                    }
                    // Fallback to polling
                    const pollProgress = async () => {
                        try {
                            const response = await axios.get(`${apiBase}/api/ingest_progress?game=${game}`);
                            handleStreamData(response.data);
                        } catch (err) {
                            console.error('Failed to fetch ingestion progress:', err);
                            setError('Failed to fetch progress');
                        }
                    };
                    pollProgress();
                    pollInterval = startPolling({ intervalMs: 5000, tick: pollProgress });
                };
            } catch (e) {
                // If EventSource construction fails, fallback to polling
                console.error('EventSource not available, falling back to polling', e);
            }
        }

        // If EventSource didn't start, use polling as a fallback
        if (!eventSource) {
            setStreamMode('polling');
            const pollProgress = async () => {
                try {
                    const response = await axios.get(`${apiBase}/api/ingest_progress?game=${game}`);
                    handleStreamData(response.data);
                } catch (err) {
                    console.error('Failed to fetch ingestion progress:', err);
                    setError('Failed to fetch progress');
                }
            };
            pollProgress();
            pollInterval = startPolling({ intervalMs: 5000, tick: pollProgress });
        }

        return () => {
            try { if (eventSource) eventSource.close(); } catch (e) {}
            try { if (pollInterval) pollInterval(); } catch (e) {}
        };
    }, [isActive, game, apiBase, onComplete, startTime]);

    if (!isActive || !progress) {
        return null;
    }

    const rowsFetched = progress.rows_fetched || 0;
    const totalRows = progress.total_rows || 1;
    const status = progress.status === 'completed' ? 'completed' 
                 : progress.status === 'error' ? 'error' 
                 : 'active';
    const panelStatusClass = status === 'error' ? 'is-error' : status === 'completed' ? 'is-complete' : 'is-active';

    return (
        <div className={`ingestion-progress-panel mt-3 ${panelStatusClass}`}>
            <h6 className="ingestion-progress-title">
                🎰 {game.toUpperCase()} Ingestion
                <span style={{marginLeft:8,fontSize:12,opacity:0.8}} className="badge bg-secondary ms-2">{streamMode === 'stream' ? 'Stream' : streamMode === 'polling' ? 'Polling' : '—'}</span>
            </h6>
            <WorkflowChart
              flow="ingest"
              game={game}
              signal={rowsFetched}
              active={status === 'active'}
              refreshMs={5000}
              title="Live ingestion · matplotlib"
              testId="workflow-chart-ingest"
            />

            {error && (
                <div className="alert alert-danger ingestion-progress-alert">
                    <strong>Error:</strong> {error}
                </div>
            )}

            {progress.error && (
                <div className="alert alert-danger ingestion-progress-alert">
                    <strong>Ingestion Failed:</strong> {progress.error}
                </div>
            )}

            {!progress.error && (
                <ProgressiveProgressBar
                    current={rowsFetched}
                    total={totalRows}
                    status={status}
                    label={`Fetching ${game} data`}
                    showMetadata={true}
                    startTime={startTime}
                    colorScheme="primary"
                />
            )}
        </div>
    );
}
