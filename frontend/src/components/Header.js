import React, { useState, useEffect } from 'react';

const Header = ({ startupStatus }) => {
  const [showProgress, setShowProgress] = useState(true);

  useEffect(() => {
    if (startupStatus?.status === 'completed') {
      const timer = setTimeout(() => setShowProgress(false), 2500);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [startupStatus?.status]);

  const progress = Number(startupStatus?.progress || 0);
  const total = Number(startupStatus?.total || 8) || 8;
  const percentFromApi = Number(startupStatus?.percent_complete);
  const progressPercentage = Number.isFinite(percentFromApi)
    ? Math.min(percentFromApi, 100)
    : Math.min((progress / total) * 100, 100);
  const isIngesting = ['ingesting', 'queued', 'pending'].includes(String(startupStatus?.status || '').toLowerCase());
  const isCompleted = startupStatus?.status === 'completed';
  const rows = Number(startupStatus?.current_game_rows_fetched || 0);
  const rowsTotal = Number(startupStatus?.current_game_rows_total || 0);

  if (!showProgress || !startupStatus) {
    return (
      <div className="container py-4">
        <div className="alert alert-info">
          <h4>Welcome to PocketPro:NYL</h4>
          <p>Workflow: ingest data, train a model, then generate suggestions.</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="header-startup-strip py-2 mb-3">
        <div className="container">
          <div className="header-startup-meta small mb-2">
            <strong>Ingestion</strong>
            {startupStatus.current_game && (
              <span className="ms-2">
                • {String(startupStatus.current_game).replace(/[_-]+/g, ' ').toUpperCase()}
                {rows > 0 && (
                  <span> — {rows.toLocaleString()}{rowsTotal ? ` / ${rowsTotal.toLocaleString()}` : ''} rows</span>
                )}
              </span>
            )}
            <span className="ms-2">{Math.round(progressPercentage)}% ({progress.toFixed(1)}/{total})</span>
            {isCompleted && <span className="ms-2 text-success">Complete</span>}
          </div>
          <div className="progress header-startup-progress" style={{ height: '0.65rem' }}>
            <div
              className={`progress-bar ${isCompleted ? 'bg-success' : 'progress-bar-striped progress-bar-animated bg-warning'}`}
              style={{ width: `${Math.max(progressPercentage, isIngesting && progressPercentage < 2 ? 4 : 0)}%` }}
              role="progressbar"
              aria-valuenow={progressPercentage}
              aria-valuemin="0"
              aria-valuemax="100"
            />
          </div>
        </div>
      </div>
      <div className="container py-4">
        <div className="alert alert-info">
          <h4>Welcome to PocketPro:NYL</h4>
          <p>Workflow: ingest data, train a model, then generate suggestions.</p>
        </div>
        <h2>PocketPro:NYL Suggestion Dashboard</h2>
      </div>
    </div>
  );
};

export default Header;
