import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { getApiBase } from '../utils/apiBase';
import { analyzeError } from '../utils/errorUtils';
import { startPolling } from '../utils/polling';
import CandyStripeProgress from './CandyStripeProgress';
import { deriveStartupView, isBackendDone, isStatusPayload, statusSignature } from '../utils/startupView';

const StartupProgress = ({ onComplete }) => {
    const [status, setStatus] = useState(null);
    const [elapsedSeconds, setElapsedSeconds] = useState(0);
    const [poll, setPoll] = useState({ lastOkAt: null, lastError: null, lastChangeAt: null });
    const [pollEpoch, setPollEpoch] = useState(0);
    const [now, setNow] = useState(Date.now());
    const ingestStartedAtRef = useRef(Date.now());
    const signatureRef = useRef('');
    const completedRef = useRef(false);

    useEffect(() => {
        const timer = setInterval(() => {
            const t = Date.now();
            setNow(t);
            setElapsedSeconds((t - ingestStartedAtRef.current) / 1000);
        }, 1000);
        return () => clearInterval(timer);
    }, []);

    useEffect(() => {
        const stop = startPolling({
            intervalMs: 2500,
            maxBackoffMs: 20000,
            tick: async () => {
                try {
                    const apiBase = getApiBase();
                    const response = await axios.get(`${apiBase}/api/startup_status`, { timeout: 8000 });
                    const data = response.data;
                    if (!isStatusPayload(data)) {
                        // e.g. the dev server answering index.html because /api is not proxied.
                        setPoll((p) => ({ ...p, lastError: 'the API answered with a web page instead of status JSON (check REACT_APP_API_BASE or the /api proxy)' }));
                        return { status: 'polling' };
                    }
                    const t = Date.now();
                    const signature = statusSignature(data);
                    setPoll((p) => ({
                        lastOkAt: t,
                        lastError: null,
                        lastChangeAt: signature !== signatureRef.current || !p.lastChangeAt ? t : p.lastChangeAt,
                    }));
                    signatureRef.current = signature;
                    setStatus(data);
                    if (data.elapsed_s) setElapsedSeconds(Number(data.elapsed_s));
                    if (isBackendDone(data) && !completedRef.current) {
                        completedRef.current = true;
                        onComplete();
                    }
                    return data;
                } catch (error) {
                    setPoll((p) => ({ ...p, lastError: analyzeError(error).originalError || error?.message || 'request failed' }));
                    return { status: 'polling' };
                }
            },
            shouldStop: (data) => isBackendDone(data),
        });
        return stop;
    }, [onComplete, pollEpoch]);

    const view = deriveStartupView({
        status,
        lastOkAt: poll.lastOkAt,
        lastError: poll.lastError,
        lastChangeAt: poll.lastChangeAt,
        startedAt: ingestStartedAtRef.current,
        now,
    });

    const retry = async () => {
        if (view.retry === 'startup_init') {
            try {
                await axios.post(`${getApiBase()}/api/startup_init`, { force: false }, { timeout: 30000 });
            } catch (e) {
                /* polling below reports whether the retry took */
            }
        }
        ingestStartedAtRef.current = Date.now();
        signatureRef.current = '';
        setPoll({ lastOkAt: null, lastError: null, lastChangeAt: null });
        setPollEpoch((n) => n + 1);
    };

    const formatTime = (seconds) => {
        const s = Number(seconds) || 0;
        if (s < 60) return `${s.toFixed(0)}s`;
        return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
    };

    const formatGameLabel = (game) => String(game || '')
        .replace(/[_-]+/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase());

    const games = status?.games || {};
    const availableGames = Array.isArray(status?.available_games) && status.available_games.length
        ? status.available_games
        : Object.keys(games);
    const currentGame = status?.current_game || status?.phase_game || null;
    const gameEntries = availableGames.map((game) => {
        const raw = games[game] || {};
        let st = String(raw.status || 'pending').toLowerCase();
        if (game === currentGame && !['completed', 'ingesting', 'fetching', 'running', 'error'].includes(st)) {
            st = 'ingesting';
        }
        const fetched = Number(raw.phase_rows_done ?? raw.rows_fetched ?? 0);
        let total = Number(raw.phase_rows_total ?? raw.total_rows ?? 0);
        if (fetched > total) total = fetched;
        const percent = total > 0 ? Math.min(100, (fetched / total) * 100) : null;
        return [game, { status: st, error: raw.error || null, rows_fetched: fetched, total_rows: total, percent }];
    });

    const isCompleted = view.mode === 'complete';
    const rowsDone = Number(status?.phase_rows_done ?? status?.current_game_rows_fetched ?? 0);
    const rowsTotal = Number(status?.phase_rows_total ?? status?.current_game_rows_total ?? 0);
    const gamePercent = rowsTotal > 0 ? Math.min(100, (rowsDone / rowsTotal) * 100) : null;
    const barStatus = view.mode === 'error' ? 'error' : (isCompleted ? 'complete' : 'in_progress');
    const headline = {
        connecting: 'Connecting to the backend…',
        ingesting: 'Downloading game history…',
        complete: 'Complete',
        error: 'Needs attention',
    }[view.mode];

    return (
        <div className="container py-4 startup-shell">
            <h2 className="mb-3">Lottery Data Initialization</h2>
            <p className="text-muted">Downloading official draw history. You can open the dashboard anytime; games fill in as they finish.</p>
            <button onClick={onComplete} className="btn btn-outline-secondary mb-4">Continue to Dashboard</button>

            <div className={`card startup-status-card mb-4 ${isCompleted ? 'is-complete' : 'is-active'}`}>
                <div className="card-body">
                    <p className="mb-2">
                        <strong>Status:</strong>{' '}
                        <span className={isCompleted ? 'text-success' : (view.mode === 'error' ? 'text-danger' : 'text-warning')}>{headline}</span>
                    </p>
                    <p className="mb-2">
                        <strong>Currently processing:</strong>{' '}
                        {currentGame ? formatGameLabel(currentGame) : (view.mode === 'connecting' ? 'waiting for the first status update' : '—')}
                    </p>
                    <p className="mb-0">
                        <strong>Elapsed:</strong> {formatTime(elapsedSeconds)}
                        {view.total ? <span className="text-muted"> · {view.gamesDone}/{view.total} games done</span> : null}
                    </p>
                </div>
            </div>

            <div className="mb-4" data-testid="startup-progress">
                <h4 className="mb-2">
                    Download progress{view.percent !== null && !isCompleted ? `: ${Math.round(view.percent)}%` : ''}
                    {view.total ? <span className="small text-muted"> ({view.gamesDone} / {view.total} games)</span> : null}
                </h4>
                <CandyStripeProgress
                    status={barStatus}
                    value={view.percent}
                    size="large"
                    showPercent={false}
                    detail={view.phase}
                    error={view.errorMessage}
                    onRetry={retry}
                    testId="startup-overall"
                />
                {currentGame && view.mode === 'ingesting' && (
                    <div className="mt-3">
                        <CandyStripeProgress
                            status="in_progress"
                            value={gamePercent}
                            size="compact"
                            label={formatGameLabel(currentGame)}
                            detail={rowsDone > 0 ? `${rowsDone.toLocaleString()}${rowsTotal ? ` / ${rowsTotal.toLocaleString()}` : ''} rows` : null}
                            testId="startup-game"
                        />
                    </div>
                )}
            </div>

            <h4>Game download status</h4>
            <div className="table-responsive mb-4">
                <table className="table table-hover align-middle">
                    <thead>
                        <tr>
                            <th>Game</th>
                            <th>Status</th>
                            <th>Rows</th>
                            <th style={{ minWidth: '140px' }}>Progress</th>
                        </tr>
                    </thead>
                    <tbody>
                        {gameEntries.map(([game, gameData]) => (
                            <tr key={game} className={game === currentGame ? 'table-warning' : ''}>
                                <td className="fw-semibold">{formatGameLabel(game)}</td>
                                <td className={gameData.status === 'completed' ? 'text-success' : gameData.status === 'ingesting' ? 'text-warning' : 'text-secondary'}>
                                    {gameData.status}
                                </td>
                                <td className="small text-muted">
                                    {gameData.rows_fetched > 0
                                        ? `${gameData.rows_fetched.toLocaleString()}${gameData.total_rows ? ` / ${gameData.total_rows.toLocaleString()}` : ''}`
                                        : '—'}
                                </td>
                                <td>
                                    <CandyStripeProgress
                                        status={gameData.status === 'completed' ? 'complete' : (gameData.status === 'error' ? 'error' : (['ingesting', 'fetching', 'running', 'queued'].includes(gameData.status) ? 'in_progress' : 'idle'))}
                                        value={gameData.status === 'completed' ? 100 : gameData.percent}
                                        size="compact"
                                        showPercent={false}
                                        error={gameData.error}
                                        testId={`startup-row-${game}`}
                                    />
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
};

export default StartupProgress;
