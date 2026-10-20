export function hasSuggestionNumbers(payload) {
  if (!payload || typeof payload !== 'object') {
    return false;
  }

  const topLevel = Array.isArray(payload.predicted_numbers) && payload.predicted_numbers.length > 0;
  if (topLevel) {
    return true;
  }

  const session = Array.isArray(payload.prediction_session) ? payload.prediction_session : [];
  return session.some(
    (draw) => Array.isArray(draw?.predicted_numbers) && draw.predicted_numbers.length > 0,
  );
}

export function getNextGameSchedule(prediction = {}) {
  const session = Array.isArray(prediction.prediction_session) ? prediction.prediction_session : [];
  const firstDraw = session[0] || {};
  const date = prediction.prediction_date
    || prediction.next_draw_date
    || prediction.predicted_for_date
    || firstDraw.prediction_date
    || null;
  const weekday = prediction.prediction_weekday
    || firstDraw.prediction_weekday
    || null;
  const timezone = prediction.prediction_timezone
    || firstDraw.prediction_timezone
    || null;
  const drawCount = prediction.session_draw_count
    ?? (session.length > 0 ? session.length : null);

  if (!date && !weekday) {
    return null;
  }

  return { date, weekday, timezone, drawCount };
}

export function formatNextGameDateLabel(prediction = {}, options = {}) {
  const schedule = getNextGameSchedule(prediction);
  if (!schedule) {
    return '';
  }

  const {
    prefix = 'Next game',
    includeDrawCount = false,
    includeGame = false,
  } = options;

  let when = '';
  if (schedule.weekday && schedule.date) {
    when = `${schedule.weekday}, ${schedule.date}`;
  } else if (schedule.date) {
    when = schedule.date;
  } else if (schedule.weekday) {
    when = schedule.weekday;
  }

  const gamePrefix = includeGame && prediction.game
    ? `${String(prediction.game).toUpperCase()} — `
    : '';

  let label = `${gamePrefix}${prefix}: ${when}`;
  if (schedule.timezone) {
    label += ` (${schedule.timezone})`;
  }
  if (includeDrawCount && Number(schedule.drawCount) > 1) {
    label += ` · ${schedule.drawCount} draws`;
  }
  return label;
}

export function hasSuggestionResponse(payload) {
  if (!payload || typeof payload !== 'object') {
    return false;
  }

  if (payload.status === 'error' || payload.error) {
    return false;
  }

  return hasSuggestionNumbers(payload) || Boolean((payload.message || '').trim());
}

export function formatSuggestionStatusLine(payload) {
  if (!payload) {
    return 'Suggestion finished.';
  }

  if (hasSuggestionNumbers(payload)) {
    const scheduleLine = formatNextGameDateLabel(payload, { includeDrawCount: true });
    const numbers = Array.isArray(payload.predicted_numbers) && payload.predicted_numbers.length > 0
      ? payload.predicted_numbers.join(', ')
      : null;
    if (numbers) {
      return scheduleLine
        ? `${scheduleLine} — ${numbers}`
        : `Suggestion ready for ${payload.game || 'game'}: ${numbers}`;
    }
    const sessionCount = Array.isArray(payload.prediction_session) ? payload.prediction_session.length : 0;
    return scheduleLine
      ? `${scheduleLine} (${sessionCount} draw${sessionCount === 1 ? '' : 's'})`
      : `Suggestion ready for ${payload.game || 'game'} (${sessionCount} draw${sessionCount === 1 ? '' : 's'}).`;
  }

  if (payload.message) {
    return payload.message;
  }

  return 'Suggestion finished with no numbers for the current schedule.';
}
function normalizeGameKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

// True when a suggestion payload belongs to `game` (or names no game at all).
export function isPredictionForGame(payload, game) {
  if (!payload || typeof payload !== 'object') return false;
  if (!payload.game || !game) return true;
  return normalizeGameKey(payload.game) === normalizeGameKey(game);
}
