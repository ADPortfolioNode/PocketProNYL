import React, { useState } from 'react';
import RetiredBadge, { RETIRED_LABEL, isRetiredGame } from './RetiredBadge';

const GameSelector = ({
  games = [],
  onGameSelect,
  includeAllOption = false,
  allOptionValue = '__all_games__',
  allOptionLabel = 'All Games'
}) => {
  // Support both string keys and dynamic catalog objects from /api/games
  const isObjectList = games.length > 0 && typeof games[0] === 'object';
  const [selected, setSelected] = useState('');
  const keyOf = (game) => game.id || game.key || game.name;
  const selectedEntry = isObjectList ? games.find((game) => keyOf(game) === selected) : null;
  const labelOf = (game) => {
    const name = game.name || game.title || game.id;
    const base = game.draw_count ? `${name} (${Number(game.draw_count).toLocaleString()} draws)` : name;
    return isRetiredGame(game) ? `${base} · ${RETIRED_LABEL}` : base;
  };
  return (
    <>
    <select
      className="form-select mb-2"
      onChange={(e) => {
        setSelected(e.target.value);
        if (onGameSelect) onGameSelect(e.target.value);
      }}
    >
      <option value="">Select a game</option>
      {includeAllOption && <option value={allOptionValue}>{allOptionLabel}</option>}
      {games.map((game) => (
        isObjectList ? (
          <option key={keyOf(game)} value={keyOf(game)}>
            {labelOf(game)}
          </option>
        ) : (
          <option key={game} value={game}>{game}</option>
        )
      ))}
    </select>
    {isRetiredGame(selectedEntry) && (
      <div className="mb-2"><RetiredBadge date={selectedEntry.retired_date} className="ms-0" /></div>
    )}
    </>
  );
};

export default GameSelector;
