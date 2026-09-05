import React from 'react';
import NextGameScheduleBanner from './NextGameScheduleBanner';
import './PredictionDisplay.css';

const hasBlendWeight = (value) => value !== undefined && value !== null && value !== '';

const PredictionDisplay = ({ prediction }) => {
  if (!prediction) {
    return null;
  }

  const session = Array.isArray(prediction.prediction_session)
    ? prediction.prediction_session
    : [];
  const hasSession = session.length > 0;

  const renderPredictionBlock = (predictionItem, keyPrefix) => {
    const formatted = predictionItem?.formatted_prediction || null;
    const mainNumbers = formatted?.main_numbers || predictionItem?.predicted_numbers || [];
    const bonusNumbers = formatted?.bonus_numbers || [];
    const mainLabel = formatted?.main_label || 'Numbers';
    const bonusLabel = formatted?.bonus_label || 'Bonus';
    const explicitBonusLabel = bonusNumbers.length > 1
      ? `Bonus Numbers (${bonusLabel})`
      : `Bonus Number (${bonusLabel})`;

    return (
      <>
        <div className="prediction-group">
          <div className="prediction-label">{mainLabel}</div>
          <div className="prediction-numbers">
            {mainNumbers.map((number, index) => (
              <div key={`${keyPrefix}-main-${index}`} className="prediction-number">
                {number}
              </div>
            ))}
          </div>
        </div>

        {bonusNumbers.length > 0 && (
          <div className="prediction-group bonus-group">
            <div className="prediction-label">{explicitBonusLabel}</div>
            <div className="prediction-numbers">
              {bonusNumbers.map((number, index) => (
                <div key={`${keyPrefix}-bonus-${index}`} className="prediction-number prediction-number-bonus">
                  {number}
                </div>
              ))}
            </div>
          </div>
        )}
      </>
    );
  };

  if (hasSession && session.length > 1) {
    // Show model metadata at the top if available
    const meta = prediction.model_metadata || {};
    return (
      <div className="prediction-display">
        <NextGameScheduleBanner prediction={prediction} includeDrawCount />
        <h6 className="prediction-header">Suggested Numbers ({session.length} Draws)</h6>
        <div className="text-muted mb-2">
          {prediction.predicted_for_date && (
            <span>Session Date: {prediction.predicted_for_date}</span>
          )}
          {meta.model_strategy && (
            <span style={{ marginLeft: 12 }}>Strategy: {meta.model_strategy}</span>
          )}
          {hasBlendWeight(meta.blend_weight) && (
            <span style={{ marginLeft: 12 }}>Blend: {meta.blend_weight}</span>
          )}
          {meta.highest_accuracy != null && (
            <span style={{ marginLeft: 12 }}>
              Highest accuracy: {(Number(meta.highest_accuracy) * 100).toFixed(2)}%
            </span>
          )}
        {meta.used_previous_training !== undefined && (
            <span style={{ marginLeft: 12 }}>PrevTrain: {meta.used_previous_training ? 'Yes' : 'No'}</span>
          )}
        </div>
        {session.map((draw) => (
          <div key={`draw-${draw.draw_index}`} className="mb-3">
            <div className="prediction-label mb-1 prediction-draw-header">
              <span className="prediction-draw-index">Draw {draw.draw_index}</span>
              {draw.prediction_date && (
                <span className="text-muted prediction-draw-date">
                  {draw.prediction_date}
                  {draw.draw_time ? ` at ${draw.draw_time}` : ''}
                  {draw.draw_session ? ` (${draw.draw_session})` : ''}
                  {draw.prediction_weekday ? ` (${draw.prediction_weekday})` : ''}
                  {draw.prediction_timezone ? ` [${draw.prediction_timezone}]` : ''}
                </span>
              )}
              {draw.spacing_from_previous_minutes != null && (
                <span className="text-muted prediction-draw-spacing">
                  {` +${draw.spacing_from_previous_minutes} min`}
                </span>
              )}
            </div>
            {renderPredictionBlock(draw, `draw-${draw.draw_index}`)}
          </div>
        ))}
      </div>
    );
  }

  const singlePrediction = hasSession ? session[0] : prediction;
  const singleNumbers = Array.isArray(singlePrediction?.predicted_numbers)
    ? singlePrediction.predicted_numbers
    : [];
  if (singleNumbers.length === 0) {
    if (prediction.message) {
      return (
        <div className="prediction-display">
          <NextGameScheduleBanner prediction={prediction} />
          <h6 className="prediction-header">Suggestion Status</h6>
          <div className="alert alert-info mb-0" role="status">
            {prediction.message}
          </div>
        </div>
      );
    }
    return null;
  }

  // Show model metadata for single prediction as well
  const meta = prediction.model_metadata || {};
  return (
    <div className="prediction-display">
      <NextGameScheduleBanner prediction={prediction} />
      <h6 className="prediction-header">Suggested Numbers</h6>
      <div className="text-muted mb-2">
        {singlePrediction.prediction_timezone && (
          <span>Timezone: {singlePrediction.prediction_timezone}</span>
        )}
        {meta.model_strategy && (
          <span style={{ marginLeft: 12 }}>Strategy: {meta.model_strategy}</span>
        )}
        {hasBlendWeight(meta.blend_weight) && (
          <span style={{ marginLeft: 12 }}>Blend: {meta.blend_weight}</span>
        )}
        {(meta.highest_accuracy != null || prediction.highest_accuracy != null) && (
          <span style={{ marginLeft: 12 }}>
            Highest accuracy: {(Number(meta.highest_accuracy ?? prediction.highest_accuracy) * 100).toFixed(2)}%
          </span>
        )}
        {meta.used_previous_training !== undefined && (
          <span style={{ marginLeft: 12 }}>PrevTrain: {meta.used_previous_training ? 'Yes' : 'No'}</span>
        )}
      </div>
      {renderPredictionBlock(singlePrediction, 'single')}
    </div>
  );
};

export default PredictionDisplay;
