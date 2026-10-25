
import network
import time
import json
import random
import math
import ntptime
import machine

# ---------- CONFIGURATION ----------
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASSWORD = ""

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = b"crop/field1/sensors/demo73921"

PUBLISH_INTERVAL = 10

mqtt_client = None
rtc_synced = False
start_time = time.ticks_ms()

# ---------- WIFI ----------
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


# ---------- TIMESTAMP ----------
def sync_clock():
    global rtc_synced

    try:
        ntptime.settime()
        rtc_synced = True
        print("UTC clock synchronized")
    except Exception as e:
        print("NTP unavailable:", e)


def get_timestamp():
    if rtc_synced:
        t = time.localtime()
        return (
            "%04d-%02d-%02dT%02d:%02d:%02dZ"
            % (t[0], t[1], t[2], t[3], t[4], t[5])
        )

    elapsed = time.ticks_diff(
        time.ticks_ms(), start_time
    ) // 1000

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
        except Exception as e:
            print("MQTT library unavailable:", e)
            return False

    try:
        mqtt_client = MQTTClient(
            "crop-" + str(random.getrandbits(24)),
            MQTT_BROKER,
            port=MQTT_PORT,
            keepalive=60
        )
        mqtt_client.connect()
        print("MQTT connected")
        return True

    except Exception as e:
        mqtt_client = None
        print("MQTT connection failed:", e)
        return False


# ---------- VIRTUAL CROP SENSORS ----------
def read_sensors(sample):
    # Simulated day/night temperature cycle
    phase = sample / 8
    temperature = (
        27 + 5 * max(0, math.sin(phase))
        + random.uniform(-1, 1)
    )

    humidity = (
        78 - (temperature - 27) * 2
        + random.uniform(-3, 3)
    )

    # Soil moisture decreases between irrigation events
    soil_moisture = (
        70 - (sample % 30) * 1.8
        + random.uniform(-2, 2)
    )

    light = max(
        0,
        int(800 * max(0, math.sin(phase))
            + random.randint(-30, 30))
    )

    ph = 6.5 + random.uniform(-0.3, 0.3)

    # Inject a hot/dry anomaly every 25 samples
    anomaly = sample > 0 and sample % 25 == 0

    if anomaly:
        temperature += 7
        humidity -= 15
        soil_moisture -= 18

    # Keep values within plausible simulation ranges
    temperature = round(temperature, 2)
    humidity = round(max(10, min(100, humidity)), 2)
    soil_moisture = round(
        max(0, min(100, soil_moisture)), 2
    )
    light = max(0, min(1000, light))
    ph = round(max(0, min(14, ph)), 2)

    risk = (
        "HIGH" if soil_moisture < 25 or temperature > 38
        else "MEDIUM" if soil_moisture < 40 or temperature > 34
        else "LOW"
    )

    return {
        "temperature": temperature,
        "humidity": humidity,
        "soil_moisture": soil_moisture,
        "light": light,
        "ph": ph,
        "simulated_anomaly": anomaly,
        "risk": risk
    }


# ---------- MAIN LOOP ----------
def main():
    global mqtt_client

    if connect_wifi():
        sync_clock()
        connect_mqtt()

    sample = 0

    while True:
        data = read_sensors(sample)

        payload = {
            "device_id": "esp32-crop-01",
            "timestamp_utc": get_timestamp(),
            "sample_number": sample
        }

        payload.update(data)

        message = json.dumps(payload)
        print("SENSOR:", message)

        
        if mqtt_client is not None:
            try:
                mqtt_client.publish(MQTT_TOPIC, message)
                print("Published:", MQTT_TOPIC.decode())

            except Exception as e:
                print("Publish failed:", e)

                try:
                    mqtt_client.disconnect()
                except Exception:
                    pass

                mqtt_client = None

        # Attempt reconnection if disconnected
        if mqtt_client is None:
            print("Attempting MQTT reconnect...")
            time.sleep(2)
            connect_mqtt()


        sample += 1
        time.sleep(PUBLISH_INTERVAL)


main()
