import React from 'react';
import './RetiredBadge.css';

export const RETIRED_LABEL = 'Retired: history only';

export const isRetiredGame = (entry) => Boolean(entry && (entry.retired || entry.status_label === RETIRED_LABEL));

const RetiredBadge = ({ date, className = '' }) => (
  <span
    className={`retired-badge ${className}`.trim()}
    title={date ? `Final draw ${date}. Kept for history and analysis.` : 'Kept for history and analysis.'}
    data-testid="retired-badge"
  >
    {RETIRED_LABEL}
  </span>
);

export default RetiredBadge;
