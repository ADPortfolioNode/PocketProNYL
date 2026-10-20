import { isPredictionForGame } from './suggestionUtils';
import { ALL_GAMES_VALUE, resolveTuningMetrics } from './tuningView';

describe('game isolation helpers', () => {
  test('isPredictionForGame matches only the requested game', () => {
    expect(isPredictionForGame({ game: 'take5' }, 'take5')).toBe(true);
    expect(isPredictionForGame({ game: 'Take 5' }, 'take5')).toBe(true);
    expect(isPredictionForGame({ game: 'pick3' }, 'take5')).toBe(false);
    expect(isPredictionForGame({ predicted_numbers: [1] }, 'take5')).toBe(true);
    expect(isPredictionForGame(null, 'take5')).toBe(false);
  });

  test('resolveTuningMetrics never shows another game\'s latest metrics', () => {
    const take5 = { accuracy_percent: 40 };
    const status = {
      metrics_by_game: { take5 },
      tuning_metrics: take5,
      tuning_metrics_game: 'take5',
    };
    expect(resolveTuningMetrics(status, 'take5', 'take5')).toBe(take5);
    expect(resolveTuningMetrics(status, 'pick3', 'pick3')).toEqual({});
    expect(resolveTuningMetrics(status, 'take5', ALL_GAMES_VALUE)).toBe(take5);
    expect(resolveTuningMetrics({ tuning_metrics: take5, current_game: 'take5' }, 'pick3', 'pick3')).toEqual({});
    expect(resolveTuningMetrics({}, 'pick3', 'pick3')).toEqual({});
  });
});
