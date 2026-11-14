"""Causal hourly features shared by forecasting training and serving."""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_TARGETS = (
    "air_temp_c",
    "air_humidity_pct",
    "soil_moisture_shallow_pct",
    "soil_moisture_deep_pct",
    "soil_temp_c",
    "rainfall_mm",
    "solar_radiation_w_m2",
)
LAGS = (0, 1, 2, 3, 6, 12, 24, 48, 168)
ROLLING_WINDOWS = (6, 24, 168)
REQUIRED_HISTORY_ROWS = max(max(LAGS) + 1, max(ROLLING_WINDOWS))


def make_features(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Build the version-1 feature schema; index must be an hourly time grid."""
    if horizon <= 0:
        raise ValueError("horizon must be a positive number of hours")
    missing = set(FEATURE_TARGETS) - set(frame.columns)
    if missing:
        raise ValueError(f"missing feature series: {', '.join(sorted(missing))}")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("feature index must be unique and chronological")
    if len(frame.index) > 1:
        gaps = frame.index.to_series().diff().dropna()
        if not (gaps == pd.Timedelta(hours=1)).all():
            raise ValueError("feature index must be a complete hourly grid")

    columns = {}
    for target in FEATURE_TARGETS:
        series = frame[target]
        for lag in LAGS:
            columns[f"{target}_lag_{lag}"] = series.shift(lag)
        for window in ROLLING_WINDOWS:
            rolled = series.rolling(window=window, min_periods=window)
            columns[f"{target}_mean_{window}"] = rolled.mean()
            if window in (24, 168):
                columns[f"{target}_std_{window}"] = rolled.std()

    valid = frame.index + pd.Timedelta(hours=horizon)
    columns["forecast_hour_sin"] = np.sin(2 * np.pi * valid.hour / 24)
    columns["forecast_hour_cos"] = np.cos(2 * np.pi * valid.hour / 24)
    columns["forecast_weekday_sin"] = np.sin(2 * np.pi * valid.day_of_week / 7)
    columns["forecast_weekday_cos"] = np.cos(2 * np.pi * valid.day_of_week / 7)
    columns["forecast_year_sin"] = np.sin(2 * np.pi * valid.day_of_year / 365.25)
    columns["forecast_year_cos"] = np.cos(2 * np.pi * valid.day_of_year / 365.25)
    return pd.DataFrame(columns, index=frame.index)


def exact_hourly_window(frame: pd.DataFrame, rows: int) -> pd.DataFrame | None:
    """Return the last exact complete hourly window or None; never drop gaps."""
    if rows <= 0 or len(frame) < rows:
        return None
    window = frame.sort_index().iloc[-rows:]
    if window.index.has_duplicates or not window.index.is_monotonic_increasing:
        return None
    expected = pd.date_range(window.index[0], periods=rows, freq="h")
    if not window.index.equals(expected) or window.isna().any().any():
        return None
    return window
