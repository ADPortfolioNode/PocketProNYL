import React from 'react';
import './CandyStripeProgress.css';

/**
 * Candy-stripe progress bar.
 *
 * status: 'in_progress' | 'complete' | 'error' | 'idle'
 * value:  0-100 when real progress is known; null/undefined/0 while in progress
 *         shows an indeterminate stripe sweep instead of a static bar.
 * error:  shown only when status is 'error' (a real failure), with Retry.
 */
export default function CandyStripeProgress({
  status = 'in_progress',
  value = null,
  label = null,
  detail = null,
  error = null,
  onRetry = null,
  size = 'normal',
  showPercent = true,
  testId = 'candy-progress',
}) {
  const numeric = Number(value);
  const known = value !== null && value !== undefined && Number.isFinite(numeric) && numeric > 0;
  const pct = Math.max(0, Math.min(100, known ? numeric : 0));
  const inProgress = status === 'in_progress';
  const indeterminate = inProgress && !known;
  const width = status === 'complete' ? 100 : (indeterminate ? 38 : pct);
  const classes = [
    'candy-progress',
    `candy-progress-${status === 'in_progress' ? 'in-progress' : status}`,
    indeterminate ? 'candy-progress-indeterminate' : '',
    size === 'compact' ? 'candy-progress-compact' : '',
    size === 'large' ? 'candy-progress-large' : '',
  ].filter(Boolean).join(' ');
  const ariaProps = indeterminate
    ? { 'aria-busy': 'true' }
    : { 'aria-valuenow': Math.round(width), 'aria-valuemin': 0, 'aria-valuemax': 100 };

  return (
    <div className={classes} data-testid={testId} data-state={indeterminate ? 'indeterminate' : status}>
      {(label || (showPercent && !indeterminate && status !== 'idle')) && (
        <div className="candy-progress-label">
          <span>{label}</span>
          {showPercent && !indeterminate && status !== 'idle' && <span>{Math.round(width)}%</span>}
        </div>
      )}
      <div className="candy-progress-track" role="progressbar" aria-label={label || 'Progress'} {...ariaProps}>
        <div className="candy-progress-fill" style={{ width: `${width}%` }} />
      </div>
      {detail && <div className="candy-progress-detail" data-testid={`${testId}-detail`}>{detail}</div>}
      {status === 'error' && (
        <div className="candy-progress-error-box" role="alert">
          <span>{error || 'Something went wrong.'}</span>
          {onRetry && (
            <button type="button" className="candy-progress-retry" onClick={onRetry}>Retry</button>
          )}
        </div>
      )}
    </div>
  );
}
