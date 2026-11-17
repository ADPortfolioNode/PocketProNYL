import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import PredictionPanel from './PredictionPanel';

jest.mock('axios');
jest.mock('./PredictionDisplay', () => function MockDisplay({ prediction }) {
  return <div data-testid="numbers">{`${prediction.game}:${(prediction.predicted_numbers || []).join(',')}`}</div>;
});

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function flush() {
  return act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe('GamePrediction isolation', () => {
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

  test('switching games clears the previous game\'s numbers', async () => {
    axios.post.mockResolvedValue({ data: { status: 'COMPLETED', game: 'take5', predicted_numbers: [1, 2, 3, 4, 5] } });
    await act(async () => { root.render(<PredictionPanel games={['take5', 'pick3']} />); });

    const suggest = () => [...container.querySelectorAll('button')].find((b) => /Suggest/.test(b.textContent));
    await act(async () => { suggest().click(); });
    await flush();
    expect(container.textContent).toContain('take5:1,2,3,4,5');

    const select = container.querySelector('select');
    await act(async () => {
      select.value = 'pick3';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(container.textContent).toContain('Suggestion for pick3');
    expect(container.textContent).not.toContain('take5:1,2,3,4,5');
  });

  test('a reply for a different game is not shown', async () => {
    axios.post.mockResolvedValue({ data: { status: 'COMPLETED', game: 'pick3', predicted_numbers: [1, 2, 3] } });
    await act(async () => { root.render(<PredictionPanel games={['take5', 'pick3']} />); });
    const suggest = [...container.querySelectorAll('button')].find((b) => /Suggest/.test(b.textContent));
    await act(async () => { suggest.click(); });
    await flush();
    expect(container.textContent).not.toContain('pick3:1,2,3');
    expect(container.textContent).toContain('was for pick3, not take5');
  });
});
