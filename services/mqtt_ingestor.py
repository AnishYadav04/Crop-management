"""Persist Wokwi MQTT readings to PostgreSQL."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg

from services.event_rules import create_events_for_reading

DATABASE_URL = os.environ["DATABASE_URL"]
BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC = os.getenv("MQTT_TOPIC", "crop/field1/sensors/demo73921")
LOCATION = os.getenv("LOCATION_NAME", "Kapriwas, Rewari, Haryana, India")
LATITUDE = float(os.getenv("LOCATION_LATITUDE", "28.24331"))
LONGITUDE = float(os.getenv("LOCATION_LONGITUDE", "76.80864"))


def on_connect(client, _userdata, _flags, reason_code, _properties=None):
    print(f"MQTT connection result: {reason_code}", flush=True)
    if reason_code == 0:
        client.subscribe(TOPIC)
        print(f"Subscribed to {TOPIC}", flush=True)


def on_message(_client, _userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8"))
        sensors = payload.get("sensors") or {}
        if not isinstance(sensors, dict):
            raise ValueError("sensors must be a JSON object")
        raw_time = payload.get("timestamp_utc")
        try:
            observed = datetime.fromisoformat(raw_time.replace("Z", "+00:00")) if raw_time else datetime.now(timezone.utc)
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            observed = datetime.now(timezone.utc)
        observed = observed.astimezone(timezone.utc)
        names = ("air_temp_c", "air_humidity_pct", "soil_moisture_shallow_pct", "soil_moisture_deep_pct", "soil_temp_c", "solar_radiation_w_m2", "rainfall_mm", "leaf_wetness_pct", "light_lux")
        values = {name: sensors.get(name) for name in names}
        raw = {**payload, "source": "wokwi-mqtt", "timestamp_quality": "device-clock" if raw_time and not str(raw_time).startswith("uptime+") else "broker-receive-time"}
        if raw["timestamp_quality"] == "broker-receive-time":
            observed = datetime.now(timezone.utc)
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO sensor_readings (observed_at,location_name,latitude,longitude,source,source_model,is_simulated,air_temp_c,air_humidity_pct,soil_moisture_shallow_pct,soil_moisture_deep_pct,soil_temp_c,solar_radiation_w_m2,rainfall_mm,leaf_wetness_pct,leaf_wetness_is_estimated,light_lux,raw_json) VALUES (%s,%s,%s,%s,'wokwi-mqtt','wokwi-simulation',TRUE,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s::jsonb) ON CONFLICT (observed_at,location_name,source_model) DO NOTHING RETURNING id", (observed, LOCATION, LATITUDE, LONGITUDE, values["air_temp_c"], values["air_humidity_pct"], values["soil_moisture_shallow_pct"], values["soil_moisture_deep_pct"], values["soil_temp_c"], values["solar_radiation_w_m2"], values["rainfall_mm"], values["leaf_wetness_pct"], values["light_lux"], json.dumps(raw)))
                inserted = cur.fetchone()
                if inserted:
                    create_events_for_reading(conn, inserted[0], observed, LOCATION, "wokwi-simulation", values)
            conn.commit()
        if inserted:
            print(f"Saved Wokwi sample {payload.get('sample_number')} at {observed.isoformat()}", flush=True)
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
