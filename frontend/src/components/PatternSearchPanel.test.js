import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import PatternSearchPanel from './PatternSearchPanel';

jest.mock('axios');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });

const REPORT = {
  status: 'ok',
  game: 'take5',
  scan_status: 'fresh',
  cached: true,
  last_run_at: '2026-10-23T22:00:00Z',
  draw_count: 1000,
  fdr_q: 0.1,
  windows: [25, 50, 'all'],
  promoted_signals: ['hot_w50'],
  history: [
    { at: '2026-10-23T21:00:00Z', type: 'promote', signals: ['hot_w50'], reason: 'won holdout' },
    { at: '2026-10-23T21:30:00Z', type: 'demote', signal: 'cold_w25', reason: 'no lift on 40 new draws' },
  ],
  split: { validation_draws: 80, holdout_draws: 60 },
  signals: [
    { signal: 'hot_w50', status: 'passed_holdout', label: 'passed holdout', validation: { lift: 1.42, p_value: 0.001 }, q_value: 0.01, holdout: { lift: 1.3 }, drift: { trend: 'strengthening', rolling_lift: [1.1, 1.5] } },
    { signal: 'gap_overdue', status: 'not_significant', label: 'not significant', validation: { lift: 1.02, p_value: 0.4 }, q_value: 0.8, drift: { trend: 'fading' } },
    { signal: 'bonus_frequency', status: 'not_applicable', label: 'not applicable' },
  ],
};

describe('PatternSearchPanel', () => {
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

  test('fetches nothing on mount', async () => {
    await act(async () => { root.render(<PatternSearchPanel games={['take5']} />); });
    expect(container.textContent).toContain('Pattern search');
    expect(axios.get).not.toHaveBeenCalled();
  });

  test('labels signals honestly: passed holdout vs not significant', async () => {
    axios.get.mockResolvedValue({ data: REPORT });
    await act(async () => { root.render(<PatternSearchPanel games={['take5', 'win4']} />); });
    const button = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Run pattern search');
    await act(async () => { button.click(); });
    await flush();
    expect(axios.get.mock.calls[0][0]).toContain('/api/pattern_report');
    expect(axios.get.mock.calls[0][1].params).toEqual({ game: 'take5' });
    const rows = container.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('passed holdout');
    expect(rows[0].textContent).toContain('1.30×');
    expect(rows[1].textContent).toContain('not significant');
    expect(container.querySelector('[data-testid="pattern-summary"]').textContent).toContain('1 of 2 signals passed holdout');
  });

  test('shows an error from the backend', async () => {
    axios.get.mockResolvedValue({ data: { status: 'error', message: 'collection missing' } });
    await act(async () => { root.render(<PatternSearchPanel games={['take5']} />); });
    const button = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Run pattern search');
    await act(async () => { button.click(); });
    await flush();
    expect(container.textContent).toContain('collection missing');
  });

  test('shows last run, trend arrows, promoted signals and history', async () => {
    axios.get.mockResolvedValue({ data: REPORT });
    await act(async () => { root.render(<PatternSearchPanel games={['take5']} />); });
    const button = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Run pattern search');
    await act(async () => { button.click(); });
    await flush();
    expect(container.querySelector('[data-testid="pattern-last-run"]').textContent).toContain('1000 draws');
    const arrows = [...container.querySelectorAll('[data-testid="pattern-trend"]')].map((a) => a.textContent);
    expect(arrows).toEqual(['▲', '▼']);
    expect(container.querySelector('[data-testid="pattern-promoted"]').textContent).toContain('hot_w50');
    const history = container.querySelector('[data-testid="pattern-history"]').textContent;
    expect(history).toContain('promoted hot_w50');
    expect(history).toContain('demoted cold_w25');
    expect(container.textContent).not.toContain('Request failed');
  });

  test('polls with a candy stripe while running, then shows the result; Rescan forces refresh', async () => {
    axios.get
      .mockResolvedValueOnce({ data: { status: 'pending', game: 'take5', scan_status: 'running', signals: [] } })
      .mockRejectedValueOnce(new Error('network blip'))
      .mockResolvedValue({ data: REPORT });
    await act(async () => { root.render(<PatternSearchPanel games={['take5']} pollMs={10} />); });
    const run = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Run pattern search');
    await act(async () => { run.click(); });
    expect(container.querySelector('[data-testid="candy-progress"]')).not.toBeNull();
    expect(container.textContent).not.toContain('network blip');
    await act(async () => { await new Promise((r) => setTimeout(r, 120)); });
    expect(axios.get.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(container.textContent).not.toContain('network blip');
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2);
    const rescan = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Rescan');
    await act(async () => { rescan.click(); });
    await flush();
    expect(axios.get.mock.calls.at(-1)[1].params).toEqual({ game: 'take5', refresh: true });
  });
});
