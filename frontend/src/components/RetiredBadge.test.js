import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import GameSelector from './GameSelector';
import PredictionDisplay from './PredictionDisplay';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

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
});

const games = [
  { id: 'powerball', name: 'Powerball', draw_count: 10 },
  { id: 'cash4life', name: 'Cash4Life', draw_count: 5, retired: true, retired_date: '2026-02-21' },
];
const badge = () => container.querySelector('[data-testid="retired-badge"]');

test('game selector labels retired games and shows the badge when one is picked', () => {
  const onGameSelect = jest.fn();
  act(() => root.render(<GameSelector games={games} onGameSelect={onGameSelect} />));
  const options = Array.from(container.querySelectorAll('option')).map((o) => o.textContent);
  expect(options.find((t) => t.startsWith('Cash4Life'))).toMatch(/Retired: history only/);
  expect(options.find((t) => t.startsWith('Powerball'))).not.toMatch(/Retired/);
  expect(badge()).toBeNull();
  const select = container.querySelector('select');
  act(() => {
    select.value = 'cash4life';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(onGameSelect).toHaveBeenCalledWith('cash4life');
  expect(badge().textContent).toBe('Retired: history only');
});

test('suggestions for a retired game carry the label; active games do not', () => {
  const base = { formatted_prediction: { main_numbers: [1, 2, 3, 4, 5], bonus_numbers: [2] } };
  act(() => root.render(
    <PredictionDisplay prediction={{ ...base, game: 'cash4life', retired: true, status_label: 'Retired: history only' }} />
  ));
  expect(badge()).not.toBeNull();
  act(() => root.render(<PredictionDisplay prediction={{ ...base, game: 'powerball' }} />));
  expect(badge()).toBeNull();
});
