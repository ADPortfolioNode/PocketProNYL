import React, { useEffect } from 'react';

/**
 * ExpandableCard — click or auto-focus to maximize full width in the dashboard grid.
 */
function formatMetadataValue(value) {
  if (value === 0) return '0';
  if (value == null) return '—';
  const text = String(value).trim();
  return text || '—';
}

export default function ExpandableCard({
  title,
  children,
  metadata = {},
  statusBadge,
  isActive = false,
  cardKey = null,
  focusedCard = null,
  onToggle,
  className = '',
  neonBorder = false,
}) {
  const expanded = Boolean(cardKey) && focusedCard === cardKey;

  useEffect(() => {
    if (!isActive || !cardKey || expanded) {
      return;
    }
    onToggle?.(true);
  }, [isActive, cardKey, expanded, onToggle]);

  const handleToggle = () => {
    onToggle?.(!expanded);
  };

  const handleHeaderKeyDown = (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      handleToggle();
    }
  };

  const isMinimized = !expanded;

  const cardClasses = [
    'card',
    'p-3',
    'mb-3',
    isMinimized ? '' : 'h-100',
    'shadow-lg',
    'expandable-card',
    'focus-card',
    neonBorder ? 'border-neon' : '',
    expanded ? 'card-selected card-maximized is-focused' : 'is-minimized',
    className,
  ].filter(Boolean).join(' ');

  const headerClasses = expanded ? 'bg-primary text-white' : '';

  return (
    <div className={cardClasses}>
      <div
        className={`card-header expandable-card-header ${headerClasses} ${expanded ? 'is-expanded' : ''}`}
        onClick={handleToggle}
        onKeyDown={handleHeaderKeyDown}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
      >
        <h5 className={`${neonBorder ? 'text-neon' : ''} mb-0`}>
          {title}
        </h5>
        <div className="expandable-card-actions">
          {statusBadge && <span>{statusBadge}</span>}
          {isActive && (
            <span className="badge bg-warning text-dark">
              Active
            </span>
          )}
          <button
            type="button"
            className="btn btn-sm btn-outline-secondary"
            onClick={(e) => {
              e.stopPropagation();
              handleToggle();
            }}
            aria-label={expanded ? 'Collapse card' : 'Maximize card'}
          >
            {expanded ? '▼' : '▶'}
          </button>
        </div>
      </div>

      <div className="card-body expandable-card-body">
          {expanded && Object.keys(metadata).length > 0 && (
            <div className="expandable-metadata mb-3">
              <h6 className="mb-2">Metadata</h6>
              <div className="expandable-metadata-grid">
                {Object.entries(metadata).map(([key, value]) => (
                  <div key={key} className="expandable-metadata-row">
                    <span className="expandable-metadata-key">{key}:</span>
                    <span className="expandable-metadata-value">
                      {formatMetadataValue(value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {children}
        </div>
    </div>
  );
}