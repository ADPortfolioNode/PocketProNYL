import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import StartupProgress from './StartupProgress';
import { CONNECT_TIMEOUT_MS, deriveStartupView, isBackendDone } from '../utils/startupView';

jest.mock('axios');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });

const ingesting = (extra = {}) => ({
  status: 'ingesting', total: 11, completed_games: 0, percent_complete: 0, current_game: 'take5',
  available_games: ['take5'], games: { take5: { status: 'ingesting' } },
  phase_game: 'take5', phase: 'fetching', phase_label: 'Fetching rows from data.ny.gov',
  phase_detail: '2,000 of ~4,000 rows', phase_rows_done: 2000, phase_rows_total: 4000, stalled: false,
  ...extra,
});

describe('deriveStartupView', () => {
  const t0 = 1_000_000;
  test('connecting, never a fake 0/1 games, before the first status', () => {
    const view = deriveStartupView({ status: null, startedAt: t0, now: t0 + 5000 });
    expect(view.mode).toBe('connecting');
    expect(view.total).toBeNull();
  });
  test('backend unreachable past the timeout is a real error with Retry', () => {
    const view = deriveStartupView({ status: null, lastError: 'Network Error', startedAt: t0, now: t0 + CONNECT_TIMEOUT_MS + 1 });
    expect(view.mode).toBe('error');
    expect(view.retry).toBe('poll');
  });
  test('completed or idle backend always completes', () => {
    expect(isBackendDone({ status: 'completed', games: {} })).toBe(true);
    expect(isBackendDone({ status: 'ready', games: { take5: { status: 'completed' } } })).toBe(true);
    expect(isBackendDone({ status: 'ready', phase: 'fetching', games: {} })).toBe(false);
  });
  test('normal delay stays in progress with phase text; zero progress is indeterminate', () => {
    const view = deriveStartupView({ status: ingesting(), lastOkAt: t0, lastChangeAt: t0, startedAt: t0, now: t0 + 60000 });
    expect(view.mode).toBe('ingesting');
    expect(view.percent).toBeNull();
    expect(view.phase).toContain('TAKE5: Fetching rows');
  });
  test('backend-reported stall is an error that retries the ingest', () => {
    const view = deriveStartupView({ status: ingesting({ stalled: true, stall_message: 'No progress for 200s' }), lastOkAt: t0, startedAt: t0, now: t0 });
    expect(view.mode).toBe('error');
    expect(view.retry).toBe('startup_init');
    expect(view.errorMessage).toContain('No progress');
  });
});

describe('StartupProgress screen', () => {
  let container;
  let root;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.resetAllMocks();
  });

  test('shows the phase under an animated bar while ingesting', async () => {
    axios.get.mockResolvedValue({ data: ingesting() });
    await act(async () => { root.render(<StartupProgress onComplete={() => {}} />); });
    await flush();
    const overall = container.querySelector('[data-testid="startup-overall"]');
    expect(overall.dataset.state).toBe('indeterminate');
    expect(container.textContent).toContain('Fetching rows from data.ny.gov — 2,000 of ~4,000 rows');
    expect(container.querySelector('[data-testid="startup-game"]').dataset.state).toBe('in_progress');
    expect(container.textContent).not.toContain('starting...');
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  test('a completed backend ends the screen', async () => {
    const onComplete = jest.fn();
    axios.get.mockResolvedValue({ data: { status: 'completed', total: 11, completed_games: 11, games: {} } });
    await act(async () => { root.render(<StartupProgress onComplete={onComplete} />); });
    await flush();
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  test('an HTML answer (unproxied /api) is not mistaken for progress', async () => {
    axios.get.mockResolvedValue({ data: '<!doctype html><html></html>' });
    await act(async () => { root.render(<StartupProgress onComplete={() => {}} />); });
    await flush();
    expect(container.textContent).toContain('Connecting to the backend');
    expect(container.textContent).not.toContain('/ 1 games');
  });

  test('a stalled ingest shows a friendly error whose Retry restarts it', async () => {
    axios.get.mockResolvedValue({ data: ingesting({ stalled: true, stall_message: 'Fetching for take5 made no progress for 200s.' }) });
    axios.post.mockResolvedValue({ data: { status: 'ok' } });
    await act(async () => { root.render(<StartupProgress onComplete={() => {}} />); });
    await flush();
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain('made no progress');
    await act(async () => { alert.querySelector('button').click(); });
    await flush();
    expect(axios.post).toHaveBeenCalledWith(expect.stringContaining('/api/startup_init'), { force: false }, expect.anything());
  });
});
