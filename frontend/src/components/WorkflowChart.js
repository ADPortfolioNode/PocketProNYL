import React, { useEffect, useState } from 'react';
import getApiBase from '../utils/apiBase';
import './WorkflowChart.css';

/**
 * WorkflowChart — live matplotlib chart for one workflow.
 *
 * The backend renders the workflow's steps (with the active step's meta)
 * as a PNG; this component refreshes the image every 2s while the flow
 * is running (`active`) and at `refreshMs` when idle, so the picture
 * tracks the real state. `signal` is an optional value (e.g. rows
 * fetched) that forces an immediate refresh when it changes, for flows
 * driven by a stream rather than a timer. On any image error the chart
 * hides itself instead of breaking the panel around it.
 */
export default function WorkflowChart({ flow, game, refreshMs = 15000, active = false, signal, title, testId }) {
  const [tick, setTick] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), active ? 2000 : refreshMs);
    return () => clearInterval(id);
  }, [refreshMs, active]);

  // Stream-driven flows bump the signal (rows fetched, phase change, …)
  // so the chart refreshes the moment new data lands, not on the timer.
  useEffect(() => {
    setTick((t) => t + 1);
  }, [signal]);

  if (failed) return null;

  const params = new URLSearchParams({ flow, t: String(tick) });
  if (game) params.set('game', game);
  const src = `${getApiBase()}/api/workflow_chart?${params.toString()}`;

  return (
    <figure className={`workflow-chart${active ? ' is-live' : ''}`} data-testid={testId || `workflow-chart-${flow}`}>
      <figcaption className="workflow-chart__head">
        <span className="workflow-chart__title">{title || 'Live workflow · matplotlib'}</span>
        <span className={`workflow-chart__live${active ? ' is-live' : ''}`} role="status">
          <i aria-hidden="true" />
          {active ? 'Live' : 'Updated'}
        </span>
      </figcaption>
      <span className="workflow-chart__sweep" aria-hidden="true" />
      <img src={src} alt={`Live ${flow} workflow steps chart`} onError={() => setFailed(true)} />
    </figure>
  );
}
