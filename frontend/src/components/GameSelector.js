import React from 'react';

const GameSelector = ({
  games = [],
  onGameSelect,
  includeAllOption = false,
  allOptionValue = '__all_games__',
  allOptionLabel = 'All Games'
}) => {
  // Support both string keys and dynamic catalog objects from /api/games
  const isObjectList = games.length > 0 && typeof games[0] === 'object';
  return (
    <select className="form-select mb-2" onChange={(e) => onGameSelect(e.target.value)}>
      <option value="">Select a game</option>
      {includeAllOption && <option value={allOptionValue}>{allOptionLabel}</option>}
      {games.map((game) => (
        isObjectList ? (
          <option key={game.id || game.key || game.name} value={game.id || game.key || game.name}>
            {game.draw_count
              ? `${game.name || game.title || game.id} (${Number(game.draw_count).toLocaleString()} draws)`
              : (game.name || game.title || game.id)}
          </option>
        ) : (
          <option key={game} value={game}>{game}</option>
        )
      ))}
    </select>
  );
};

export default GameSelector;
