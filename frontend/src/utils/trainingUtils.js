/** Shared training payload + result formatting for PocketPro:NYL dashboards. */

import { parseExperimentTimestampMs } from './timestampUtils';

export const BASE_MODEL_TYPE = 'Random Forest Regressor';

const STRATEGY_LABELS = {
  single: 'Single model',
  ensemble: 'Blended with prior model',
  ensemble_top3: 'Top-3 ensemble',
};

export function formatModelStrategyLabel(strategy) {
  if (!strategy) return null;
  const key = String(strategy).toLowerCase();
  return STRATEGY_LABELS[key] || String(strategy);
}

export function formatModelTypeLabel({ strategy, blendWeight } = {}) {
  const strategyLabel = formatModelStrategyLabel(strategy);
  if (!strategyLabel) return BASE_MODEL_TYPE;
  if (strategy === 'ensemble' && blendWeight != null && Number.isFinite(Number(blendWeight))) {
    return `${BASE_MODEL_TYPE} (${strategyLabel}, weight ${Number(blendWeight).toFixed(2)})`;
  }
  return `${BASE_MODEL_TYPE} (${strategyLabel})`;
}

export function clampTrainValue(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

export function normalizeTrainSizeFraction(value, fallback = 0.25) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  const asFraction = n > 1 ? n / 100 : n;
  return clampTrainValue(asFraction, 0.1, 0.5, fallback);
}

export function formatTrainSizePercent(testSize) {
  return `${Math.round(normalizeTrainSizeFraction(testSize) * 100)}%`;
}

export function normalizeNEstimators(value, fallback = 250) {
  return Math.round(clampTrainValue(value, 50, 600, fallback));
}

export function normalizeMaxDepth(value, fallback = 18) {
  return Math.round(clampTrainValue(value, 4, 32, fallback));
}

export function normalizeMaxIterations(value, fallback = 40) {
  return Math.round(clampTrainValue(value, 1, 100, fallback));
}

export function buildTrainRequestBody(game, trainParams) {
  const trainingTarget = clampTrainValue(trainParams.targetAccuracy ?? 0.9, 0.5, 0.99, 0.9);
  const body = {
    game,
    target_accuracy: trainingTarget,
    max_iterations: normalizeMaxIterations(trainParams.maxIterations, 40),
    train_size: normalizeTrainSizeFraction(trainParams.testSize, 0.25),
    n_estimators: normalizeNEstimators(trainParams.nEstimators, 250),
    max_depth: normalizeMaxDepth(trainParams.maxDepth, 18),
    random_state: Math.round(clampTrainValue(trainParams.randomState, 0, 2_147_483_647, 42)),
    window_size: Math.round(clampTrainValue(trainParams.windowSize, 1, 8, 3)),
    auto_tune: Boolean(trainParams.autoTune ?? true),
  };
  if (trainParams.blendStep != null && Number.isFinite(Number(trainParams.blendStep))) {
    body.blend_step = clampTrainValue(trainParams.blendStep, 0.01, 0.5, 0.05);
  }
  return body;
}

export function formatTrainingSuccessMessage(game, data) {
  const recordScore = data.record_accuracy ?? data.highest_accuracy ?? data.baseline_accuracy;
  const trainScore = recordScore ?? data.score ?? data.accuracy ?? data.final_accuracy;
  const scoreText = trainScore != null ? `${(Number(trainScore) * 100).toFixed(2)}%` : 'N/A';
  const strategy = data.model_strategy;
  const prevAcc = data.previous_accuracy ?? data.baseline_accuracy ?? recordScore;
  const candAcc = data.candidate_accuracy ?? data.new_accuracy;
  const trainTarget = data.training_target ?? data.target_accuracy;
  let learningNote = '';
  if (data.retained_previous_model && recordScore != null && candAcc != null) {
    learningNote = ` Record ${(Number(recordScore) * 100).toFixed(2)}% kept (new run ${(Number(candAcc) * 100).toFixed(2)}% did not beat the floor).`;
  } else if (data.retained_previous_model && prevAcc != null && candAcc != null) {
    learningNote = ` Record ${(Number(prevAcc) * 100).toFixed(2)}% kept (new run ${(Number(candAcc) * 100).toFixed(2)}%).`;
  } else if (strategy === 'ensemble' && data.blend_weight != null) {
    learningNote = ` Ensemble blend (weight ${Number(data.blend_weight).toFixed(2)}) improved on prior model.`;
  } else if (prevAcc != null && trainScore != null && trainScore > prevAcc) {
    learningNote = ` Improved from prior ${(prevAcc * 100).toFixed(2)}%.`;
  }
  const targetNote = trainTarget != null ? ` Target: ${(Number(trainTarget) * 100).toFixed(2)}%.` : '';
  return `Training completed successfully for ${String(game).toUpperCase()}! Experiment ID: ${data.experiment_id}, Accuracy: ${scoreText}.${targetNote}${learningNote}`;
}

export function isTrainSuccessStatus(status) {
  const normalized = String(status || '').toLowerCase();
  return normalized === 'completed' || normalized === 'success';
}

export function experimentAccuracy(exp) {
  for (const key of ['highest_accuracy', 'final_accuracy', 'accuracy', 'score']) {
    const value = Number(exp?.[key]);
    if (Number.isFinite(value)) return value;
  }
  return null;
}

export function isCompletedTrainingExperiment(exp) {
  const expType = String(exp?.type || '').toLowerCase();
  const expStatus = String(exp?.status || '').toLowerCase();
  return expType === 'training' && (expStatus === 'completed' || expStatus === 'success');
}

export function pickHighestAccuracyExperiment(experiments, game = null) {
  const gameKey = game ? String(game).toLowerCase() : null;
  const rows = (experiments || []).filter((exp) => {
    if (!isCompletedTrainingExperiment(exp)) return false;
    if (gameKey && String(exp.game || '').toLowerCase() !== gameKey) return false;
    return experimentAccuracy(exp) != null;
  });
  if (!rows.length) return null;
  rows.sort((a, b) => {
    const accDiff = (experimentAccuracy(b) ?? -1) - (experimentAccuracy(a) ?? -1);
    if (accDiff !== 0) return accDiff;
    return (parseExperimentTimestampMs(b) ?? 0) - (parseExperimentTimestampMs(a) ?? 0);
  });
  return rows[0];
}

export function mapApiDefaultsToTrainParams(defaults = {}, prev = {}) {
  // Use optimized defaults if available, otherwise use regular defaults
  const optimizedDefaults = defaults.optimized_defaults || {};
  const effectiveDefaults = defaults.optimization_applied ? optimizedDefaults : defaults;
  
  return {
    testSize: normalizeTrainSizeFraction(effectiveDefaults.train_size ?? prev.testSize ?? 0.25, 0.25),
    randomState: effectiveDefaults.random_state ?? prev.randomState ?? 42,
    nEstimators: normalizeNEstimators(effectiveDefaults.n_estimators ?? prev.nEstimators ?? 250),
    maxDepth: normalizeMaxDepth(effectiveDefaults.max_depth ?? prev.maxDepth ?? 18),
    maxIterations: normalizeMaxIterations(effectiveDefaults.max_iterations ?? prev.maxIterations ?? 40),
    targetAccuracy: effectiveDefaults.target_accuracy ?? prev.targetAccuracy ?? 0.90,
    windowSize: effectiveDefaults.window_size ?? prev.windowSize ?? 3,
    autoTune: effectiveDefaults.auto_tune ?? prev.autoTune ?? true,
    blendStep: effectiveDefaults.blend_step ?? prev.blendStep ?? 0.05,
    // Include optimization metadata
    optimizationApplied: defaults.optimization_applied || false,
    optimizationReasoning: effectiveDefaults.reasoning || '',
  };
}

export function experimentToTrainParams(experiment) {
  if (!experiment) return {};
  const params = experiment.training_params || experiment.best_training_params || experiment;
  return mapApiDefaultsToTrainParams({
    train_size: params.train_size,
    random_state: params.random_state,
    n_estimators: params.n_estimators,
    max_depth: params.max_depth,
    max_iterations: params.max_iterations,
    target_accuracy: params.target_accuracy ?? params.training_target,
    window_size: params.window_size,
    auto_tune: params.auto_tune,
    blend_step: params.blend_step,
  });
}

export function resolveBestTrainParams({
  defaults = {},
  recreateDefaults = {},
  bestTrainingParams = {},
  experiment = null,
} = {}) {
  const fromApi = mapApiDefaultsToTrainParams({
    ...defaults,
    ...recreateDefaults,
    ...bestTrainingParams,
  });
  const fromExperiment = experimentToTrainParams(experiment);
  return { ...fromApi, ...fromExperiment };
}

export function trainingDataBadgeClass(status) {
  switch (String(status || '').toLowerCase()) {
    case 'never_trained':
      return 'bg-warning text-dark';
    case 'stale':
      return 'bg-info text-dark';
    case 'current':
      return 'bg-success';
    default:
      return 'bg-secondary';
  }
}

export function trainingDataStatusLabel(status, details = {}) {
  const key = String(status || '').toLowerCase();
  const current = Number(details.current_record_count ?? 0);
  const last = Number(details.last_trained_record_count ?? 0);
  const newDraws = Number(details.new_draws ?? 0);

  if (key === 'never_trained') {
    return current > 0
      ? `Never trained (${current.toLocaleString()} draws waiting)`
      : 'Never trained';
  }
  if (key === 'stale') {
    if (newDraws > 0) {
      return `${newDraws.toLocaleString()} new draw(s) (${last.toLocaleString()} → ${current.toLocaleString()})`;
    }
    return `Dataset changed (${last.toLocaleString()} → ${current.toLocaleString()})`;
  }
  if (key === 'current') {
    return `Up to date (${current.toLocaleString()} draws)`;
  }
  if (key === 'no_data') {
    return 'No ingested data';
  }
  return details.message || 'Unknown';
}

export function gameNeedsTraining(trainingData) {
  return Boolean(trainingData?.has_untrained_data);
}

export function formatTrainingErrorMessage(error, formatApiError) {
  const status = error?.response?.status;
  const base = formatApiError(error, 'Training request failed.');
  if (status === 502 || status === 503 || /502 bad gateway/i.test(base) || /503 service unavailable/i.test(base)) {
    return (
      'Backend unavailable during training (HTTP '
      + (status || '502')
      + '). The API container may have restarted or run out of memory on large games like Powerball. '
      + 'Wait 30s, check docker logs for pocketpro_nyl_backend, then retry with Target Accuracy 85% to 90%, '
      + 'Max Iterations 10 to 12, N Estimators 150, and Auto-tune off. '
      + 'Also confirm a completed experiment was not already saved.'
    );
  }
  if (status === 504 || /504 gateway timeout/i.test(base) || /timeout/i.test(base)) {
    return (
      'Gateway timeout while waiting for training to finish. Training can take several minutes — '
      + 'the job may still be running on the server. Check Completed Training Experiments in a minute, '
      + 'or retry with fewer Max Iterations / disable Auto-tune train split.'
    );
  }
  return base;
}
