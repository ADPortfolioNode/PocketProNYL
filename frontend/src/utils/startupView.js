// Derives what the "Lottery Data Initialization" screen shows from
// /api/startup_status plus the health of the polling itself.
//
// Rules (Deo's standard): in-progress work always animates; an error with
// Retry appears only on a real failure (backend error status, a backend-
// reported stall, the backend unreachable past a timeout, or no status change
// at all past a long frontend stall timeout). A completed or idle backend
// always ends the screen; it never sits on "starting...".

export const CONNECT_TIMEOUT_MS = 45000;
export const FRONTEND_STALL_MS = 10 * 60 * 1000;

const ACTIVE_GAME_STATES = ['ingesting', 'fetching', 'running', 'queued'];

export function isStatusPayload(data) {
  return Boolean(data) && typeof data === 'object' && !Array.isArray(data) && typeof data.status === 'string';
}

export function statusSignature(s) {
  if (!isStatusPayload(s)) return '';
  return JSON.stringify([
    s.status, s.phase, s.phase_game, s.phase_rows_done, s.progress, s.completed_games,
    s.current_game, s.current_game_rows_fetched,
  ]);
}

function anyGameActive(s) {
  return Object.values(s.games || {}).some((g) => ACTIVE_GAME_STATES.includes(String(g?.status || '').toLowerCase()));
}

export function isBackendDone(s) {
  if (!isStatusPayload(s)) return false;
  const st = s.status.toLowerCase();
  if (st === 'completed') return true;
  const phaseActive = s.phase && !['done', 'error'].includes(s.phase);
  return ['ready', 'idle'].includes(st) && !phaseActive && !anyGameActive(s);
}

export function phaseText(s) {
  if (!isStatusPayload(s) || !s.phase_label) return null;
  const game = s.phase_game ? `${String(s.phase_game).toUpperCase()}: ` : '';
  return `${game}${s.phase_label}${s.phase_detail ? ` — ${s.phase_detail}` : ''}`;
}

export function deriveStartupView({ status, lastOkAt = null, lastError = null, lastChangeAt = null, startedAt, now }) {
  const total = isStatusPayload(status)
    ? (Number(status.total) || (status.available_games || []).length || null)
    : null;
  const gamesDone = isStatusPayload(status) ? Number(status.completed_games || 0) : null;
  const base = { total, gamesDone, percent: null, phase: phaseText(status), errorMessage: null, retry: null };

  if (!isStatusPayload(status)) {
    if (lastError && now - startedAt > CONNECT_TIMEOUT_MS) {
      return { ...base, mode: 'error', retry: 'poll', errorMessage: `Can't get startup status from the backend: ${lastError}` };
    }
    return { ...base, mode: 'connecting', phase: 'Connecting to the backend…' };
  }
  if (isBackendDone(status)) return { ...base, mode: 'complete', percent: 100 };
  if (lastError && lastOkAt && now - lastOkAt > CONNECT_TIMEOUT_MS) {
    return { ...base, mode: 'error', retry: 'poll', errorMessage: `Lost contact with the backend: ${lastError}` };
  }
  if (String(status.status).toLowerCase() === 'error') {
    return { ...base, mode: 'error', retry: 'startup_init', errorMessage: status.message || 'The backend reported an ingest error.' };
  }
  if (status.stalled) {
    return { ...base, mode: 'error', retry: 'startup_init', errorMessage: status.stall_message || 'Ingest stopped making progress.' };
  }
  if (!status.phase && lastChangeAt && now - lastChangeAt > FRONTEND_STALL_MS) {
    return { ...base, mode: 'error', retry: 'startup_init', errorMessage: 'No progress for 10 minutes. The ingest may be stuck; retry it.' };
  }
  const pct = Number(status.percent_complete);
  return { ...base, mode: 'ingesting', percent: Number.isFinite(pct) && pct > 0 ? pct : null };
}
