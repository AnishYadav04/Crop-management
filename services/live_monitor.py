"""Poll Kapriwas current weather, persist simulated readings, and open events."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import psycopg

from services.data_quality import build_measurement_metadata
from services.event_rules import create_events_for_reading


DATABASE_URL = os.getenv("DATABASE_URL")
POLL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "900"))
API_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_FIELDS = ",".join(
    (
        "temperature_2m",
        "relative_humidity_2m",
        "rain",
        "precipitation",
        "shortwave_radiation",
        "soil_temperature_6cm",
        "soil_moisture_3_to_9cm",
        "soil_moisture_9_to_27cm",
        "is_day",
    )
)


def load_site_profile(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT location_name,latitude,longitude,timezone,crop_name,growth_stage,soil_type FROM site_profile WHERE id=1")
        row = cur.fetchone()
    if not row:
        raise RuntimeError("No active site profile exists")
    return dict(zip(("location_name", "latitude", "longitude", "timezone", "crop_name", "growth_stage", "soil_type"), row))


def fetch_current(profile: dict) -> dict:
    query = urlencode(
        {
            "latitude": profile["latitude"],
            "longitude": profile["longitude"],
            "current": CURRENT_FIELDS,
            "timezone": profile["timezone"],
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        }
    )
    request = Request(
        API_URL + "?" + query,
        headers={"User-Agent": "CropManagementSimulation/0.1"},
    )
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if "current" not in payload:
        raise ValueError("Current weather API response has no current values")
    return payload


def _previous_rows(conn: psycopg.Connection, current_time: datetime, location: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT observed_at, rainfall_mm, leaf_wetness_pct
            FROM sensor_readings
            WHERE location_name = %s AND source_model = 'open-meteo-current'
              AND observed_at < %s
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            (location, current_time),
        )
        previous = cur.fetchone()
    return previous


def estimate_leaf_wetness(
    humidity: float | None,
    rain_mm: float | None,
    air_temp_c: float | None,
    previous_pct: float | None,
) -> float:
    """Simple synthetic proxy for simulation only; it is not a leaf sensor."""
    state = previous_pct or 0.0
    if (rain_mm or 0.0) >= 0.1:
        state = max(state, min(100.0, 35.0 + (rain_mm or 0.0) * 20.0))
    elif (humidity or 0.0) >= 90 and air_temp_c is not None and 5 <= air_temp_c <= 30:
        state = min(100.0, state + 12.0)
    else:
        state = max(0.0, state - 18.0)
    return round(state, 1)


def _percent_from_vwc(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(1.0, value)) * 100, 2)


def persist_reading(conn: psycopg.Connection, payload: dict, profile: dict) -> tuple[datetime, bool, dict]:
    current = payload["current"]
    observed_at = datetime.fromisoformat(current["time"])
    timezone = ZoneInfo(profile["timezone"])
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone)
    observed_at = observed_at.astimezone(timezone)
    previous = _previous_rows(conn, observed_at, profile["location_name"])

    air_temp = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    # `precipitation` is the total liquid/frozen interval amount used by ERA5.
    rain = current.get("precipitation")
    leaf_wetness = estimate_leaf_wetness(
        humidity,
        rain,
        air_temp,
        None if previous is None else previous[2],
    )
    sensors = {
        "air_temp_c": air_temp,
        "air_humidity_pct": humidity,
        "soil_moisture_shallow_pct": _percent_from_vwc(current.get("soil_moisture_3_to_9cm")),
        "soil_moisture_deep_pct": _percent_from_vwc(current.get("soil_moisture_9_to_27cm")),
        "soil_temp_c": current.get("soil_temperature_6cm"),
        "solar_radiation_w_m2": current.get("shortwave_radiation"),
        "rainfall_mm": rain,
        "rainfall_interval_seconds": current.get("interval"),
        "source_kind": "weather_model",
        "soil_moisture_semantics": "volumetric_water_content_pct",
        "leaf_wetness_pct": leaf_wetness,
    }
    metadata = build_measurement_metadata(sensors, "weather_model", {
        "soil_moisture_shallow_pct": {"semantics":"volumetric_water_content_pct","depth_cm":[3,9]},
        "soil_moisture_deep_pct": {"semantics":"volumetric_water_content_pct","depth_cm":[9,27]},
        "rainfall_mm": {"interval_seconds":current.get("interval"),"semantics":"preceding_interval_sum_mm"},
        "leaf_wetness_pct": {"source_kind":"derived_estimate","is_estimated":True,"method":"synthetic rain-humidity-temperature proxy"},
    })
    record = {
        "device_id": "open-meteo-kapriwas-model",
        "observed_at": observed_at.isoformat(),
        "source_kind": "weather_model",
        "source": "open-meteo-current-model",
        "source_model": "open-meteo-current",
        "is_simulated": True,
        "sensors": sensors,
        "measurement_metadata": metadata,
        "leaf_wetness_is_estimated": True,
        "weather_api": {
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "timezone": payload.get("timezone"),
            "current_interval_seconds": current.get("interval"),
            "is_day": current.get("is_day"),
        },
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sensor_readings (
                observed_at, location_name, latitude, longitude, source, source_model,
                is_simulated, air_temp_c, air_humidity_pct,
                soil_moisture_shallow_pct, soil_moisture_deep_pct, soil_temp_c,
                solar_radiation_w_m2, rainfall_mm, leaf_wetness_pct,
                leaf_wetness_is_estimated, raw_json, measurement_metadata, source_kind, received_at,
                timestamp_quality, device_id, soil_moisture_semantics,
                soil_moisture_shallow_top_cm, soil_moisture_shallow_bottom_cm,
                soil_moisture_deep_top_cm, soil_moisture_deep_bottom_cm,
                rainfall_interval_seconds, rainfall_semantics, quality_status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, 'weather_model', now(), 'model_timestamp', 'open-meteo-kapriwas-model',
                'volumetric_water_content_pct', 3, 9, 9, 27, %s, 'preceding_interval_sum_mm', 'valid'
            ) ON CONFLICT (observed_at, location_name, source_model) DO NOTHING
            RETURNING id
            """,
            (
                observed_at,
                profile["location_name"],
                profile["latitude"],
                profile["longitude"],
                record["source"],
                record["source_model"],
                True,
                sensors["air_temp_c"],
                sensors["air_humidity_pct"],
                sensors["soil_moisture_shallow_pct"],
                sensors["soil_moisture_deep_pct"],
                sensors["soil_temp_c"],
                sensors["solar_radiation_w_m2"],
                sensors["rainfall_mm"],
                sensors["leaf_wetness_pct"],
                True,
                json.dumps(record),
                json.dumps(metadata),
                current.get("interval"),
            ),
        )
        inserted_row = cur.fetchone()
        inserted = inserted_row is not None

    if inserted:
        create_events_for_reading(conn, inserted_row[0], observed_at, profile["location_name"],
                                  "open-meteo-current", sensors)
    conn.commit()
    return observed_at, inserted, sensors


def run_once() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        profile = load_site_profile(conn)
    payload = fetch_current(profile)
    with psycopg.connect(DATABASE_URL) as conn:
        observed_at, inserted, sensors = persist_reading(conn, payload, profile)
    if inserted:
        print(
            f"Saved {observed_at.isoformat()} {profile['location_name']} model reading: "
            f"{json.dumps(sensors)}",
            flush=True,
        )
    else:
        print(f"No new model interval at {observed_at.isoformat()}; waiting.", flush=True)


def main() -> None:
    if not DATABASE_URL:
        raise SystemExit("Set DATABASE_URL before starting the live monitor")
    print(
        f"Live Kapriwas weather simulation started; polling every {POLL_SECONDS}s.",
        flush=True,
    )
    while True:
        cycle_started = time.monotonic()
        try:
            run_once()
        except Exception as exc:
            print(f"Monitor cycle failed: {type(exc).__name__}: {exc}", flush=True)
        remaining = max(30, POLL_SECONDS - (time.monotonic() - cycle_started))
        time.sleep(remaining)


if __name__ == "__main__":
    main()
