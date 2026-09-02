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
  const isCompleted = startupStatus?.status === 'completed';
  const rows = Number(startupStatus?.current_game_rows_fetched || 0);
  const rowsTotal = Number(startupStatus?.current_game_rows_total || 0);
  const game = startupStatus?.current_game
    ? String(startupStatus.current_game).replace(/[_-]+/g, ' ').toUpperCase()
    : '';

  return (
    <header className="magazine-header">
      <div className="magazine-header__inner">
        <a className="magazine-brand" href="/">
          Pocket<span>Pro</span>:NYL
        </a>
        {showProgress && startupStatus ? (
          <div className="magazine-ingest" aria-label="Ingestion progress">
            <div className="meta">
              {isCompleted ? 'Ready' : 'Ingesting'}
              {game ? ` · ${game}` : ''}
              {rows > 0 ? ` · ${rows.toLocaleString()}${rowsTotal ? ` / ${rowsTotal.toLocaleString()}` : ''}` : ''}
              {' · '}
              {Math.round(progressPercentage)}%
            </div>
            <div className="magazine-progress">
              <span style={{ width: `${Math.max(progressPercentage, 2)}%` }} />
            </div>
          </div>
        ) : (
          <span className="magazine-badge">Ops desk</span>
        )}
      </div>
    </header>
  );
};

export default Header;
