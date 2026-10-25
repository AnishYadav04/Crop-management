
import json
import sqlite3
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# MQTT configuration — must match your ESP32 topic
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "crop/field1/sensors/demo73921"

# Database will be created in this folder
DB_NAME = "crop_monitoring.db"


def setup_database():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            timestamp_utc TEXT,
            device_id TEXT,
            sample_number INTEGER,
            temperature REAL,
            humidity REAL,
            soil_moisture REAL,
            light REAL,
            ph REAL,
            simulated_anomaly INTEGER,
            risk TEXT,
            raw_json TEXT
        )
    """)
    conn.commit()
    conn.close()
    print(f"Database ready: {DB_NAME}")


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"MQTT connection result: {reason_code}")
    if reason_code == 0:
        client.subscribe(MQTT_TOPIC)
        print(f"Subscribed to: {MQTT_TOPIC}")
        print("Waiting for sensor readings...")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))

        conn = sqlite3.connect(DB_NAME)
        conn.execute("""
            INSERT INTO sensor_readings (
                received_at, timestamp_utc, device_id, sample_number,
                temperature, humidity, soil_moisture, light, ph,
                simulated_anomaly, risk, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            data.get("timestamp_utc"),
            data.get("device_id"),
            data.get("sample_number"),
            data.get("temperature"),
            data.get("humidity"),
            data.get("soil_moisture"),
            data.get("light"),
            data.get("ph"),
            int(bool(data.get("simulated_anomaly", False))),
            data.get("risk"),
            json.dumps(data)
        ))
        conn.commit()
        conn.close()

        print(
            f"Saved sample {data.get('sample_number')}: "
            f"temperature={data.get('temperature')}°C, "
            f"humidity={data.get('humidity')}%, "
            f"soil moisture={data.get('soil_moisture')}%, "
            f"pH={data.get('ph')}"
        )

    except Exception as exc:
        print(f"Could not process message: {exc}")


setup_database()

client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="crop-subscriber-demo73921"
)
client.on_connect = on_connect
client.on_message = on_message

print(f"Connecting to {MQTT_BROKER}:{MQTT_PORT} ...")

try:
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()
except KeyboardInterrupt:
    print("\nSubscriber stopped.")
    client.disconnect()
except Exception as exc:
    print(f"Subscriber error: {exc}")
