# PocketProNYGames Training Profiles

## Overview

Per-game profiles are applied when the training API omits a parameter. Profiles bound model capacity (shallower trees, fewer estimators and attempts) and reserve a larger chronological validation share. They are starting points, not evidence that any lottery outcome is predictable.

Draws are random. Training can improve backtest metrics without creating a real predictive edge, so treat accuracy targets as soft stopping goals rather than guarantees.

## How It Works

### Game Analysis Factors

The optimizer analyzes each game based on:

1. **Game Complexity**: Calculated from combination space, bonus numbers, and uniqueness requirements
2. **Data Volume Potential**: Based on draw frequency (daily, weekly, high-frequency games)
3. **Pattern Complexity**: Derived from number ranges and selection counts
4. **Historical Performance**: Incorporates previous training results and accuracy metrics

### Profile Approach

Profiles are maintained in `backend/utils/training_defaults.py`. Current profiles use a maximum tree depth of 12, at most 200 estimators, at most 18 attempts, and a train split of at least 30% (the remaining chronological rows are used for validation). Individual games use lower capacity where draw cadence or dataset size warrants it. The train endpoint accepts explicit parameter overrides; omitted values resolve to that game's profile and then its saved per-game recreation settings.

Training accepts only improving validation rounds and stops when the target is reached or improvement patience is exhausted. Results report validation and untouched holdout scores separately. A validation-to-holdout gap of 2.0 percentage points is the overfit warning boundary. The final holdout is evaluated once after selection; it is deliberately not used to choose settings, because repeatedly tuning against it would itself overfit the holdout.

The dashboard shows live validation round, evaluated draw count and provisional validation score. At completion it shows validation, untouched holdout and whether the overfit boundary was reached.

## API Integration

### New Endpoints

#### Get Game-Specific Settings
```http
GET /api/train_settings/{game}
```

Returns optimized defaults for the specific game:
```json
{
  "game": "powerball",
  "defaults": {
    "target_accuracy": 0.92,
    "max_iterations": 18,
    "train_size": 0.30,
    "n_estimators": 180,
    "max_depth": 12,
    "window_size": 5,
    "auto_tune": true,
    "blend_step": 0.04
  },
  "optimized_defaults": {
    "target_accuracy": 0.92,
    "max_iterations": 18,
    "train_size": 0.30,
    "n_estimators": 180,
    "max_depth": 12,
    "window_size": 5,
    "auto_tune": true,
    "blend_step": 0.04,
    "data_limit": 0,
    "reasoning": "Powerball has high complexity with large number space..."
  },
  "optimization_applied": true,
  "optimization_reasoning": "Powerball has high complexity..."
}
```

#### Get Optimization Details
```http
GET /api/train_optimization/{game}
```

Returns detailed comparison between game-specific and generic defaults:
```json
{
  "game": "powerball",
  "optimized_defaults": { /* ... */ },
  "comparison": {
    "target_accuracy": {
      "game_specific": 0.92,
      "generic": 0.90,
      "difference_pct": 2.2,
      "adjustment": "higher"
    },
    "max_iterations": {
      "game_specific": 50,
      "generic": 40,
      "difference_pct": 25.0,
      "adjustment": "higher"
    }
    // ... more parameters
  }
}
```

## Frontend Integration

### New Component: TrainingOptimizationPanel

The frontend includes a new component that displays optimized parameters:

```javascript
import TrainingOptimizationPanel from './components/TrainingOptimizationPanel';

<TrainingOptimizationPanel 
  selectedGame={selectedGame} 
  apiBase={apiBase} 
/>
```

**Features**:
- Visual display of game-specific parameters
- Optimization reasoning explanation
- Comparison with generic defaults (expandable)
- Automatic updates when game selection changes
- Loading and error states

### Updated Training Utilities

The `trainingUtils.js` has been updated to:
- Automatically use optimized defaults when available
- Include optimization metadata in training parameters
- Maintain backward compatibility with existing code

```javascript
const params = mapApiDefaultsToTrainParams(defaults, prevParams);
// params now includes:
// - optimizationApplied: boolean
// - optimizationReasoning: string
```

## Usage Examples

### Automatic Application

When a user selects a game in the training section, optimized defaults are automatically applied:

```javascript
// Fetch game-specific settings
const response = await fetch(`${apiBase}/api/train_settings/${game}`);
const data = await response.json();

// Training form automatically uses optimized defaults
const trainParams = mapApiDefaultsToTrainParams(data.defaults);
```

### Manual Override

Users can still manually override parameters:

```javascript
const customParams = {
  ...optimizedDefaults,
  target_accuracy: 0.95, // Manual override
  max_iterations: 60     // Manual override
};
```

### Comparison Analysis

To understand why specific parameters were chosen:

```javascript
const comparison = await fetch(`${apiBase}/api/train_optimization/${game}`);
const data = await comparison.json();

// data.comparison shows detailed parameter adjustments
// data.optimized_defaults.reasoning explains the optimization logic
```

## Performance and Limitations

The bounded profiles are intended to reduce unnecessary capacity and overfitting risk. They do not guarantee shorter runs or higher accuracy; runtime depends on data size and hardware. Because lottery draws are random, a better historical score does not establish a future edge.

## Configuration

### Environment Variables

The optimization system respects existing environment variables:

```bash
# Override optimization if needed
TRAIN_TARGET_ACCURACY=0.95
TRAIN_MAX_ATTEMPTS=60
TRAIN_SIZE=0.30
```

### Custom Game Optimization

To add optimization for a new game:

```python
@staticmethod
def _optimize_for_custom_game() -> Dict[str, Any]:
    return {
        "target_accuracy": 0.90,
        "max_iterations": 40,
        # ... other parameters
        "reasoning": "Custom game optimization logic"
    }

# Add to game_optimizers dictionary
game_optimizers = {
    # ... existing games
    "custom_game": cls._optimize_for_custom_game,
}
```

## Monitoring and Validation

### Validation Metrics

Monitor the effectiveness of optimizations:

```python
# Compare optimized vs generic training results
optimized_accuracy = experiment.highest_accuracy
generic_accuracy = baseline_experiment.highest_accuracy
improvement = (optimized_accuracy - generic_accuracy) / generic_accuracy
```

### A/B Testing

To validate optimization effectiveness:

1. Train with generic defaults
2. Train with optimized defaults  
3. Compare accuracy, training time, and resource usage
4. Adjust optimization parameters based on results

## Troubleshooting

### Optimization Not Applied

**Problem**: Generic defaults still being used

**Solution**: Check that:
- Game key matches configured games
- Optimization system is imported in routes
- No environment variables override defaults

### Poor Results with Optimized Settings

**Problem**: Optimized settings perform worse than expected

**Solution**: 
- Check game configuration accuracy
- Verify data quality and quantity
- Compare with generic defaults using `/api/train_optimization/{game}`
- Consider manual override for specific use cases

### Frontend Not Showing Optimized Parameters

**Problem**: Training panel shows generic defaults

**Solution**:
- Clear browser cache
- Check that backend returns `optimization_applied: true`
- Verify frontend component is properly integrated
- Check browser console for API errors

## Future Enhancements

### Planned Improvements

1. **Dynamic Learning**: System learns from training results to auto-adjust parameters
2. **Ensemble Optimization**: Optimize ensemble weights per game
3. **Time-Based Optimization**: Adjust parameters based on time of day/week
4. **Cross-Game Learning**: Apply learnings from similar games
5. **User Feedback**: Allow users to rate optimization quality

### Advanced Features

- **Multi-Objective Optimization**: Balance accuracy vs training time
- **Resource Awareness**: Adjust based on available compute resources
- **Seasonal Adjustment**: Adapt parameters based on seasonal patterns
- **Real-Time Optimization**: Adjust parameters during training

## Best Practices

1. **Start with Optimized Defaults**: Always begin with game-specific settings
2. **Monitor Results**: Track accuracy improvements vs generic settings
3. **Manual Tuning**: Override when domain knowledge suggests better parameters
4. **Document Changes**: Keep records of manual parameter adjustments
5. **Regular Review**: Periodically review optimization effectiveness

## Conclusion

Use the per-game profile as a conservative default, monitor validation and holdout separately, and do not interpret training metrics as improved lottery odds.