export const ALL_GAMES_VALUE = '__all_games__';

// Metrics to show for `game`. The status payload keeps one entry per game in
// `metrics_by_game`; `tuning_metrics` only mirrors the most recently tuned game,
// so it is used for a specific game only when it is known to be that game's.
export function resolveTuningMetrics(status, game, selectedGame) {
  const byGame = status?.metrics_by_game || {};
  if (game && byGame[game]) return byGame[game];
  const latest = status?.tuning_metrics;
  if (!latest) return {};
  if (!game || !selectedGame || selectedGame === ALL_GAMES_VALUE) return latest;
  const owner = status?.tuning_metrics_game
    || status?.latest_result?.game
    || status?.current_game
    || '';
  return owner && owner === game ? latest : {};
}
