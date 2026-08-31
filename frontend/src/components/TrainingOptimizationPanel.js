import React, { useState, useEffect } from 'react';
import './TrainingOptimizationPanel.css';

export default function TrainingOptimizationPanel({ selectedGame, apiBase }) {
  const [optimizationData, setOptimizationData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    if (selectedGame) {
      fetchOptimizationData();
    } else {
      setOptimizationData(null);
    }
  }, [selectedGame]);

  const fetchOptimizationData = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/api/train_optimization/${selectedGame}`);
      if (!response.ok) throw new Error('Failed to fetch optimization data');
      const data = await response.json();
      setOptimizationData(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (!selectedGame) {
    return (
      <div className="optimization-panel">
        <div className="optimization-placeholder">
          <p>Select a game to see optimized training parameters</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="optimization-panel">
        <div className="optimization-loading">
          <div className="spinner"></div>
          <p>Loading optimization data for {selectedGame}...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="optimization-panel">
        <div className="optimization-error">
          <p>Error loading optimization data: {error}</p>
          <button onClick={fetchOptimizationData} className="retry-btn">Retry</button>
        </div>
      </div>
    );
  }

  if (!optimizationData) {
    return null;
  }

  const { optimized_defaults, comparison } = optimizationData;
  const reasoning = optimized_defaults.reasoning || '';

  return (
    <div className="optimization-panel">
      <div className="optimization-header">
        <h3>⚡ Optimized Training Parameters</h3>
        <span className="optimization-badge">Game-Specific</span>
      </div>

      <div className="optimization-reasoning">
        <strong>Why these settings?</strong>
        <p>{reasoning}</p>
      </div>

      <div className="optimization-params">
        <div className="param-group">
          <div className="param-item">
            <label>Target Accuracy:</label>
            <span className="param-value highlight">{(optimized_defaults.target_accuracy * 100).toFixed(1)}%</span>
          </div>
          <div className="param-item">
            <label>Max Iterations:</label>
            <span className="param-value">{optimized_defaults.max_iterations}</span>
          </div>
          <div className="param-item">
            <label>Train Size:</label>
            <span className="param-value">{(optimized_defaults.train_size * 100).toFixed(0)}%</span>
          </div>
        </div>

        <div className="param-group">
          <div className="param-item">
            <label>N Estimators:</label>
            <span className="param-value">{optimized_defaults.n_estimators}</span>
          </div>
          <div className="param-item">
            <label>Max Depth:</label>
            <span className="param-value">{optimized_defaults.max_depth}</span>
          </div>
          <div className="param-item">
            <label>Window Size:</label>
            <span className="param-value">{optimized_defaults.window_size}</span>
          </div>
        </div>

        <div className="param-group">
          <div className="param-item">
            <label>Auto Tune:</label>
            <span className="param-value">{optimized_defaults.auto_tune ? 'Enabled' : 'Disabled'}</span>
          </div>
          <div className="param-item">
            <label>Blend Step:</label>
            <span className="param-value">{optimized_defaults.blend_step.toFixed(3)}</span>
          </div>
          {optimized_defaults.data_limit > 0 && (
            <div className="param-item">
              <label>Data Limit:</label>
              <span className="param-value">{optimized_defaults.data_limit.toLocaleString()}</span>
            </div>
          )}
        </div>
      </div>

      {comparison && comparison.comparison && (
        <div className="optimization-comparison">
          <button 
            className="comparison-toggle" 
            onClick={() => setShowDetails(!showDetails)}
          >
            {showDetails ? '▼' : '▶'} Compare with Generic Defaults
          </button>
          
          {showDetails && (
            <div className="comparison-details">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th>Parameter</th>
                    <th>Optimized</th>
                    <th>Generic</th>
                    <th>Difference</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(comparison.comparison).map(([param, data]) => (
                    <tr key={param}>
                      <td className="param-name">{param}</td>
                      <td className="param-optimized">{data.game_specific}</td>
                      <td className="param-generic">{data.generic}</td>
                      <td className={`param-diff ${data.adjustment}`}>
                        {data.adjustment} ({data.difference_pct}%)
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="optimization-info">
        <p className="info-text">
          💡 These parameters are automatically optimized based on game characteristics including 
          draw frequency, number range complexity, and historical data patterns. 
          You can override them manually if needed.
        </p>
      </div>
    </div>
  );
}