import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

// eslint-disable-next-line import/first
import WorkflowChart from './WorkflowChart';

global.IS_REACT_ACT_ENVIRONMENT = true;

function renderChart(props) {
  const el = document.createElement('div');
  document.body.appendChild(el);
  const root = createRoot(el);
  act(() => {
    root.render(<WorkflowChart flow="pipeline" {...props} />);
  });
  return { el, root };
}

function tickParam(el) {
  const img = el.querySelector('img');
  if (!img) return null;
  const match = img.getAttribute('src').match(/[?&]t=(\d+)/);
  return match ? Number(match[1]) : null;
}

describe('<WorkflowChart />', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
    document.body.innerHTML = '';
  });

  it('renders the live matplotlib chart for the flow', () => {
    const { el, root } = renderChart({ active: true, testId: 'workflow-chart-pipeline' });
    const figure = el.querySelector('[data-testid="workflow-chart-pipeline"]');
    expect(figure).not.toBeNull();
    expect(figure.className).toContain('is-live');
    const img = el.querySelector('img');
    expect(img.getAttribute('src')).toContain('/api/workflow_chart?flow=pipeline');
    expect(el.textContent).toContain('Live');
    act(() => root.unmount());
  });

  it('refreshes every 2s while a flow is live', () => {
    const { el, root } = renderChart({ active: true });
    const first = tickParam(el);
    act(() => {
      jest.advanceTimersByTime(2000);
    });
    const second = tickParam(el);
    expect(second).toBeGreaterThan(first);
    act(() => root.unmount());
  });

  it('refreshes at the idle cadence when nothing runs', () => {
    const { el, root } = renderChart({ active: false, refreshMs: 15000 });
    const first = tickParam(el);
    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(tickParam(el)).toBe(first);
    act(() => {
      jest.advanceTimersByTime(13000);
    });
    expect(tickParam(el)).toBeGreaterThan(first);
    act(() => root.unmount());
  });

  it('refreshes immediately when the stream signal changes', () => {
    const { el, root } = renderChart({ active: false, signal: 10 });
    const first = tickParam(el);
    act(() => {
      root.render(<WorkflowChart flow="pipeline" active={false} signal={42} />);
    });
    expect(tickParam(el)).toBeGreaterThan(first);
    act(() => root.unmount());
  });

  it('hides itself instead of breaking the panel when the image fails', () => {
    const { el, root } = renderChart({ active: false });
    const img = el.querySelector('img');
    expect(img).not.toBeNull();
    act(() => {
      img.dispatchEvent(new Event('error'));
    });
    expect(el.querySelector('[data-testid="workflow-chart-pipeline"]')).toBeNull();
    act(() => root.unmount());
  });
});
