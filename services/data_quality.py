"""Input validation and timestamp quality helpers for telemetry ingestion."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


SENSOR_RANGES = {
    "air_temp_c": (-80.0, 70.0),
    "air_humidity_pct": (0.0, 100.0),
    "soil_moisture_shallow_pct": (0.0, 100.0),
    "soil_moisture_deep_pct": (0.0, 100.0),
    "soil_temp_c": (-40.0, 100.0),
    "light_lux": (0.0, 200000.0),
    "rainfall_mm": (0.0, 1000.0),
    "leaf_wetness_pct": (0.0, 100.0),
}
MAX_MQTT_PAYLOAD_BYTES = 16_384
SENSOR_UNITS = {
    "air_temp_c": "degC", "air_humidity_pct": "percent",
    "soil_moisture_shallow_pct": "percent", "soil_moisture_deep_pct": "percent",
    "soil_temp_c": "degC", "light_lux": "lux", "solar_radiation_w_m2": "W/m2",
    "rainfall_mm": "mm", "leaf_wetness_pct": "percent",
}


def build_measurement_metadata(values: dict, default_source_kind: str, overrides: dict | None = None) -> dict:
    overrides = overrides or {}
    metadata = {}
    for name, value in values.items():
        if name not in SENSOR_UNITS or value is None:
            continue
        item = {"source_kind": default_source_kind, "unit": SENSOR_UNITS[name]}
        item.update(overrides.get(name, {}))
        metadata[name] = item
    return metadata


def normalize_sensor_values(values: object) -> dict:
    if not isinstance(values, dict):
        raise ValueError("sensors must be a JSON object")
    normalized = {}
    for name, (minimum, maximum) in SENSOR_RANGES.items():
        value = values.get(name)
        if value is None:
            normalized[name] = None
            continue
        if isinstance(value, bool):
            raise ValueError(f"{name} must be numeric")
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{name} is outside its allowed range")
        normalized[name] = value
    return normalized


def parse_observation_time(raw_value: object, received_at: datetime | None = None) -> tuple[datetime, str]:
    received_at = received_at or datetime.now(timezone.utc)
    if not isinstance(raw_value, str) or not raw_value.strip() or raw_value.startswith("uptime+"):
        return received_at, "receive_time_no_device_clock"
    try:
        observed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return received_at, "receive_time_invalid_device_clock"
    if observed.tzinfo is None:
        return received_at, "receive_time_naive_device_clock"
    observed = observed.astimezone(timezone.utc)
    if observed > received_at + timedelta(minutes=5):
        return received_at, "receive_time_future_device_clock"
    return observed, "device_clock"
