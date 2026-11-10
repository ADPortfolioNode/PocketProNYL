import React, { useEffect, useState } from 'react';
import getApiBase from '../utils/apiBase';
import './WorkflowChart.css';

/**
 * TrainingChart — the training overfit check as a live matplotlib chart.
 *
 * Mirrors WorkflowChart: the backend renders accuracy by validation round,
 * the kept-weights validation score vs the untouched holdout, the gap
 * against the 2-point line, and the warning reason in words as a PNG.
 * Refreshes every 2s while training runs (`active`) and at `refreshMs`
 * when idle, so the picture tracks the last completed tuning. On any
 * image error the chart hides itself instead of breaking the panel.
 */
export default function TrainingChart({ game, refreshMs = 15000, active = false, signal, title, testId }) {
  const [tick, setTick] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), active ? 2000 : refreshMs);
    return () => clearInterval(id);
  }, [refreshMs, active]);

  // Stream-driven flows bump the signal so the chart refreshes the moment
  // new data lands, not on the timer.
  useEffect(() => {
    setTick((t) => t + 1);
  }, [signal]);

  if (failed) return null;

  const params = new URLSearchParams({ t: String(tick) });
  if (game) params.set('game', game);
  const src = `${getApiBase()}/api/training_chart?${params.toString()}`;

  return (
    <figure className={`workflow-chart${active ? ' is-live' : ''}`} data-testid={testId || 'training-chart'}>
      <figcaption className="workflow-chart__head">
        <span className="workflow-chart__title">{title || 'Training overfit check · matplotlib'}</span>
        <span className={`workflow-chart__live${active ? ' is-live' : ''}`} role="status">
          <i aria-hidden="true" />
          {active ? 'Live' : 'Updated'}
        </span>
      </figcaption>
      <span className="workflow-chart__sweep" aria-hidden="true" />
      <img src={src} alt="Training validation/holdout gap chart" onError={() => setFailed(true)} />
    </figure>
  );
}
