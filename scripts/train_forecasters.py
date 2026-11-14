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


warnings.filterwarnings(
    "ignore",
    message=r"`sklearn\.utils\.parallel\.delayed`.*",
    category=UserWarning,
)

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Kolkata")
LOCATION = "Kapriwas, Rewari, Haryana, India"
TARGETS = {
    "air_temp_c": "air_temp_c",
    "air_humidity_pct": "air_humidity_pct",
    "soil_moisture_shallow_pct": "soil_moisture_shallow_pct",
    "soil_moisture_deep_pct": "soil_moisture_deep_pct",
    "soil_temp_c": "soil_temp_c",
    "rainfall_mm": "rainfall_mm",
    "solar_radiation_w_m2": "solar_radiation_w_m2",
}
HORIZONS = (1, 6, 24)
LAGS = (0, 1, 2, 3, 6, 12, 24, 48, 168)
ROLLING_WINDOWS = (6, 24, 168)
CV_SPLITS = 3
CV_TEST_HOURS = 24 * 30


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
        rows = conn.execute(query, (LOCATION,)).fetchall()
    if not rows:
        raise RuntimeError(f"No ERA5 readings found for {LOCATION}")

    names = ["observed_at", *TARGETS.keys(), "source", "source_model", "is_simulated",
             "leaf_wetness_pct", "leaf_wetness_is_estimated"]
    frame = pd.DataFrame(rows, columns=names).set_index("observed_at").sort_index()
    if frame.index.tz is None:
        raise RuntimeError("Database timestamps must include a timezone")
    frame.index = frame.index.tz_convert(TZ)
    return frame


def audit_data(frame: pd.DataFrame, out_dir: Path) -> dict:
    expected = pd.date_range(frame.index.min(), frame.index.max(), freq="h")
    original_rows = len(frame)
    duplicate_timestamps = int(frame.index.duplicated().sum())
    regular = frame.reindex(expected)
    numeric = list(TARGETS)
    quality = {
        "location": LOCATION,
        "timezone": "Asia/Kolkata",
        "source_models": sorted(frame["source_model"].dropna().unique().tolist()),
        "rows_in_database": original_rows,
        "first_timestamp": frame.index.min().isoformat(),
        "last_timestamp": frame.index.max().isoformat(),
        "expected_hourly_rows_in_span": len(expected),
        "missing_hourly_timestamps": int(len(expected) - original_rows),
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


def make_features(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    columns = {}
    for target in TARGETS:
        series = frame[target]
        for lag in LAGS:
            columns[f"{target}_lag_{lag}"] = series.shift(lag)
        for window in ROLLING_WINDOWS:
            rolled = series.rolling(window=window, min_periods=window)
            columns[f"{target}_mean_{window}"] = rolled.mean()
            if window in (24, 168):
                columns[f"{target}_std_{window}"] = rolled.std()

    valid_time = frame.index + pd.Timedelta(hours=horizon)
    hour = valid_time.hour
    weekday = valid_time.day_of_week
    day_of_year = valid_time.day_of_year
    columns["forecast_hour_sin"] = np.sin(2 * np.pi * hour / 24)
    columns["forecast_hour_cos"] = np.cos(2 * np.pi * hour / 24)
    columns["forecast_weekday_sin"] = np.sin(2 * np.pi * weekday / 7)
    columns["forecast_weekday_cos"] = np.cos(2 * np.pi * weekday / 7)
    columns["forecast_year_sin"] = np.sin(2 * np.pi * day_of_year / 365.25)
    columns["forecast_year_cos"] = np.cos(2 * np.pi * day_of_year / 365.25)
    return pd.DataFrame(columns, index=frame.index)


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
    metrics: list[dict] = []
    prediction_rows: list[dict] = []
    artifacts_dir = out_dir / "models"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    for horizon in HORIZONS:
        x_all = make_features(frame, horizon)
        splitter = TimeSeriesSplit(
            n_splits=CV_SPLITS, test_size=CV_TEST_HOURS, gap=horizon
        )
        y_by_target = {
            target: frame[target].shift(-horizon) for target in TARGETS
        }
        folds = list(splitter.split(x_all))

        for target in TARGETS:
            y_all = y_by_target[target]
            oof_actual: list[float] = []
            oof_model: list[float] = []
            oof_persistence: list[float] = []
            oof_daily: list[float] = []
            oof_weekly: list[float] = []

            for fold_num, (train_idx, test_idx) in enumerate(folds, start=1):
                train_idx = train_idx[y_all.iloc[train_idx].notna().to_numpy()]
                test_idx = test_idx[y_all.iloc[test_idx].notna().to_numpy()]
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

                oof_actual.extend(y_test.to_numpy(dtype=float).tolist())
                oof_model.extend(predicted.tolist())
                oof_persistence.extend(origin_values.tolist())
                oof_daily.extend(daily_values.tolist())
                oof_weekly.extend(weekly_values.tolist())

                for timestamp, actual, forecast, persist, daily, weekly in zip(
                    y_test.index, y_test.to_numpy(), predicted, origin_values,
                    daily_values, weekly_values
                ):
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

            actual_arr = np.asarray(oof_actual)
            candidates = {
                "hist_gradient_boosting": scores(actual_arr, np.asarray(oof_model)),
                "persistence": scores(actual_arr, np.asarray(oof_persistence)),
                "seasonal_daily_24h": scores(actual_arr, np.asarray(oof_daily)),
                "seasonal_weekly_168h": scores(actual_arr, np.asarray(oof_weekly)),
            }
            winner = min(candidates, key=lambda name: candidates[name]["mae"])
            model_metric = candidates["hist_gradient_boosting"]
            metrics.append({
                "target": target,
                "horizon_hours": horizon,
                "cross_validation_folds": CV_SPLITS,
                "out_of_fold_rows": len(actual_arr),
                "selected_by_mae": winner,
                **{
                    f"{name}_{metric}": value
                    for name, result in candidates.items()
                    for metric, value in result.items()
                },
            })

            # Refit a deployable artifact on all available rows after evaluation.
            valid = y_all.notna()
            final_model = new_model()
            final_model.fit(x_all.loc[valid], y_all.loc[valid])
            joblib.dump(
                {
                    "model": final_model,
                    "feature_columns": x_all.columns.tolist(),
                    "target": target,
                    "horizon_hours": horizon,
                    "timezone": "Asia/Kolkata",
                    "training_start": frame.index.min().isoformat(),
                    "training_end": y_all.loc[valid].index.max().isoformat(),
                    "data_source": "Open-Meteo ERA5 reanalysis",
                    "metrics": candidates,
                },
                artifacts_dir / f"{target}_h{horizon}.joblib",
            )
            print(f"Evaluated {target} at +{horizon}h; best baseline/model: {winner}", flush=True)

    metrics_path = out_dir / "metrics.csv"
    pd.DataFrame(metrics).to_csv(metrics_path, index=False)
    pd.DataFrame(prediction_rows).to_csv(out_dir / "oof_predictions.csv", index=False)
    summary = {
        "run_at": datetime.now(TZ).isoformat(),
        "location": LOCATION,
        "data_source": "Open-Meteo ERA5 historical reanalysis",
        "evaluation": f"{CV_SPLITS} expanding-window folds, {CV_TEST_HOURS} test hours per fold, horizon-specific gap",
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
