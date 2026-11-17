"""Wokwi crop-monitoring simulator: read simulated sensors and publish MQTT data."""

import json
import math
import os
import binascii
import network
import time
import ntptime
import machine
from machine import ADC, Pin


# ---------- CONFIGURATION ----------
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASSWORD = ""

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = b"crop/field1/sensors/demo73921"
PUBLISH_INTERVAL = 10

DEVICE_ID = "wokwi-crop-sim-01"
DEVICE_SESSION_ID = binascii.hexlify(os.urandom(8)).decode()
DHT_PIN = 4

mqtt_client = None
rtc_synced = False
start_time = time.ticks_ms()


# ADC1 pins remain available while Wi-Fi is active on the simulated ESP32.
def make_adc(pin_number):
    adc = ADC(Pin(pin_number))
    adc.atten(ADC.ATTN_11DB)
    return adc


soil_moisture_shallow_adc = make_adc(34)
soil_moisture_deep_adc = make_adc(33)
soil_temperature_adc = make_adc(32)
light_adc = make_adc(35)
rainfall_adc = make_adc(36)
leaf_wetness_adc = make_adc(39)

try:
    import dht

    air_sensor = dht.DHT22(Pin(DHT_PIN))
except Exception as exc:
    air_sensor = None
    print("DHT22 setup failed:", exc)

def adc_percent(adc):
    return round(adc.read() * 100 / 4095, 1)


def read_light_lux():
    # Convert the Wokwi photoresistor module's analog output to approximate lux.
    analog_value = light_adc.read()
    voltage = analog_value * 3.3 / 4095
    if voltage <= 0.001:
        return 0.0
    if voltage >= 3.299:
        return 100000.0

    resistance = 2000 * voltage / (1 - voltage / 3.3)
    if resistance <= 0:
        return 100000.0

    gamma = 0.7
    rl10 = 50
    lux = (rl10 * 1000 * (10 ** gamma) / resistance) ** (1 / gamma)
    return round(max(0, min(100000, lux)), 1)


def read_air_conditions():
    if air_sensor is None:
        return None, None
    try:
        air_sensor.measure()
        return round(air_sensor.temperature(), 2), round(air_sensor.humidity(), 2)
    except Exception as exc:
        print("DHT22 read failed:", exc)
        return None, None


def read_soil_temperature():
    # Wokwi NTC uses a 10K thermistor and 10K divider; beta must match diagram.json.
    analog_value = soil_temperature_adc.read()
    if analog_value <= 0 or analog_value >= 4095:
        return None
    beta = 3950
    resistance_ratio = 4095 / analog_value - 1
    temp_kelvin = 1 / (math.log(1 / resistance_ratio) / beta + 1 / 298.15)
    return round(temp_kelvin - 273.15, 2)


def read_sensors():
    air_temp_c, air_humidity_pct = read_air_conditions()
    return {
        "air_temp_c": air_temp_c,
        "air_humidity_pct": air_humidity_pct,
        "soil_moisture_shallow_pct": adc_percent(soil_moisture_shallow_adc),
        "soil_moisture_deep_pct": adc_percent(soil_moisture_deep_adc),
        "soil_temp_c": read_soil_temperature(),
        "light_lux": read_light_lux(),
        # These two inputs are simulation controls, not physical measurements.
        "rainfall_mm": round(rainfall_adc.read() * 20 / 4095, 1),
        "leaf_wetness_pct": adc_percent(leaf_wetness_adc),
    }


# ---------- WIFI AND TIME ----------
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to Wi-Fi...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(30):
            if wlan.isconnected():
                break
            time.sleep(1)
    print("Wi-Fi status:", wlan.isconnected())
    return wlan.isconnected()


def sync_clock():
    global rtc_synced
    try:
        ntptime.settime()
        rtc_synced = True
        print("UTC clock synchronized")
    except Exception as exc:
        print("NTP unavailable:", exc)


def get_timestamp():
    if rtc_synced:
        now = time.localtime()
        return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (
            now[0], now[1], now[2], now[3], now[4], now[5]
        )
    elapsed = time.ticks_diff(time.ticks_ms(), start_time) // 1000
    return "uptime+%ds" % elapsed


# ---------- MQTT ----------
def connect_mqtt():
    global mqtt_client
    try:
        from umqtt.simple import MQTTClient
    except ImportError:
        try:
            import mip

            mip.install("umqtt.simple")
            from umqtt.simple import MQTTClient
        except Exception as exc:
            print("MQTT library unavailable:", exc)
            return False

    try:
        mqtt_client = MQTTClient(
            "crop-sim-" + DEVICE_SESSION_ID,
            MQTT_BROKER,
            port=MQTT_PORT,
            keepalive=60,
        )
        mqtt_client.connect()
        print("MQTT connected")
        return True
    except Exception as exc:
        mqtt_client = None
        print("MQTT connection failed:", exc)
        return False


def main():
    global mqtt_client

    if connect_wifi():
        sync_clock()
        connect_mqtt()

    sample = 0
    while True:
        payload = {
            "device_id": DEVICE_ID,
            "device_session_id": DEVICE_SESSION_ID,
            "sample_interval_seconds": PUBLISH_INTERVAL,
            "timestamp_utc": get_timestamp(),
            "sample_number": sample,
            "simulation": True,
            "sensors": read_sensors(),
        }
        message = json.dumps(payload)
        print("SENSOR:", message)

        if mqtt_client is not None:
            try:
                mqtt_client.publish(MQTT_TOPIC, message)
                print("Published:", MQTT_TOPIC.decode())
            except Exception as exc:
                print("Publish failed:", exc)
                try:
                    mqtt_client.disconnect()
                except Exception:
                    pass
                mqtt_client = None

        if mqtt_client is None:
            print("Attempting MQTT reconnect...")
            time.sleep(2)
            connect_mqtt()

        sample += 1
        time.sleep(PUBLISH_INTERVAL)


main()
