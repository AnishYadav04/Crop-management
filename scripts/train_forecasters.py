"""Audit the hourly archive, evaluate baselines, and train direct forecasts."""

from __future__ import annotations

import json
import os
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import psycopg
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import TimeSeriesSplit

from ml.features import FEATURE_TARGETS, LAGS, ROLLING_WINDOWS, make_features


warnings.filterwarnings(
    "ignore",
    message=r"`sklearn\.utils\.parallel\.delayed`.*",
    category=UserWarning,
)

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Kolkata")
TARGETS = {target: target for target in FEATURE_TARGETS}
HORIZONS = (1, 6, 24)
CV_SPLITS = 3
CV_TEST_HOURS = 24 * 30
HOLDOUT_HOURS = 24 * 30


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_data(database_url: str) -> pd.DataFrame:
    columns = ", ".join(TARGETS.values())
    query = f"""
        SELECT observed_at, {columns}, source, source_model, is_simulated,
               leaf_wetness_pct, leaf_wetness_is_estimated
        FROM sensor_readings
        WHERE location_name = %s AND source_model = 'ERA5'
        ORDER BY observed_at
    """
    with psycopg.connect(database_url) as conn:
        profile = conn.execute("SELECT location_name,timezone FROM site_profile WHERE id=1").fetchone()
        if not profile:
            raise RuntimeError("No active site profile exists")
        location, timezone_name = profile
        rows = conn.execute(query, (location,)).fetchall()
    if not rows:
        raise RuntimeError(f"No ERA5 readings found for {LOCATION}")

    names = ["observed_at", *TARGETS.keys(), "source", "source_model", "is_simulated",
             "leaf_wetness_pct", "leaf_wetness_is_estimated"]
    frame = pd.DataFrame(rows, columns=names).set_index("observed_at").sort_index()
    if frame.index.tz is None:
        raise RuntimeError("Database timestamps must include a timezone")
    frame.index = frame.index.tz_convert(ZoneInfo(timezone_name))
    frame.attrs["location"] = location
    frame.attrs["timezone"] = timezone_name
    return frame


def audit_data(frame: pd.DataFrame, out_dir: Path) -> dict:
    expected = pd.date_range(frame.index.min(), frame.index.max(), freq="h")
    original_rows = len(frame)
    duplicate_timestamps = int(frame.index.duplicated().sum())
    unique = frame.loc[~frame.index.duplicated(keep="first")]
    regular = unique.reindex(expected)
    numeric = list(TARGETS)
    quality = {
        "location": frame.attrs.get("location", "unknown"),
        "timezone": frame.attrs.get("timezone", "unknown"),
        "source_models": sorted(frame["source_model"].dropna().unique().tolist()),
        "rows_in_database": original_rows,
        "first_timestamp": frame.index.min().isoformat(),
        "last_timestamp": frame.index.max().isoformat(),
        "expected_hourly_rows_in_span": len(expected),
        "missing_hourly_timestamps": int(len(expected) - len(unique)),
        "duplicate_timestamps": duplicate_timestamps,
        "missing_values_by_target": {
            column: int(regular[column].isna().sum()) for column in numeric
        },
        "ranges": {
            column: {
                "min": None if regular[column].dropna().empty else float(regular[column].min()),
                "max": None if regular[column].dropna().empty else float(regular[column].max()),
            }
            for column in numeric
        },
        "leaf_wetness_is_estimated_for_all_rows": bool(
            frame["leaf_wetness_is_estimated"].fillna(False).all()
        ),
        "note": "ERA5 reanalysis values are model estimates; leaf wetness is a synthetic proxy.",
    }
    (out_dir / "data_quality.json").write_text(
        json.dumps(quality, indent=2), encoding="utf-8"
    )
    return quality


def scores(actual: np.ndarray, predicted: np.ndarray) -> dict:
    errors = actual - predicted
    return {
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors ** 2))),
    }


def new_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        learning_rate=0.07,
        max_iter=180,
        max_leaf_nodes=15,
        l2_regularization=1.0,
        random_state=42,
    )


def train_and_evaluate(frame: pd.DataFrame, out_dir: Path) -> tuple[list[dict], dict]:
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise RuntimeError("Training requires unique, chronological timestamps")
    expected = pd.date_range(frame.index.min(), frame.index.max(), freq="h")
    if not frame.index.equals(expected):
        raise RuntimeError("Training stopped: archive timestamps are not a complete hourly grid")
    missing_targets = [name for name in FEATURE_TARGETS if frame[name].isna().any()]
    if missing_targets:
        raise RuntimeError("Training stopped: missing target values in " + ", ".join(missing_targets))
    if len(frame) <= HOLDOUT_HOURS + CV_SPLITS * CV_TEST_HOURS + 168:
        raise RuntimeError("Insufficient rows for expanding-window validation plus a final 30-day holdout")

    metrics: list[dict] = []
    prediction_rows: list[dict] = []
    holdout_rows: list[dict] = []
    artifacts_dir = out_dir / "models"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    development_end = len(frame) - HOLDOUT_HOURS

    for horizon in HORIZONS:
        x_all = make_features(frame, horizon)
        splitter = TimeSeriesSplit(
            n_splits=CV_SPLITS, test_size=CV_TEST_HOURS, gap=horizon
        )
        y_by_target = {
            target: frame[target].shift(-horizon) for target in TARGETS
        }
        cv_end = development_end - max(HORIZONS)
        folds = list(splitter.split(x_all.iloc[:cv_end]))

        for target in TARGETS:
            y_all = y_by_target[target]
            oof: dict[str, list[float]] = {
                name: [] for name in (
                    "actual", "hist_gradient_boosting", "persistence",
                    "seasonal_daily_24h", "seasonal_weekly_168h",
                )
            }

            for fold_num, (train_idx, test_idx) in enumerate(folds, start=1):
                train_mask = x_all.iloc[train_idx].notna().all(axis=1) & y_all.iloc[train_idx].notna()
                test_mask = x_all.iloc[test_idx].notna().all(axis=1) & y_all.iloc[test_idx].notna()
                train_idx = train_idx[train_mask.to_numpy()]
                test_idx = test_idx[test_mask.to_numpy()]
                x_train = x_all.iloc[train_idx]
                y_train = y_all.iloc[train_idx]
                x_test = x_all.iloc[test_idx]
                y_test = y_all.iloc[test_idx]
                if len(y_train) < 24 * 30 or len(y_test) == 0:
                    raise RuntimeError("Insufficient hourly observations for expanding-window evaluation")

                model = new_model()
                model.fit(x_train, y_train)
                predicted = model.predict(x_test)
                origin_values = frame.loc[y_test.index, target].to_numpy(dtype=float)
                valid_index = y_test.index + pd.Timedelta(hours=horizon)
                daily_index = valid_index - pd.Timedelta(hours=24)
                weekly_index = valid_index - pd.Timedelta(hours=168)
                daily_values = frame[target].reindex(daily_index).to_numpy(dtype=float)
                weekly_values = frame[target].reindex(weekly_index).to_numpy(dtype=float)
                actual_values = y_test.to_numpy(dtype=float)
                common = np.isfinite(actual_values) & np.isfinite(predicted) & np.isfinite(origin_values) & np.isfinite(daily_values) & np.isfinite(weekly_values)
                if not common.any():
                    continue
                values_by_name = {
                    "actual": actual_values,
                    "hist_gradient_boosting": predicted,
                    "persistence": origin_values,
                    "seasonal_daily_24h": daily_values,
                    "seasonal_weekly_168h": weekly_values,
                }
                for name, values in values_by_name.items():
                    oof[name].extend(values[common].tolist())

                for timestamp, actual, forecast, persist, daily, weekly in zip(
                    y_test.index, y_test.to_numpy(), predicted, origin_values,
                    daily_values, weekly_values
                ):
                    if not all(np.isfinite(value) for value in (actual, forecast, persist, daily, weekly)):
                        continue
                    prediction_rows.append({
                        "valid_time": (timestamp + pd.Timedelta(hours=horizon)).isoformat(),
                        "target": target,
                        "horizon_hours": horizon,
                        "actual": float(actual),
                        "model": float(forecast),
                        "persistence": float(persist),
                        "seasonal_daily": float(daily),
                        "seasonal_weekly": float(weekly),
                        "fold": fold_num,
                    })

            actual_arr = np.asarray(oof["actual"], dtype=float)
            if not len(actual_arr):
                raise RuntimeError(f"No common finite validation rows for {target} at +{horizon}h")
            candidates = {
                name: scores(actual_arr, np.asarray(oof[name], dtype=float))
                for name in (
                    "hist_gradient_boosting", "persistence",
                    "seasonal_daily_24h", "seasonal_weekly_168h",
                )
            }
            winner = min(candidates, key=lambda name: candidates[name]["mae"])

            # Evaluate once on a final chronological holdout not used by CV selection.
            hold_idx = np.arange(development_end, len(frame) - horizon)
            fit_limit = development_end - horizon
            fit_idx = np.arange(fit_limit)
            fit_mask = x_all.iloc[fit_idx].notna().all(axis=1) & y_all.iloc[fit_idx].notna()
            fit_idx = fit_idx[fit_mask.to_numpy()]
            hold_x = x_all.iloc[hold_idx]
            hold_y = y_all.iloc[hold_idx]
            hold_valid = hold_x.notna().all(axis=1) & hold_y.notna()
            hold_idx = hold_idx[hold_valid.to_numpy()]
            hold_x = x_all.iloc[hold_idx]
            hold_y = y_all.iloc[hold_idx]
            if len(fit_idx) < 24 * 30 or hold_x.empty:
                raise RuntimeError(f"Insufficient holdout rows for {target} at +{horizon}h")
            hold_model = new_model()
            hold_model.fit(x_all.iloc[fit_idx], y_all.iloc[fit_idx])
            hold_predictions = hold_model.predict(hold_x)
            hold_origin = frame.loc[hold_y.index, target].to_numpy(dtype=float)
            hold_valid_time = hold_y.index + pd.Timedelta(hours=horizon)
            hold_daily = frame[target].reindex(hold_valid_time - pd.Timedelta(hours=24)).to_numpy(dtype=float)
            hold_weekly = frame[target].reindex(hold_valid_time - pd.Timedelta(hours=168)).to_numpy(dtype=float)
            hold_actual = hold_y.to_numpy(dtype=float)
            hold_common = np.isfinite(hold_actual) & np.isfinite(hold_predictions) & np.isfinite(hold_origin) & np.isfinite(hold_daily) & np.isfinite(hold_weekly)
            if not hold_common.any():
                raise RuntimeError(f"No common finite holdout rows for {target} at +{horizon}h")
            hold_candidates = {
                "hist_gradient_boosting": scores(hold_actual[hold_common], hold_predictions[hold_common]),
                "persistence": scores(hold_actual[hold_common], hold_origin[hold_common]),
                "seasonal_daily_24h": scores(hold_actual[hold_common], hold_daily[hold_common]),
                "seasonal_weekly_168h": scores(hold_actual[hold_common], hold_weekly[hold_common]),
            }
            for position in np.flatnonzero(hold_common):
                holdout_rows.append({
                    "origin_time": hold_y.index[position].isoformat(),
                    "valid_time": hold_valid_time[position].isoformat(),
                    "target": target,
                    "horizon_hours": horizon,
                    "actual": float(hold_actual[position]),
                    "hist_gradient_boosting": float(hold_predictions[position]),
                    "persistence": float(hold_origin[position]),
                    "seasonal_daily_24h": float(hold_daily[position]),
                    "seasonal_weekly_168h": float(hold_weekly[position]),
                })

            metric_row = {
                "target": target,
                "horizon_hours": horizon,
                "cross_validation_folds": CV_SPLITS,
                "out_of_fold_rows": len(actual_arr),
                "holdout_rows": int(hold_common.sum()),
                "holdout_start": frame.index[development_end].isoformat(),
                "selected_by_mae": winner,
                "holdout_best_by_mae": min(hold_candidates, key=lambda name: hold_candidates[name]["mae"]),
            }
            metric_row.update({f"{name}_{metric}": value for name, result in candidates.items() for metric, value in result.items()})
            metric_row.update({f"holdout_{name}_{metric}": value for name, result in hold_candidates.items() for metric, value in result.items()})
            metrics.append(metric_row)

            # Refit a deployable artifact on all available rows after evaluation.
            valid = y_all.notna() & x_all.notna().all(axis=1)
            final_model = new_model()
            final_model.fit(x_all.loc[valid], y_all.loc[valid])
            joblib.dump(
                {
                    "model": final_model,
                    "feature_columns": x_all.columns.tolist(),
                    "target": target,
                    "horizon_hours": horizon,
                    "feature_schema_version": "hourly-lag-v1",
                    "timezone": "Asia/Kolkata",
                    "training_start": frame.index.min().isoformat(),
                    "training_end": y_all.loc[valid].index.max().isoformat(),
                    "data_source": "Open-Meteo ERA5 reanalysis",
                    "metrics": candidates,
                    "holdout_metrics": hold_candidates,
                    "holdout_start": frame.index[development_end].isoformat(),
                },
                artifacts_dir / f"{target}_h{horizon}.joblib",
            )
            print(f"Evaluated {target} at +{horizon}h; best baseline/model: {winner}", flush=True)

    metrics_path = out_dir / "metrics.csv"
    pd.DataFrame(metrics).to_csv(metrics_path, index=False)
    pd.DataFrame(prediction_rows).to_csv(out_dir / "oof_predictions.csv", index=False)
    pd.DataFrame(holdout_rows).to_csv(out_dir / "holdout_predictions.csv", index=False)
    summary = {
        "run_at": datetime.now(TZ).isoformat(),
        "location": frame.attrs.get("location", "unknown"),
        "data_source": "Open-Meteo ERA5 historical reanalysis",
        "evaluation": f"{CV_SPLITS} expanding-window folds, {CV_TEST_HOURS} test hours per fold, horizon-specific gap",
        "final_holdout_hours": HOLDOUT_HOURS,
        "final_holdout_start": frame.index[development_end].isoformat(),
        "feature_schema_version": "hourly-lag-v1",
        "targets": list(TARGETS),
        "horizons_hours": list(HORIZONS),
        "rows_per_target_horizon": CV_SPLITS * CV_TEST_HOURS,
        "metrics_file": str(metrics_path.relative_to(ROOT)),
        "model_directory": str(artifacts_dir.relative_to(ROOT)),
        "warning": "These are environmental forecasts, not crop diagnoses. Leaf wetness is estimated and excluded as a target.",
    }
    (out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return metrics, summary


def main() -> None:
    load_env_file()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Set DATABASE_URL in .env before training")
    out_dir = ROOT / "artifacts" / "forecasting"
    out_dir.mkdir(parents=True, exist_ok=True)

    frame = load_data(database_url)
    quality = audit_data(frame, out_dir)
    print(
        f"Audited {quality['rows_in_database']} rows; "
        f"{quality['missing_hourly_timestamps']} missing hours; "
        f"{quality['duplicate_timestamps']} duplicate timestamps.",
        flush=True,
    )
    metrics, summary = train_and_evaluate(frame, out_dir)
    print(f"Saved {len(metrics)} target/horizon evaluations to {summary['metrics_file']}.")
    print(f"Saved {len(metrics)} refitted model artifacts under {summary['model_directory']}.")


if __name__ == "__main__":
    main()
