# Forecasting implementation plan

## Implementation status

Implemented on 2026-11-08:

- Data audit and repeatable report: **done**. The current archive has 8,760 rows, no missing hours, and no duplicate timestamps.
- Initial targets and horizons: **done**. Seven environmental variables are forecast at +1h, +6h, and +24h.
- Baselines and learned model: **done**. The pipeline compares persistence, 24-hour seasonal-naive, 168-hour seasonal-naive, and histogram gradient boosting.
- Chronological evaluation and refit artifacts: **core implemented**. Three expanding-window folds each evaluate 720 hours per target/horizon; 21 model/horizon artifacts and out-of-fold metrics are saved locally under `artifacts/forecasting/`. Seasonal breakdown plots and a final frozen holdout are still future work.
- Live dashboard and forecast API: **implemented with a live-data gate**. It uses only Open-Meteo current-feed history once at least 168 consecutive hourly readings are present; it does not bridge gaps with ERA5.
- Crop-attention labels: **not available**. The data has no observed crop-stress labels, so no attention classifier was trained. Experimental weather rules remain advisory.

The learned model had the lowest out-of-fold MAE for air temperature, humidity, soil temperature, and solar radiation at +1h; for air temperature, humidity, and soil temperature at +6h. Persistence or the daily seasonal baseline won the other target/horizon comparisons. Use [`artifacts/forecasting/metrics.csv`](artifacts/forecasting/metrics.csv) for the full scores. These scores measure fit to reanalysis data, not measured field performance.

## Current starting point

- PostgreSQL contains 8,760 hourly rows for Kapriwas, Rewari, Haryana, covering 2025-11-03 through 2026-11-02 in `Asia/Kolkata`.
- The source is Open-Meteo ERA5 historical reanalysis. These values are model estimates at grid resolution, not readings captured from Wokwi or field sensors.
- Air temperature, humidity, rainfall, solar radiation, soil temperature, and soil moisture come from the archive. Leaf wetness is a synthetic proxy derived from weather. pH is not present.
- This is enough to build and evaluate a first environmental forecasting prototype. It is not enough to claim reliable crop-health prediction: one year represents only one annual cycle, and there are no observed crop-stress labels.

## Phase 1 — Audit and prepare the data

1. Add a repeatable data-quality report for row count, hourly gaps, duplicates, nulls, timestamp timezone, units, min/max values, and source/model mix.
2. Keep the imported reanalysis as the raw historical source. Build a separate feature dataset or SQL view for modeling so transformations can be repeated.
3. Mark estimated fields explicitly. Do not treat `leaf_wetness_pct` as observed, and do not silently combine ERA5 values with future Wokwi or field readings.
4. Check suspicious values and missing intervals; document any filtering or imputation.

**Deliverable:** a documented, reproducible dataset query and a quality summary saved with each model run.

## Phase 2 — Define what the first model predicts

Start with measurable regression tasks that match the available data:

- Forecast air temperature and humidity 1, 6, and 24 hours ahead.
- Forecast shallow and deeper soil moisture 6 and 24 hours ahead.
- Optionally forecast rainfall or solar radiation if the evaluation shows the one-year archive supports it.

Keep the first model focused on forecasting sensor-like variables. A separate “crop needs attention” classifier needs a defined crop, location/soil context, growth stage, irrigation history, and labeled stress events. A weather-derived leaf-wetness proxy cannot act as a disease label.

**Deliverable:** a short target specification defining target column, forecast horizon, eligible inputs, units, and the intended user decision.

## Phase 3 — Build honest chronological baselines

1. Build persistence forecasts (the next value equals the latest observed value).
2. Build seasonal-naive forecasts (for hourly data, compare against the value 24 hours earlier).
3. Compare these baselines with one compact feature-based model, such as gradient-boosted trees, using lagged values, rolling summaries, and hour/day-of-year features.
4. Fit preprocessing only on each training window. Avoid random train/test splitting and avoid using future weather values unless the deployed model will receive those forecasts at prediction time.

**Deliverable:** repeatable baseline forecasts and a model comparison report.

## Phase 4 — Evaluate without looking into the future

1. Sort by `observed_at` and keep each validation/test window later than its training window.
2. Use expanding-window time-series splits for model selection, then hold out the latest continuous period as a final test.
3. Report MAE and RMSE per target and horizon; include a plot of predictions versus actual archive values and performance by season.
4. Compare every candidate against persistence and seasonal-naive baselines. Do not call a model useful unless it beats a baseline on the held-out period.

Scikit-learn's `TimeSeriesSplit` is designed for time-ordered data and notes that comparable fold durations require equally spaced samples. [Documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)

**Deliverable:** saved split dates, metrics, plots, and a clear go/no-go decision for each target.

## Phase 5 — Add crop-attention labels and validate them

1. Choose the first crop and growth stage to support.
2. Define candidate stress rules with crop-specific agronomic references, then review them with a knowledgeable grower or agronomist.
3. Record actual irrigation, heat/drought symptoms, disease observations, and interventions with timestamps and confidence/source.
4. Only after labels exist, train an attention-risk classifier. Evaluate precision/recall and missed high-risk events, not just overall accuracy.
5. Keep recommendations advisory and display the evidence, uncertainty, and source for each alert.

**Deliverable:** a labeled event dataset and an evaluated alert model; until then, the dashboard should show forecasts and explicitly rule-based experimental indicators.

## Phase 6 — Save and serve forecasts

1. **Done:** artifacts include target, horizon, training interval, features, evaluation metrics, and ERA5 source metadata.
2. **Done:** forecasts are served through the backend API, selected from the model or baseline winner from historical validation, and shown in the dashboard with the selected method and validation MAE.
3. **Done:** forecasts stay unavailable until the current weather feed has a contiguous seven-day hourly window; the feed is kept separate from Wokwi and ERA5.
4. **Future:** persist forecast runs and add calibrated uncertainty ranges.
5. **Future:** compare each forecast with later observations, report live errors, and retrain only after a recorded evaluation.

**Deliverable:** a versioned forecasting service suitable for dashboard use.

## Data sufficiency and next collection

The current 8,760 rows are a reasonable first sandbox for hourly environmental forecasts and pipeline work. For stronger season-aware generalization, collect at least 2–3 years or multiple full crop cycles at the same site. Crop-attention prediction needs repeated, timestamped examples of both stressed and healthy conditions; row count alone cannot replace those labels. Real field observations are especially important because the current ERA5 soil values are grid-model estimates, while the leaf-wetness series is synthetic.

## Proposed build order

1. Data-quality report and stable feature query.
2. Target/horizon definitions.
3. Persistence and seasonal-naive baselines.
4. Chronological evaluation and plots.
5. One feature-based model for a selected target.
6. Crop-specific event logging and labels.
7. Attention-risk model, API, then dashboard presentation.

The training pipeline is in `scripts/train_forecasters.py`; the backend serving path is in `services/dashboard.py`. Run training after PostgreSQL is up and the Python requirements are installed. It writes a coverage/null/range audit, fold metrics, out-of-fold predictions, run metadata, and refitted model artifacts. Artifacts are mounted read-only into the dashboard container and excluded from Git.
