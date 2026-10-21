import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import CandyStripeProgress from './CandyStripeProgress';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe('CandyStripeProgress', () => {
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
  const bar = () => container.querySelector('[data-testid="candy-progress"]');
  const fill = () => container.querySelector('.candy-progress-fill');

  test('indeterminate sweep when in progress with no known progress', () => {
    act(() => root.render(<CandyStripeProgress status="in_progress" value={0} label="Downloading" />));
    expect(bar().dataset.state).toBe('indeterminate');
    expect(bar().className).toContain('candy-progress-indeterminate');
    expect(container.querySelector('[role="progressbar"]').getAttribute('aria-busy')).toBe('true');
    expect(container.textContent).not.toContain('0%');
  });

  test('determinate animated stripes when progress is known', () => {
    act(() => root.render(<CandyStripeProgress status="in_progress" value={42.4} detail="Fetching 4,240 of ~10,000 rows" />));
    expect(bar().dataset.state).toBe('in_progress');
    expect(bar().className).toContain('candy-progress-in-progress');
    expect(fill().style.width).toBe('42.4%');
    expect(container.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')).toBe('42');
    expect(container.textContent).toContain('42%');
    expect(container.textContent).toContain('Fetching 4,240');
  });

  test('complete fills the bar without the in-progress animation class', () => {
    act(() => root.render(<CandyStripeProgress status="complete" value={10} />));
    expect(fill().style.width).toBe('100%');
    expect(bar().className).not.toContain('in-progress');
  });

  test('error only on real failure, with a working Retry', () => {
    const onRetry = jest.fn();
    act(() => root.render(<CandyStripeProgress status="in_progress" value={null} error="should not show" onRetry={onRetry} />));
    expect(container.querySelector('[role="alert"]')).toBeNull();
    act(() => root.render(<CandyStripeProgress status="error" error="Chroma is unreachable." onRetry={onRetry} />));
    expect(container.querySelector('[role="alert"]').textContent).toContain('Chroma is unreachable.');
    act(() => { container.querySelector('button').click(); });
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test('stylesheet honours prefers-reduced-motion', () => {
    const css = require('fs').readFileSync(require('path').join(__dirname, 'CandyStripeProgress.css'), 'utf8');
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*animation: none/);
    expect(css).toMatch(/@keyframes candy-stripes/);
    expect(css).toMatch(/@keyframes candy-sweep/);
  });
});
