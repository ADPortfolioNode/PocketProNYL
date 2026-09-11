import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { getApiBase } from '../utils/apiBase';
import { analyzeError, ErrorCategory } from '../utils/errorUtils';
import { startPolling } from '../utils/polling';

const StartupProgress = ({ onComplete }) => {
    const [status, setStatus] = useState(null);
    const [elapsedSeconds, setElapsedSeconds] = useState(0);
    const ingestStartedAtRef = useRef(Date.now());

    useEffect(() => {
        const timer = setInterval(() => {
            setElapsedSeconds((Date.now() - ingestStartedAtRef.current) / 1000);
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
                    const data = response.data || {};
                    if (data.status === 'ready' && !data.current_game && (!data.games || !Object.keys(data.games).length)) {
                        return data;
                    }
                    setStatus(data);
                    if (data.elapsed_s) setElapsedSeconds(Number(data.elapsed_s));
                    if (data.status === 'completed') onComplete();
                    return data;
                } catch (error) {
                    return { status: 'ingesting', _pollError: analyzeError(error).originalError };
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

    const games = status?.games || {};
    const availableGames = Array.isArray(status?.available_games) && status.available_games.length
        ? status.available_games
        : Object.keys(games);
    const currentGame = status?.current_game;
    const gameEntries = availableGames.map((game) => {
        const raw = games[game] || {};
        let st = String(raw.status || 'pending').toLowerCase();
        if (game === currentGame && !['completed', 'ingesting', 'fetching', 'running'].includes(st)) {
            st = 'ingesting';
        }
        const fetched = Number(raw.rows_fetched || 0);
        let total = Number(raw.total_rows || 0);
        if (fetched > total) total = fetched;
        const percent = total > 0 ? Math.min(100, (fetched / total) * 100) : (st === 'completed' ? 100 : (st === 'ingesting' ? 8 : 0));
        return [game, { status: st, error: raw.error || null, rows_fetched: fetched, total_rows: total, percent }];
    });

    const progressVal = Number(status?.progress ?? 0);
    const totalVal = Number(status?.total) || availableGames.length || 1;
    let rowsFetched = Number(status?.current_game_rows_fetched ?? 0);
    let rowsTotal = Number(status?.current_game_rows_total ?? 0);
    if (rowsFetched > rowsTotal) rowsTotal = rowsFetched;
    const completedGameCount = gameEntries.filter(([, g]) => g.status === 'completed').length;
    const rowFrac = rowsTotal > 0 ? Math.min(1, rowsFetched / rowsTotal) : 0;
    const backendPercent = Number(status?.percent_complete);
    const fallbackPercent = ((completedGameCount + rowFrac) / totalVal) * 100;
    const overallProgress = Math.max(0, Math.min(Number.isFinite(backendPercent) ? backendPercent : fallbackPercent, 100));
    const isIngesting = ['ingesting', 'queued', 'pending', 'fetching'].includes(String(status?.status || 'pending').toLowerCase());
    const isCompleted = status?.status === 'completed';
    const rowLabel = rowsFetched > 0
        ? `${rowsFetched.toLocaleString()}${rowsTotal && rowsTotal !== rowsFetched ? ` / ${rowsTotal.toLocaleString()}` : ''} rows`
        : '';

    return (
        <div className="container py-4 startup-shell">
            <h2 className="mb-3">Lottery Data Initialization</h2>
            <p className="text-muted">Downloading official draw history. You can open the dashboard anytime; games fill in as they finish.</p>
            <button onClick={onComplete} className="btn btn-outline-secondary mb-4">Continue to Dashboard</button>

            <div className={`card startup-status-card mb-4 ${isCompleted ? 'is-complete' : 'is-active'}`}>
                <div className="card-body">
                    <p className="mb-2">
                        <strong>Status:</strong>{' '}
                        {isCompleted ? <span className="text-success">Complete</span> : <span className="text-warning">Downloading game history...</span>}
                    </p>
                    <p className="mb-2">
                        <strong>Currently processing:</strong>{' '}
                        {currentGame ? formatGameLabel(currentGame) : 'starting...'}
                        {status?.current_task ? ` (${status.current_task})` : ''}
                    </p>
                    <p className="mb-0">
                        <strong>Elapsed:</strong> {formatTime(elapsedSeconds)}
                        <span className="text-muted"> · {completedGameCount}/{totalVal} games done</span>
                    </p>
                </div>
            </div>

            <div className="mb-4">
                <h4 className="mb-2">
                    Download progress: {Math.round(overallProgress)}%
                    <span className="small text-muted"> ({progressVal.toFixed(1)} / {totalVal} games)</span>
                    {rowLabel && <span className="small text-muted"> · {rowLabel}{currentGame ? ` in ${formatGameLabel(currentGame)}` : ''}</span>}
                </h4>
                <div className="progress" style={{ height: '1.5rem' }}>
                    <div
                        className={`progress-bar ${isCompleted ? 'bg-success' : 'bg-warning progress-bar-striped progress-bar-animated'}`}
                        style={{ width: `${Math.max(overallProgress, isIngesting && overallProgress < 2 ? 6 : 0)}%` }}
                        role="progressbar"
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
                                    <div className="progress" style={{ height: '0.6rem' }}>
                                        <div
                                            className={`progress-bar ${gameData.status === 'completed' ? 'bg-success' : 'bg-warning'}`}
                                            style={{ width: `${Math.max(0, Math.min(gameData.percent, 100))}%` }}
                                        />
                                    </div>
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
