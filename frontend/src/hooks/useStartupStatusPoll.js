import { useEffect, useState } from 'react';
import axios from 'axios';
import { getApiBase } from '../utils/apiBase';
import { startPolling } from '../utils/polling';

const DEFAULT_STATUS = {
  status: 'unknown',
  progress: 0,
  total: 8,
  elapsed_s: 0,
  percent_complete: 0,
};

export function useStartupStatusPoll({
  enabled = true,
  intervalMs = 2000,
  stopWhenCompleted = false,
} = {}) {
  const [startupStatus, setStartupStatus] = useState(DEFAULT_STATUS);
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    if (!enabled) return undefined;
    const apiBase = getApiBase();
    return startPolling({
      intervalMs,
      maxBackoffMs: 15000,
      tick: async () => {
        const response = await axios.get(`${apiBase}/api/startup_status`, { timeout: 12000 });
        const data = response?.data || {};
        setErrorMessage('');
        setStartupStatus({
          status: String(data.status || 'unknown').toLowerCase(),
          progress: Number(data.progress || 0),
          total: Number(data.total || 8),
          elapsed_s: Number(data.elapsed_s || 0),
          percent_complete: Number(data.percent_complete || 0),
          current_game: data.current_game || null,
          games: data.games || {},
          available_games: data.available_games || [],
          current_game_rows_fetched: Number(data.current_game_rows_fetched || 0),
          current_game_rows_total: Number(data.current_game_rows_total || 0),
          current_task: data.current_task || null,
          completed_games: Number(data.completed_games || 0),
        });
        return data;
      },
      shouldStop: stopWhenCompleted
        ? (data) => data?.status === 'completed'
        : undefined,
    });
  }, [enabled, intervalMs, stopWhenCompleted]);

  return { startupStatus, errorMessage, setStartupStatus };
}
