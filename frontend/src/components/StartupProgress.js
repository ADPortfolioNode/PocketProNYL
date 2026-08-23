import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { getApiBase } from '../utils/apiBase';
import { analyzeError, ErrorCategory } from '../utils/errorUtils';
import { startPolling } from '../utils/polling';
import ErrorMessage from './ErrorMessage';

const KNOWN_GAMES = ['take5', 'pick3', 'powerball', 'megamillions', 'pick10', 'cash4life', 'quickdraw', 'nylotto'];

const StartupProgress = ({ onComplete }) => {
    const [status, setStatus] = useState(null);
    const [errorReport, setErrorReport] = useState(null);
    const [elapsedSeconds, setElapsedSeconds] = useState(0);
    const [isStarting, setIsStarting] = useState(false);
    const [startError, setStartError] = useState(null);
    const transientErrorCountRef = useRef(0);
    const ingestStartedAtRef = useRef(Date.now());

    const getStartupStatus = async () => {
        const apiBase = getApiBase();
        return axios.get(`${apiBase}/api/startup_status`, { timeout: 12000 });
    };

    const postStartupInit = async () => {
        const apiBase = getApiBase();
        return axios.post(`${apiBase}/api/startup_init`, {}, { timeout: 30000 });
    };

    useEffect(() => {
        const timer = setInterval(() => {
            setElapsedSeconds((Date.now() - ingestStartedAtRef.current) / 1000);
        }, 1000);
        return () => clearInterval(timer);
    }, []);

    useEffect(() => {
        const stop = startPolling({
            intervalMs: 2000,
            maxBackoffMs: 15000,
            tick: async () => {
                try {
                    const response = await getStartupStatus();
                    transientErrorCountRef.current = 0;
                    setErrorReport(null);
                    setStatus(response.data);
                    if (response.data?.elapsed_s) {
                        setElapsedSeconds(Number(response.data.elapsed_s));
                    }
                    if (response.data.status === 'completed') {
                        onComplete();
                    }
                    return response.data;
                } catch (error) {
                    const report = analyzeError(error);
                    const statusCode = error?.response?.status;
                    const isTransientGateway = statusCode === 502 || statusCode === 503 || statusCode === 504;
                    const isConnectionIssue = report.category === ErrorCategory.CONNECTION_ERROR;
                    const isAxiosTimeout = /timeout of \d+ms exceeded/i.test(error?.message || '');
                    if (isTransientGateway || isConnectionIssue || isAxiosTimeout) {
                        transientErrorCountRef.current += 1;
                        if (transientErrorCountRef.current >= 12) {
                            setErrorReport(report);
                        }
                        throw error;
                    }
                    setErrorReport(report);
                    throw error;
                }
            },
            shouldStop: (data) => data?.status === 'completed',
        });
        return stop;
    }, [onComplete]);

    const formatTime = (seconds) => {
        const s = Number(seconds) || 0;
        if (s < 60) return `${s.toFixed(0)}s`;
        return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
    };

    const formatGameLabel = (game) => String(game || '')
        .replace(/[_-]+/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase());

    const getStatusIcon = (gameStatus) => {
        if (gameStatus === 'completed') return '✓';
        if (['ingesting', 'running', 'fetching'].includes(gameStatus)) return '↻';
        if (gameStatus === 'queued') return '○';
        if (gameStatus === 'failed' || gameStatus === 'error') return '✗';
        return '○';
    };

    const getStatusTextClass = (gameStatus) => {
        if (gameStatus === 'completed') return 'text-success';
        if (['ingesting', 'running', 'fetching'].includes(gameStatus)) return 'text-warning';
        if (gameStatus === 'failed' || gameStatus === 'error') return 'text-danger';
        return 'text-secondary';
    };

    const availableGames = Array.isArray(status?.available_games) && status.available_games.length
        ? status.available_games
        : KNOWN_GAMES;
    const games = status?.games || {};
    const gameEntries = availableGames.map((game) => {
        const raw = games[game] || {};
        return [game, {
            status: String(raw.status || 'pending').toLowerCase(),
            error: raw.error || null,
            rows_fetched: Number(raw.rows_fetched || 0),
            total_rows: Number(raw.total_rows || 0),
            percent: Number(raw.percent || 0),
        }];
    });

    const progressVal = Number(status?.progress ?? 0);
    const totalVal = Number(status?.total ?? gameEntries.length ?? 8) || 8;
    const rowsFetched = Number(status?.current_game_rows_fetched ?? 0);
    const rowsTotal = Number(status?.current_game_rows_total ?? 0);
    const completedGameCount = gameEntries.filter(([, g]) => g.status === 'completed').length;
    const currentGameFraction = rowsTotal > 0 ? Math.max(0, Math.min(rowsFetched / rowsTotal, 1)) : 0;
    const backendPercent = Number(status?.percent_complete);
    const fallbackPercent = ((completedGameCount + currentGameFraction) / totalVal) * 100;
    const overallProgress = Math.max(
        0,
        Math.min(Number.isFinite(backendPercent) ? backendPercent : fallbackPercent, 100),
    );
    const isIngesting = ['ingesting', 'queued', 'pending'].includes(String(status?.status || 'pending').toLowerCase());
    const isCompleted = status?.status === 'completed';

    const handleStartInitialization = async () => {
        setIsStarting(true);
        setStartError(null);
        try {
            await postStartupInit();
        } catch (error) {
            setStartError(analyzeError(error));
        } finally {
            setIsStarting(false);
        }
    };

    return (
        <div className="container py-4 startup-shell">
            <h2 className="mb-3">Lottery Data Initialization</h2>
            {errorReport && (
                <div className="mb-3">
                    <ErrorMessage errorReport={errorReport} />
                    <p className="small text-muted mb-0">Ingestion may still be running. This bar uses the last known status.</p>
                </div>
            )}
            {startError && (
                <div className="mb-3">
                    <ErrorMessage errorReport={startError} />
                </div>
            )}
            <div className="mb-3 d-flex flex-wrap gap-2">
                {!isCompleted && (
                    <button onClick={handleStartInitialization} disabled={isStarting} className="btn btn-primary">
                        {isStarting ? 'Queuing...' : isIngesting ? 'Re-queue ingestion' : 'Start Initialization'}
                    </button>
                )}
                <button onClick={onComplete} className="btn btn-outline-secondary">
                    Continue to Dashboard
                </button>
            </div>

            <div className={`card startup-status-card mb-4 ${isCompleted ? 'is-complete' : isIngesting ? 'is-active' : ''}`}>
                <div className="card-body">
                    <p className="mb-2">
                        <strong>Status:</strong>
                        {isCompleted && <span className="text-success ms-2">Complete</span>}
                        {isIngesting && !isCompleted && <span className="text-warning ms-2">Downloading game history...</span>}
                    </p>
                    <p className="mb-2">
                        <strong>Currently processing:</strong>{' '}
                        {status?.current_game ? formatGameLabel(status.current_game) : 'waiting for first game...'}
                        {status?.current_task ? ` (${status.current_task})` : ''}
                    </p>
                    <p className="mb-0">
                        <strong>Elapsed:</strong> {formatTime(elapsedSeconds)}
                        {' '}<span className="text-muted">· {completedGameCount}/{totalVal} games done</span>
                    </p>
                </div>
            </div>

            <div className="mb-4">
                <h4 className="mb-2">
                    Download progress: {Math.round(overallProgress)}%
                    <span className="small text-muted"> ({progressVal.toFixed(1)} / {totalVal} games)</span>
                    {rowsFetched > 0 && (
                        <span className="small text-muted">
                            {' '}· {rowsFetched.toLocaleString()}
                            {rowsTotal > 0 ? ` / ${rowsTotal.toLocaleString()}` : ''} rows
                            {status?.current_game ? ` in ${formatGameLabel(status.current_game)}` : ''}
                        </span>
                    )}
                </h4>
                <div className="progress startup-progress-bar" style={{ height: '1.5rem' }}>
                    <div
                        className={`progress-bar ${isCompleted ? 'bg-success' : 'bg-warning progress-bar-striped progress-bar-animated'}`}
                        role="progressbar"
                        style={{ width: `${Math.max(overallProgress, isIngesting && overallProgress < 2 ? 4 : 0)}%` }}
                        aria-valuenow={overallProgress}
                        aria-valuemin="0"
                        aria-valuemax="100"
                    >
                        {Math.round(overallProgress)}%
                    </div>
                </div>
            </div>

            <h4>Game download status</h4>
            <div className="table-responsive mb-4">
                <table className="table table-hover align-middle startup-status-table">
                    <thead>
                        <tr>
                            <th>Game</th>
                            <th className="text-center">Status</th>
                            <th>Rows</th>
                            <th style={{ minWidth: '140px' }}>Progress</th>
                        </tr>
                    </thead>
                    <tbody>
                        {gameEntries.map(([game, gameData]) => {
                            const pct = gameData.status === 'completed'
                                ? 100
                                : (gameData.total_rows > 0 ? gameData.percent : (gameData.status === 'ingesting' ? 8 : 0));
                            return (
                                <tr key={game} className={game === status?.current_game ? 'table-warning' : ''}>
                                    <td className="fw-semibold">{formatGameLabel(game)}</td>
                                    <td className={`text-center ${getStatusTextClass(gameData.status)}`}>
                                        {getStatusIcon(gameData.status)} {gameData.status}
                                    </td>
                                    <td className="small text-muted">
                                        {gameData.rows_fetched > 0
                                            ? `${gameData.rows_fetched.toLocaleString()}${gameData.total_rows ? ` / ${gameData.total_rows.toLocaleString()}` : ''}`
                                            : (gameData.error || '—')}
                                    </td>
                                    <td>
                                        <div className="progress" style={{ height: '0.6rem' }}>
                                            <div
                                                className={`progress-bar ${gameData.status === 'completed' ? 'bg-success' : 'bg-warning'}`}
                                                style={{ width: `${Math.max(0, Math.min(pct, 100))}%` }}
                                            />
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
};

export default StartupProgress;
