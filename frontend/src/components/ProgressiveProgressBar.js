import React from 'react';

/**
 * ProgressiveProgressBar Component
 * Shows progress with detailed metadata including:
 * - Current/Total counts
 * - Percentage
 * - Rate (items/sec)
 * - ETA (estimated time remaining)
 * - Status (active/completed/error)
 */
export default function ProgressiveProgressBar({
  current = 0,
  total = 100,
  status = 'idle', // idle, active, completed, error
  label = 'Progress',
  labelColor = null,
  showMetadata = true,
  rate = null, // items per second
  startTime = null, // timestamp when started
  colorScheme = 'primary' // primary, success, warning, danger
}) {
  const percentage = total > 0 ? Math.round((current / total) * 100) : 0;
  const clampedPercentage = Math.max(0, Math.min(percentage, 100));
  
  // Calculate rate if not provided
  let calculatedRate = rate;
  let eta = null;
  
  if (startTime && current > 0 && total > 0) {
    const elapsed = (Date.now() - startTime) / 1000; // seconds
    calculatedRate = calculatedRate || (current / elapsed);
    const remaining = total - current;
    eta = remaining / calculatedRate; // seconds
  }

  // Format ETA
  const formatETA = (seconds) => {
    if (!seconds || seconds <= 0) return 'calculating...';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${Math.round(seconds / 3600)}h`;
  };

  // Format rate
  const formatRate = (r) => {
    if (!r || r <= 0) return '0';
    if (r < 1) return r.toFixed(2);
    if (r < 100) return r.toFixed(1);
    return Math.round(r).toLocaleString();
  };

  // Determine color based on status
  const getBarColor = () => {
    switch (status) {
      case 'completed': return 'bg-success';
      case 'error': return 'bg-danger';
      case 'active': return `bg-${colorScheme}`;
      default: return 'bg-secondary';
    }
  };

  const getStatusIcon = () => {
    switch (status) {
      case 'completed': return '✓';
      case 'error': return '✗';
      case 'active': return '⟳';
      default: return '○';
    }
  };

  return (
    <div className="progressive-progress mb-3">
      {/* Header with label and status */}
      <div className="progressive-progress-header">
        <span className={`progressive-progress-label ${labelColor ? `progressive-progress-label-${colorScheme}` : ''}`}>
          {getStatusIcon()} {label}
        </span>
        <span className="progressive-progress-percent">
          {clampedPercentage}%
        </span>
      </div>

      {/* Progress Bar */}
      <div className="progressive-progress-track">
        <progress
          className={`progressive-progress-native ${getBarColor()} ${status === 'active' ? 'is-active' : ''}`}
          value={clampedPercentage}
          max="100"
        />
        {clampedPercentage > 15 && (
          <span className="progressive-progress-overlay">{clampedPercentage}%</span>
        )}
      </div>

      {/* Metadata */}
      {showMetadata && (
        <div className="progressive-progress-meta">
          <div>
            <strong>Items:</strong> {current.toLocaleString()} / {total.toLocaleString()}
          </div>
          {calculatedRate && status === 'active' && (
            <>
              <div>
                <strong>Rate:</strong> {formatRate(calculatedRate)}/s
              </div>
              {eta && (
                <div>
                  <strong>ETA:</strong> {formatETA(eta)}
                </div>
              )}
            </>
          )}
          {status === 'completed' && startTime && (
            <div>
              <strong>Duration:</strong> {formatETA((Date.now() - startTime) / 1000)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
