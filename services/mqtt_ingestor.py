"""Validate and persist simulated device observations from MQTT."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg

from services.data_quality import MAX_MQTT_PAYLOAD_BYTES, build_measurement_metadata, normalize_sensor_values, parse_observation_time
from services.event_rules import create_events_for_reading

DATABASE_URL = os.environ["DATABASE_URL"]
BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC = os.getenv("MQTT_TOPIC", "crop/field1/sensors/demo73921")


def on_connect(client, _userdata, _flags, reason_code, _properties=None):
    print(f"MQTT connection result: {reason_code}", flush=True)
    if reason_code == 0:
        client.subscribe(TOPIC)
        print(f"Subscribed to {TOPIC}", flush=True)


def on_message(_client, _userdata, message):
    try:
        if len(message.payload) > MAX_MQTT_PAYLOAD_BYTES:
            raise ValueError("MQTT payload exceeds 16 KiB")
        payload = json.loads(message.payload.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        sensors = normalize_sensor_values(payload.get("sensors"))
        device_id = payload.get("device_id")
        session_id = payload.get("device_session_id")
        sample_number = payload.get("sample_number")
        if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 128:
            raise ValueError("device_id is required and must be at most 128 characters")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
            raise ValueError("device_session_id is required and must be at most 128 characters")
        if isinstance(sample_number, bool) or not isinstance(sample_number, int) or sample_number < 0:
            raise ValueError("sample_number must be a non-negative integer")
        interval_seconds = payload.get("sample_interval_seconds")
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or not 1 <= interval_seconds <= 3600:
            raise ValueError("sample_interval_seconds must be an integer from 1 to 3600")

        received_at = datetime.now(timezone.utc)
        observed_at, timestamp_quality = parse_observation_time(payload.get("timestamp_utc"), received_at)
        metadata = build_measurement_metadata(sensors, "device_simulation", {
            "soil_moisture_shallow_pct": {"semantics":"relative_simulation_index_pct"},
            "soil_moisture_deep_pct": {"semantics":"relative_simulation_index_pct"},
            "rainfall_mm": {"interval_seconds":interval_seconds,"semantics":"simulated_control_per_sample_mm"},
            "light_lux": {"semantics":"approximate_photoresistor_conversion"},
        })
        with psycopg.connect(DATABASE_URL) as conn:
            profile = conn.execute(
                "SELECT location_name,latitude,longitude FROM site_profile WHERE id=1"
            ).fetchone()
            if not profile:
                raise RuntimeError("No active site profile exists")
            location, latitude, longitude = profile
            raw = {
                **payload,
                "source": "wokwi-mqtt",
                "source_kind": "device_simulation",
                "timestamp_quality": timestamp_quality,
                "received_at": received_at.isoformat(),
                "measurement_metadata": metadata,
            }
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO sensor_readings (
                         observed_at,location_name,latitude,longitude,source,source_model,is_simulated,
                         air_temp_c,air_humidity_pct,soil_moisture_shallow_pct,soil_moisture_deep_pct,
                         soil_temp_c,solar_radiation_w_m2,rainfall_mm,leaf_wetness_pct,
                         leaf_wetness_is_estimated,light_lux,raw_json,source_kind,received_at,
                         timestamp_quality,device_id,device_session_id,sample_number,measurement_metadata,
                         soil_moisture_semantics,rainfall_interval_seconds,rainfall_semantics,quality_status,quality_flags
                       ) VALUES (
                         %s,%s,%s,%s,'wokwi-mqtt','wokwi-simulation',TRUE,
                         %s,%s,%s,%s,%s,NULL,%s,%s,FALSE,%s,%s::jsonb,'device_simulation',%s,
                         %s,%s,%s,%s,%s::jsonb,'relative_simulation_index_pct',%s,
                         'simulated_control_per_sample_mm','valid','[]'::jsonb
                       ) ON CONFLICT DO NOTHING RETURNING id""",
                    (
                        observed_at, location, latitude, longitude,
                        sensors["air_temp_c"], sensors["air_humidity_pct"],
                        sensors["soil_moisture_shallow_pct"], sensors["soil_moisture_deep_pct"],
                        sensors["soil_temp_c"], sensors["rainfall_mm"], sensors["leaf_wetness_pct"],
                        sensors["light_lux"], json.dumps(raw), received_at, timestamp_quality,
                        device_id, session_id, sample_number, json.dumps(metadata), interval_seconds,
                    ),
                )
                inserted = cur.fetchone()
                if inserted:
                    sensors["source_kind"] = "device_simulation"
                    sensors["soil_moisture_semantics"] = "relative_simulation_index_pct"
                    sensors["rainfall_interval_seconds"] = interval_seconds
                    create_events_for_reading(
                        conn, inserted[0], observed_at, location, "wokwi-simulation", sensors
                    )
            conn.commit()
        if inserted:
            print(f"Saved {device_id} sample {sample_number} at {observed_at.isoformat()}", flush=True)
    except Exception as exc:
        print(f"Could not process MQTT message: {type(exc).__name__}: {exc}", flush=True)


def main():
    client_id = os.getenv("MQTT_CLIENT_ID", "crop-management-ingestor")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    username, password = os.getenv("MQTT_USERNAME"), os.getenv("MQTT_PASSWORD")
    if username:
        client.username_pw_set(username, password)
    if os.getenv("MQTT_TLS", "false").lower() == "true":
        client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message
    print(f"Connecting MQTT ingestor to {BROKER}:{PORT}", flush=True)
    client.connect(BROKER, PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
