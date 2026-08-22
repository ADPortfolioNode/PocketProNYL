
import React, { useState, useEffect } from 'react';

const Header = ({ startupStatus }) => {
  const [showProgress, setShowProgress] = useState(true);

  useEffect(() => {
    if (startupStatus?.status === 'completed') {
      const timer = setTimeout(() => setShowProgress(false), 500);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [startupStatus?.status]);

  const getProgressPercentage = () => {
    if (!startupStatus) return 0;
    const progress = Number(startupStatus.progress || 0);
    const total = Number(startupStatus.total || 1);
    return Math.min((progress / total) * 100, 100);
  };

  const progressPercentage = getProgressPercentage();
  const isIngesting = startupStatus?.status === 'ingesting';
  const isCompleted = startupStatus?.status === 'completed';

  return (
    <div>
      {showProgress && startupStatus && (
        <div className="header-startup-strip py-2 mb-3">
          <div className="container">
            <div className="header-startup-meta small mb-2">
              <strong>Lottery Data Initialization</strong>
              {startupStatus.current_game && (
                <span className="ms-2">
                  • {startupStatus.current_game.toUpperCase()}
                  {startupStatus.progress > 0 && (
                    <span> ({startupStatus.progress.toFixed(1)}/{startupStatus.total})</span>
                  )}
                </span>
              )}
              {isCompleted && <span className="ms-2 text-success">✓ Complete</span>}
            </div>
            <div className="progress header-startup-progress" role="progressbar" aria-valuenow={progressPercentage} aria-valuemin="0" aria-valuemax="100">
              <progress
                className={`header-startup-progress-native ${isCompleted ? 'is-complete' : isIngesting ? 'is-active' : 'is-idle'}`}
                value={Math.max(0, Math.min(progressPercentage, 100))}
                max="100"
              />
            </div>
          </div>
        </div>
      )}

      <div className="container py-4">
        <div className="alert alert-info">
          <h4>Welcome to PocketPro:NYL</h4>
          <p>Use this app to suggest NY lottery numbers. Follow the workflow: 1. Ingest data, 2. Train the model, 3. Make suggestions.</p>
        </div>
        <h2>PocketPro:NYL Suggestion Dashboard</h2>
      </div>
    </div>
  );
};

export default Header;
