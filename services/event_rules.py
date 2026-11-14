"""Shared, configurable weather event rules for every reading source."""
from __future__ import annotations

import json

EVENT_TYPE = "rain_started_after_temperature_drop"


def create_events_for_reading(conn, reading_id: int, observed_at, location: str,
                              source_model: str, sensors: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT rainfall_onset_mm, temperature_drop_c, temperature_window_hours, event_cooldown_hours, crop_name, growth_stage, soil_type FROM site_profile WHERE id=1")
        profile = cur.fetchone()
        if not profile:
            return
        rain_threshold, drop_threshold, window_hours, cooldown, crop, stage, soil = profile
        cur.execute("SELECT observed_at, rainfall_mm FROM sensor_readings WHERE location_name=%s AND source_model=%s AND id<>%s AND observed_at<%s ORDER BY observed_at DESC LIMIT 1", (location, source_model, reading_id, observed_at))
        previous = cur.fetchone()
        cur.execute("SELECT air_temp_c FROM sensor_readings WHERE location_name=%s AND source_model=%s AND observed_at<=%s - (%s * interval '1 hour') AND air_temp_c IS NOT NULL ORDER BY observed_at DESC LIMIT 1", (location, source_model, observed_at, window_hours))
        older = cur.fetchone()
        rain = sensors.get("rainfall_mm") or 0.0
        if rain < rain_threshold or (previous and (previous[1] or 0.0) >= rain_threshold):
            return
        current_temp = sensors.get("air_temp_c")
        if current_temp is None or older is None or older[0] - current_temp < drop_threshold:
            return
        cur.execute("SELECT 1 FROM maintenance_events WHERE location_name=%s AND event_type=%s AND detected_at >= %s - (%s * interval '1 hour') LIMIT 1", (location, EVENT_TYPE, observed_at, cooldown))
        if cur.fetchone():
            return
        drop = round(float(older[0]) - float(current_temp), 2)
        details = {"temperature_window_hours": window_hours, "temperature_before_c": older[0], "temperature_now_c": current_temp, "temperature_drop_c": drop, "rainfall_interval_mm": rain, "humidity_pct": sensors.get("air_humidity_pct"), "soil_moisture_shallow_pct": sensors.get("soil_moisture_shallow_pct"), "soil_moisture_deep_pct": sensors.get("soil_moisture_deep_pct"), "soil_temp_c": sensors.get("soil_temp_c"), "light_lux": sensors.get("light_lux"), "leaf_wetness_pct": sensors.get("leaf_wetness_pct"), "source_model": source_model, "crop_name": crop, "growth_stage": stage, "soil_type": soil}
        key = f"{location}|{EVENT_TYPE}|{source_model}|{observed_at.isoformat()}"
        cur.execute("INSERT INTO maintenance_events (event_key,event_type,location_name,detected_at,severity,summary,details,research_status) VALUES (%s,%s,%s,%s,'medium',%s,%s::jsonb,'pending') ON CONFLICT(event_key) DO NOTHING", (key, EVENT_TYPE, location, observed_at, "Rain began after a significant temperature drop. Review field conditions; this is not a crop diagnosis.", json.dumps(details)))
        if cur.rowcount:
            print(f"MAINTENANCE EVENT: {key}", flush=True)
