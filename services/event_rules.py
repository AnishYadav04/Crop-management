"""Bounded, source-isolated prototype weather observation rule."""
from __future__ import annotations

import json
from datetime import timedelta

EVENT_TYPE = "weather_rain_onset_after_temperature_drop"
RULE_VERSION = "weather-rain-cooling-v2"


def build_event_key(location: str, source_model: str, event_type: str, observed_at) -> str:
    return f"{location}|{source_model}|{event_type}|{observed_at.isoformat()}"


def create_events_for_reading(
    conn, reading_id: int, observed_at, location: str, source_model: str, sensors: dict
) -> None:
    """Record an informational event only when required evidence is present."""
    rain = sensors.get("rainfall_mm")
    temperature_now = sensors.get("air_temp_c")
    if rain is None or temperature_now is None:
        return

    with conn.cursor() as cur:
        cur.execute(
            """SELECT rainfall_onset_mm, temperature_drop_c, temperature_window_hours,
                      event_cooldown_hours, crop_name, growth_stage, soil_type
                 FROM site_profile WHERE id=1"""
        )
        profile = cur.fetchone()
        if not profile:
            return
        rain_threshold, drop_threshold, window_hours, cooldown, crop, stage, soil = profile

        cur.execute(
            """SELECT observed_at, rainfall_mm
                 FROM sensor_readings
                WHERE id<>%s AND location_name=%s AND source_model=%s
                  AND observed_at < %s
                ORDER BY observed_at DESC LIMIT 1""",
            (reading_id, location, source_model, observed_at),
        )
        previous = cur.fetchone()
        if previous is None or previous[1] is None:
            return  # Unknown previous interval is not evidence that rain just started.
        interval_seconds = sensors.get("rainfall_interval_seconds")
        max_rain_gap = timedelta(seconds=max(1800, min(7200, (interval_seconds or 900) * 3)))
        if observed_at - previous[0] > max_rain_gap:
            return  # Do not infer onset across a sensor/weather-feed outage.
        if float(previous[1]) >= rain_threshold or float(rain) < rain_threshold:
            return

        target_time = observed_at - timedelta(hours=window_hours)
        tolerance = timedelta(hours=1)
        cur.execute(
            """SELECT observed_at, air_temp_c
                 FROM sensor_readings
                WHERE location_name=%s AND source_model=%s AND id<>%s
                  AND air_temp_c IS NOT NULL
                  AND observed_at BETWEEN %s - interval '1 hour' AND %s + interval '1 hour'
                ORDER BY abs(extract(epoch FROM (observed_at - %s)))
                LIMIT 1""",
            (location, source_model, reading_id, target_time, target_time, target_time),
        )
        older = cur.fetchone()
        if older is None or abs(older[0] - target_time) > tolerance:
            return
        drop = float(older[1]) - float(temperature_now)
        if drop < float(drop_threshold):
            return

        cur.execute(
            """SELECT 1 FROM maintenance_events
                WHERE location_name=%s AND event_type=%s AND source_model=%s
                  AND detected_at >= %s - (%s * interval '1 hour')
                LIMIT 1""",
            (location, EVENT_TYPE, source_model, observed_at, cooldown),
        )
        if cur.fetchone():
            return

        details = {
            "rule_version": RULE_VERSION,
            "source_model": source_model,
            "source_kind": sensors.get("source_kind", "unknown"),
            "crop_name": crop,
            "growth_stage": stage,
            "soil_type": soil,
            "rainfall_interval_mm": float(rain),
            "rainfall_interval_seconds": interval_seconds,
            "temperature_window_hours": int(window_hours),
            "temperature_before_c": float(older[1]),
            "temperature_before_at": older[0].isoformat(),
            "temperature_now_c": float(temperature_now),
            "temperature_drop_c": round(drop, 2),
            "air_humidity_pct": sensors.get("air_humidity_pct"),
            "soil_moisture_shallow_pct": sensors.get("soil_moisture_shallow_pct"),
            "soil_moisture_deep_pct": sensors.get("soil_moisture_deep_pct"),
            "soil_moisture_semantics": sensors.get("soil_moisture_semantics", "unspecified"),
            "leaf_wetness_pct_estimated": sensors.get("leaf_wetness_pct"),
        }
        key = build_event_key(location, source_model, EVENT_TYPE, observed_at)
        summary = (
            "Weather observation: rain began after a temperature drop. "
            "Inspect field conditions; this event does not diagnose crop stress or disease."
        )
        evidence = [
            {"name": "rainfall_mm", "value": float(rain), "threshold": float(rain_threshold)},
            {"name": "temperature_drop_c", "value": round(drop, 2), "threshold": float(drop_threshold),
             "window_hours": int(window_hours), "comparison_timestamp": older[0].isoformat()},
        ]
        cur.execute(
            """INSERT INTO maintenance_events
                   (event_key,event_type,location_name,detected_at,severity,summary,details,
                    research_status,source_model,rule_version,event_status,evidence)
               VALUES (%s,%s,%s,%s,'low',%s,%s::jsonb,'pending',%s,%s,'open',%s::jsonb)
               ON CONFLICT(event_key) DO NOTHING""",
            (key, EVENT_TYPE, location, observed_at, summary, json.dumps(details),
             source_model, RULE_VERSION, json.dumps(evidence)),
        )
        if cur.rowcount:
            print(f"WEATHER OBSERVATION EVENT: {key}", flush=True)
