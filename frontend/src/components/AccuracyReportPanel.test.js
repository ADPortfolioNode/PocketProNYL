import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import AccuracyReportPanel from './AccuracyReportPanel';

jest.mock('axios');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

const report = (overrides = {}) => ({
  status: 'ok',
  game: 'take5',
  last: 3,
  data_label: 'Real',
  data_label_note: 'Graded against real draws.',
  suggestions: [
    {
      id: 'a', status: 'graded', served_at: '2026-10-21T16:30:00+00:00', suggestion_date: '2026-10-21',
      target_draw_date: '2026-10-21', target_draw_session: 'evening',
      expected_draw_date: '2026-10-21', expected_draw_session: 'evening',
      actual_draw_date: '2026-10-21', actual_draw_session: 'evening', draw_source: 'ingested',
      numbers: [1, 2, 3, 4, 5], actual_numbers: [1, 2, 9, 10, 11], matched_numbers: [1, 2], hits: 2,
      expected_hits_by_chance: 0.641, p_value_at_least_these_hits: 0.1342, tier: { tier: 'Match 2 (free Quick Pick)', prize: 1 },
      strategy_weights: { frequency: 0.6, hot_cold: 0.4 },
      strategy_contributions: { frequency: [1, 2, 3], hot_cold: [4, 5] },
      model: 'frequency_blend', model_version: 'abc123', source: 'ledger', recent_k: 50,
    },
    { id: 'b', status: 'pending', served_at: '2026-10-22T01:15:00+00:00', suggestion_date: '2026-10-21',
      target_draw_date: '2026-10-22', expected_draw_date: '2026-10-22', expected_draw_session: 'evening',
      numbers: [6, 7, 8, 9, 10], expected_hits_by_chance: 0.641,
      strategy_weights: {}, strategy_contributions: {} },
  ],
  pooled_last: { graded: 1, total_hits: 2, expected_total_hits_by_chance: 0.641, p_value_exact: 0.1342,
    verdict: 'Consistent with chance: these hits are what random tickets would get.' },
  pooled_all_time: { graded: 1, lift_vs_chance: 3.12 },
  backtest: { label: 'Simulated', available: true, source: 'holdout', simulated_mean_hits: 0.66,
    simulated_random_baseline_mean_hits: 0.64, real_mean_hits: 2, gap_real_minus_simulated: 1.34 },
  disclaimer: 'Lottery draws are random. No pick, model, filter or wheel can change the odds of winning.',
  ...overrides,
});

describe('AccuracyReportPanel', () => {
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

  const clickCheck = async () => {
    const button = [...container.querySelectorAll('button')].find((b) => /Check last 3 suggestions/.test(b.textContent));
    await act(async () => { button.click(); });
    await flush();
  };

  const clickRow = async (index = 0) => {
    const rows = container.querySelectorAll('[data-testid="suggestion-row"]');
    await act(async () => { rows[index].click(); });
    await flush();
  };

  test('nothing is fetched until the button is pressed, then the analysis renders', async () => {
    axios.get.mockResolvedValue({ data: report() });
    await act(async () => { root.render(<AccuracyReportPanel game="take5" />); });
    expect(axios.get).not.toHaveBeenCalled();
    await clickCheck();
    expect(axios.get).toHaveBeenCalledWith(expect.stringContaining('/api/accuracy_report'),
      expect.objectContaining({ params: { game: 'take5', last: 3 } }));
    expect(container.textContent).toContain('Real');
    expect(container.textContent).toContain('2 vs 0.64');
    expect(container.textContent).toContain('Draw not in yet');
    expect(container.textContent).toContain('Simulated');
    expect(container.textContent).toContain('can change the odds');
    expect(container.querySelectorAll('.insight-ball-hit').length).toBe(4);
  });

  test('a report for another game is not shown', async () => {
    axios.get.mockResolvedValue({ data: report({ game: 'pick3' }) });
    await act(async () => { root.render(<AccuracyReportPanel game="take5" />); });
    await clickCheck();
    expect(container.textContent).toContain('was for pick3, not take5');
    expect(container.querySelector('[data-testid="accuracy-report"]')).toBeNull();
  });

  test('labels simulated data when nothing is graded yet', async () => {
    axios.get.mockResolvedValue({ data: report({ data_label: 'Simulated', suggestions: [],
      pooled_last: { graded: 0 }, pooled_all_time: { graded: 0 } }) });
    await act(async () => { root.render(<AccuracyReportPanel game="take5" />); });
    await clickCheck();
    const badge = container.querySelector('.insight-badge');
    expect(badge.textContent).toBe('Simulated');
    expect(container.textContent).toContain('No suggestions served for this game yet');
  });

  test('clicking a graded row opens the detail with dates, numbers and weights', async () => {
    axios.get.mockResolvedValue({ data: report() });
    await act(async () => { root.render(<AccuracyReportPanel game="take5" />); });
    await clickCheck();
    expect(container.querySelector('[data-testid="suggestion-detail"]')).toBeNull();
    await clickRow(0);
    const detail = container.querySelector('[data-testid="suggestion-detail"]');
    expect(detail).not.toBeNull();
    // Suggestion date + time (served 16:30 UTC = 12:30 PM lottery time), target draw + winning numbers.
    expect(detail.textContent).toContain('10/21/2026, 12:30 PM');
    expect(detail.textContent).toContain('Suggestion date: 2026-10-21');
    expect(detail.textContent).toContain('Target draw (immediate next draw after the suggestion)');
    expect(detail.textContent).toContain('evening');
    expect(detail.textContent).toContain('2 hits vs 0.64 expected by chance');
    expect(detail.textContent).toContain('p-value 0.1342');
    expect(detail.textContent).toContain('Match 2 (free Quick Pick)');
    // Weights behind the suggestion, so it can be repeated.
    expect(detail.textContent).toContain('frequency');
    expect(detail.textContent).toContain('0.600');
    expect(detail.textContent).toContain('hot_cold');
    expect(detail.textContent).toContain('Model: frequency_blend');
  });

  test('clicking a pending row shows the awaited draw and the no-weights note', async () => {
    axios.get.mockResolvedValue({ data: report() });
    await act(async () => { root.render(<AccuracyReportPanel game="take5" />); });
    await clickCheck();
    await clickRow(1);
    const detail = container.querySelector('[data-testid="suggestion-detail"]');
    expect(detail).not.toBeNull();
    expect(detail.textContent).toContain('Draw not in yet');
    expect(detail.textContent).toContain('2026-10-22');
    expect(detail.textContent).toContain('No weights recorded for this suggestion');
  });

  test('close button and clicking the selected row again hide the detail', async () => {
    axios.get.mockResolvedValue({ data: report() });
    await act(async () => { root.render(<AccuracyReportPanel game="take5" />); });
    await clickCheck();
    await clickRow(0);
    expect(container.querySelector('[data-testid="suggestion-detail"]')).not.toBeNull();
    const close = container.querySelector('[data-testid="suggestion-detail-close"]');
    await act(async () => { close.click(); });
    await flush();
    expect(container.querySelector('[data-testid="suggestion-detail"]')).toBeNull();
    await clickRow(0);
    expect(container.querySelector('[data-testid="suggestion-detail"]')).not.toBeNull();
    await clickRow(0);
    expect(container.querySelector('[data-testid="suggestion-detail"]')).toBeNull();
  });

  test('selecting another row switches the detail', async () => {
    axios.get.mockResolvedValue({ data: report() });
    await act(async () => { root.render(<AccuracyReportPanel game="take5" />); });
    await clickCheck();
    await clickRow(0);
    expect(container.querySelector('[data-testid="suggestion-detail"]').textContent).toContain('frequency');
    await clickRow(1);
    const detail = container.querySelector('[data-testid="suggestion-detail"]');
    expect(detail.textContent).toContain('Draw not in yet');
    expect(detail.textContent).not.toContain('frequency');
  });
});
