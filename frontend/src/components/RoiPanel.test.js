import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import RoiPanel from './RoiPanel';

jest.mock('axios');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });

describe('RoiPanel', () => {
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

  test('states plainly that picks cannot change the odds, and fetches nothing on mount', async () => {
    await act(async () => { root.render(<RoiPanel games={['take5', 'pick3']} />); });
    expect(container.textContent).toContain('No pick, model, filter or wheel can change the odds of winning');
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  test('EV ranking renders rows and the not-ranked games', async () => {
    axios.get.mockResolvedValue({ data: {
      ranking: [{ game: 'take5', rank: 1, ev_per_dollar: 0.558, expected_loss_per_dollar: 0.442, note: 'Pari-mutuel.' }],
      not_ranked: [{ game: 'cash4life', note: 'Final draw 2026-02-21.' }],
    } });
    await act(async () => { root.render(<RoiPanel games={['take5']} />); });
    const button = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Rank games');
    await act(async () => { button.click(); });
    await flush();
    const table = container.querySelector('[data-testid="ev-ranking"]');
    expect(table.textContent).toContain('$0.56');
    expect(table.textContent).toContain('cash4life');
  });

  test('a failed request shows an error with Retry, not a static bar', async () => {
    axios.get.mockRejectedValue(new Error('Network Error'));
    await act(async () => { root.render(<RoiPanel games={['take5']} />); });
    const button = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Rank games');
    await act(async () => { button.click(); });
    await flush();
    const alert = container.querySelector('[data-testid="ev-ranking"] [role="alert"]');
    expect(alert.textContent).toContain('Network Error');
    expect(alert.querySelector('button').textContent).toBe('Retry');
  });
});
