import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { getApiBase } from '../utils/apiBase';
import { analyzeError, ErrorCategory } from '../utils/errorUtils';
import { startPolling } from '../utils/polling';
import ErrorMessage from './ErrorMessage';

const StartupProgress = ({ onComplete }) => {
    const [status, setStatus] = useState(null);
    const [errorReport, setErrorReport] = useState(null);
    const [elapsedSeconds, setElapsedSeconds] = useState(0);
    const [isStarting, setIsStarting] = useState(false);
    const [startError, setStartError] = useState(null);
    const transientErrorCountRef = useRef(0);

    const getStartupStatus = async () => {
        const apiBase = getApiBase();
        return axios.get(`${apiBase}/api/startup_status`, { timeout: 660000 }); // Increased timeout to 11 minutes (660s)
    };

    const postStartupInit = async () => {
        const apiBase = getApiBase();
        return axios.post(`${apiBase}/api/startup_init`, {}, { timeout: 30000 });
    };

    useEffect(() => {
        const stop = startPolling({
            intervalMs: 5000,
            maxBackoffMs: 60000,
            tick: async () => {
                try {
                    const response = await getStartupStatus();
                    transientErrorCountRef.current = 0;
                    setErrorReport(null);
                    setStatus(response.data);
                    setElapsedSeconds(response.data.elapsed_s || 0);
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
                        if (transientErrorCountRef.current >= 8) {
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

    if (errorReport) {
        return (
            <div className="container py-4">
                <ErrorMessage errorReport={errorReport} />
            </div>
        );
    }

    if (!status || status.status === 'pending') {
        return <div className="container py-4">Initializing...</div>;
    }

    const availableGames = Array.isArray(status.available_games) ? status.available_games : [];
    const games = status.games || {};
    const normalizedGames = availableGames.length > 0
        ? availableGames.reduce((acc, game) => {
            acc[game] = {
                status: games?.[game]?.status || 'pending',
                error: games?.[game]?.error || null,
            };
            return acc;
        }, {})
        : games;
    const gameEntries = Object.entries(normalizedGames);
    const progressVal = Number(status.progress ?? 0);
    // Fall back to the number of games if backend omits total
    const totalVal = Number(status.total ?? gameEntries.length ?? 0);
    const rowsFetched = Number(status.current_game_rows_fetched ?? 0);
    const rowsTotal = Number(status.current_game_rows_total ?? 0);
    const completedGameCount = gameEntries.filter(([, gameData]) => {
        const gameStatus = String(gameData?.status || '').toLowerCase();
        return gameStatus === 'completed' || gameStatus === 'failed';
    }).length;
    const currentGameFraction = rowsTotal > 0 ? Math.max(0, Math.min(rowsFetched / rowsTotal, 1)) : 0;
    const fallbackOverallProgress = gameEntries.length > 0
        ? ((completedGameCount + currentGameFraction) / gameEntries.length) * 100
        : 0;
    const backendOverallProgress = totalVal > 0 ? (progressVal / totalVal) * 100 : fallbackOverallProgress;
    const overallProgress = Math.max(0, Math.min(backendOverallProgress, 100));
    const isIngesting = status.status === 'ingesting';
    const isCompleted = status.status === 'completed';
    const hasGamesConfigured = gameEntries.length > 0;
    
    const formatTime = (seconds) => {
        if (seconds < 60) return `${seconds.toFixed(1)}s`;
        return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
    };

    const getStatusIcon = (gameStatus) => {
        if (gameStatus === 'completed') return '✓';
        if (gameStatus === 'ingesting') return '↻';
        if (gameStatus === 'failed') return '✗';
        return '◯';
    };

    const getStatusTextClass = (gameStatus) => {
        if (gameStatus === 'completed') return 'text-success';
        if (gameStatus === 'ingesting') return 'text-warning';
        if (gameStatus === 'failed') return 'text-danger';
        return 'text-secondary';
    };

    const getGameStatusClass = (gameStatus) => {
        if (gameStatus === 'completed') return 'status-completed';
        if (gameStatus === 'ingesting') return 'status-ingesting';
        if (gameStatus === 'failed') return 'status-failed';
        return 'status-pending';
    };

    const formatGameLabel = (game) => game
        .replace(/[_-]+/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase());

    const handleStartInitialization = async () => {
        setIsStarting(true);
        setStartError(null);
        try {
            const response = await postStartupInit();
            if (response?.data?.status === 'completed') {
                onComplete();
            }
        } catch (error) {
            console.error('Error starting initialization:', error);
            setStartError(analyzeError(error));
        } finally {
            setIsStarting(false);
        }
    };

    return (
        <div className="container py-4 startup-shell">
            <h2 className="mb-3">🎰 Lottery Data Initialization</h2>
            {startError && (
                <div className="mb-3">
                    <ErrorMessage errorReport={startError} />
                </div>
            )}
            {(status.status === 'pending' || status.status === 'ready' || status.status === 'completed') && (
                <div className="mb-3 d-flex flex-wrap gap-2">
                    {status.status !== 'completed' && (
                        <button
                            onClick={handleStartInitialization}
                            disabled={isStarting}
                            className="btn btn-primary"
                        >
                            {isStarting ? 'Starting...' : 'Start Initialization'}
                        </button>
                    )}
                    {(status.status === 'ready' || status.status === 'completed') && (
                        <button
                            onClick={onComplete}
                            className="btn btn-outline-secondary"
                        >
                            Continue to Dashboard
                        </button>
                    )}
                </div>
            )}
            
            {/* Status Header */}
            <div className={`card startup-status-card mb-4 ${isCompleted ? 'is-complete' : isIngesting ? 'is-active' : ''}`}>
                <div className="card-body">
                <p className="mb-2">
                    <strong>Status:</strong> 
                    {isCompleted && <span className="text-success ms-2">✓ Complete</span>}
                    {isIngesting && <span className="text-warning ms-2">⟳ Downloading Game Data...</span>}
                    {!isCompleted && !isIngesting && <span className="ms-2">Ready</span>}
                </p>
                {status.current_game && (
                    <p className="mb-2">
                        <strong>Currently Processing:</strong> {status.current_game.toUpperCase()}
                        {status.current_task && <span> ({status.current_task})</span>}
                    </p>
                )}
                <p className="mb-0">
                    <strong>Elapsed Time:</strong> {formatTime(elapsedSeconds)}
                </p>
                </div>
            </div>

            {/* Progress Bar */}
            <div className="mb-4">
                <h4 className="mb-2">
                    Download Progress: {Math.round(overallProgress)}% ({progressVal.toFixed(1)} of {totalVal} games)
                    {rowsFetched > 0 && rowsTotal > 0 && (
                        <span className="small text-muted">
                            {' '}({rowsFetched.toLocaleString()} / {rowsTotal.toLocaleString()} rows in {status.current_game})
                        </span>
                    )}
                    {rowsFetched > 0 && rowsTotal <= 0 && (
                        <span className="small text-muted">
                            {' '}({rowsFetched.toLocaleString()} rows in {status.current_game})
                        </span>
                    )}
                </h4>
                {!hasGamesConfigured && (
                    <p className="text-muted mb-2">
                        No games reported by backend yet. This usually means ingestion has not started.
                    </p>
                )}
                <div className="progress startup-progress-bar">
                    <progress
                        className={`startup-progress-native ${overallProgress === 100 ? 'is-complete' : 'is-active'}`}
                        value={Math.max(0, Math.min(overallProgress, 100))}
                        max="100"
                    />
                    <span className="startup-progress-overlay">{Math.round(overallProgress)}%</span>
                </div>
                {hasGamesConfigured && (
                    <div className="d-flex flex-wrap gap-2 mt-2">
                        {gameEntries.map(([game, gameData]) => (
                            <div
                                key={game}
                                className={`startup-game-pill ${getGameStatusClass(gameData.status)} ${game === status.current_game ? 'is-current' : ''}`}
                            >
                                <span className={getStatusTextClass(gameData.status)}>{getStatusIcon(gameData.status)}</span>
                                <span>{formatGameLabel(game)}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Game Status Table */}
            <h4>Game Download Status</h4>
            <div className="table-responsive mb-4">
            <table className="table table-hover align-middle startup-status-table">
                <thead>
                    <tr>
                        <th>Game</th>
                        <th className="text-center startup-status-col">Status</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
                    {gameEntries.length === 0 && (
                        <tr>
                            <td colSpan="3" className="text-center text-muted py-3">
                                Waiting for backend to report game ingestion status...
                            </td>
                        </tr>
                    )}
                    {gameEntries.map(([game, gameData]) => (
                        <tr
                            key={game}
                            className={game === status.current_game ? 'startup-current-row' : ''}
                        >
                            <td className="fw-semibold">
                                {formatGameLabel(game)}
                            </td>
                            <td className={`text-center fs-5 fw-bold ${getStatusTextClass(gameData.status)}`}>
                                {getStatusIcon(gameData.status)}
                            </td>
                            <td>
                                {gameData.status === 'completed' && (
                                    <span className="text-success">Downloaded ✓</span>
                                )}
                                {gameData.status === 'ingesting' && (
                                    <span className="text-warning">Downloading...</span>
                                )}
                                {gameData.status === 'pending' && (
                                    <span className="text-secondary">Waiting...</span>
                                )}
                                {gameData.status === 'failed' && (
                                    <span className="text-danger">
                                        Failed {gameData.error && `(${gameData.error})`}
                                    </span>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
            </div>

            {isCompleted && (
                <div className="alert alert-success text-center startup-complete-banner">
                    <h3 className="my-2">✓ All games downloaded successfully!</h3>
                    <p className="mb-0">Launching dashboard...</p>
                </div>
            )}
        </div>
    );
};

export default StartupProgress;
