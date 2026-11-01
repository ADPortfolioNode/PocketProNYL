import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import CollectionDetailModal from './CollectionDetailModal';

jest.mock('axios');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });

const COLLECTION = { name: 'take5', count: 10782, state: 'refreshed', record_index: 1 };
const DRAWS = [
  { draw_date: '2025-05-11', draw_session: 'evening', winning_numbers: '16 21 25 28 34' },
  { draw_date: '2025-05-11', draw_session: 'midday', winning_numbers: '15 17 27 32 38' },
];

describe('CollectionDetailModal', () => {
  let container;
  let root;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    jest.clearAllMocks();
  });
  afterEach(() => {
    act(() => { root.unmount(); });
    container.remove();
  });

  test('opens from a collection and renders its details and sample draws', async () => {
    axios.get.mockResolvedValueOnce({ data: { draws: DRAWS } });
    const onClose = jest.fn();
    await act(async () => {
      root.render(<CollectionDetailModal collection={COLLECTION} apiBase="http://127.0.0.1:5002" onClose={onClose} />);
    });
    await flush();
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    expect(container.textContent).toContain('TAKE5');
    expect(container.textContent).toContain('10,782');
    expect(container.textContent).toContain('refreshed');
    expect(container.textContent).toContain('16 21 25 28 34');
    expect(container.textContent).toContain('15 17 27 32 38');
    expect(axios.get).toHaveBeenCalledWith(
      'http://127.0.0.1:5002/api/games/take5/draws?limit=5',
      expect.objectContaining({ timeout: 20000 }),
    );
  });

  test('close button returns to the list via onClose', async () => {
    axios.get.mockResolvedValueOnce({ data: { draws: [] } });
    const onClose = jest.fn();
    await act(async () => {
      root.render(<CollectionDetailModal collection={COLLECTION} apiBase="http://127.0.0.1:5002" onClose={onClose} />);
    });
    await flush();
    const closeButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent === 'Close');
    expect(closeButton).not.toBeUndefined();
    await act(async () => { closeButton.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('shows an honest error when sample draws fail to load', async () => {
    axios.get.mockRejectedValueOnce(new Error('network down'));
    const onClose = jest.fn();
    await act(async () => {
      root.render(<CollectionDetailModal collection={COLLECTION} apiBase="http://127.0.0.1:5002" onClose={onClose} />);
    });
    await flush();
    expect(container.textContent).toContain('network down');
  });

  test('shows an empty state when the collection has no draws yet', async () => {
    axios.get.mockResolvedValueOnce({ data: { draws: [] } });
    const onClose = jest.fn();
    await act(async () => {
      root.render(<CollectionDetailModal collection={{ name: 'pick3', count: 0, state: 'empty', record_index: 2 }} apiBase="http://127.0.0.1:5002" onClose={onClose} />);
    });
    await flush();
    expect(container.textContent).toContain('No draws ingested yet for this collection.');
  });
});
