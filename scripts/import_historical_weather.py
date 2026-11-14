"""Import one year of hourly Kapriwas reanalysis data into PostgreSQL."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg

from services.data_quality import build_measurement_metadata


DATABASE_URL = os.environ.get("DATABASE_URL")
API_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = ",".join(
    (
        "temperature_2m",
        "relative_humidity_2m",
        "precipitation",
        "shortwave_radiation",
        "soil_temperature_0_to_7cm",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
    )
)


def fetch_period(start: date, end: date, profile: dict) -> dict:
    params = urlencode(
        {
            "latitude": profile["latitude"],
            "longitude": profile["longitude"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": HOURLY,
            "timezone": profile["timezone"],
            "models": "era5",
        }
    )
    request = Request(API_URL + "?" + params, headers={"User-Agent": "CropManagement/0.1"})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def estimated_leaf_wetness(humidity: float | None, rain: float | None, temp: float | None, state: float) -> float:
    """A declared synthetic proxy; not a measured leaf-wetness value."""
    humidity = humidity or 0.0
    rain = rain or 0.0
    if rain >= 0.1:
        state = max(state, min(100.0, 35.0 + rain * 20.0))
    elif humidity >= 90 and temp is not None and 5 <= temp <= 30:
        state = min(100.0, state + 12.0)
    else:
        state = max(0.0, state - 18.0)
    return round(state, 1)


def import_rows(conn: psycopg.Connection, payload: dict, profile: dict) -> int:
    hourly = payload["hourly"]
    times = hourly["time"]
    fields = (
        "temperature_2m",
        "relative_humidity_2m",
        "precipitation",
        "shortwave_radiation",
        "soil_temperature_0_to_7cm",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
    )
    if any(len(hourly[name]) != len(times) for name in fields):
        raise ValueError("Weather API returned hourly arrays with mismatched lengths")

    rows = []
    leaf_wetness = 0.0
    for index, timestamp in enumerate(times):
        values = {name: hourly[name][index] for name in fields}
        air_temp = values["temperature_2m"]
        humidity = values["relative_humidity_2m"]
        rain = values["precipitation"]
        leaf_wetness = estimated_leaf_wetness(humidity, rain, air_temp, leaf_wetness)
        measurements = {
            "air_temp_c": air_temp, "air_humidity_pct": humidity,
            "soil_moisture_shallow_pct": None if values["soil_moisture_0_to_7cm"] is None else values["soil_moisture_0_to_7cm"] * 100,
            "soil_moisture_deep_pct": None if values["soil_moisture_7_to_28cm"] is None else values["soil_moisture_7_to_28cm"] * 100,
            "soil_temp_c": values["soil_temperature_0_to_7cm"], "solar_radiation_w_m2": values["shortwave_radiation"],
            "rainfall_mm": rain, "leaf_wetness_pct": leaf_wetness,
        }
        metadata = build_measurement_metadata(measurements, "weather_model", {
            "soil_moisture_shallow_pct": {"semantics":"volumetric_water_content_pct","depth_cm":[0,7]},
            "soil_moisture_deep_pct": {"semantics":"volumetric_water_content_pct","depth_cm":[7,28]},
            "rainfall_mm": {"interval_seconds":3600,"semantics":"preceding_interval_sum_mm"},
            "leaf_wetness_pct": {"source_kind":"derived_estimate","is_estimated":True,"method":"synthetic rain-humidity-temperature proxy"},
        })
        observed_at = datetime.fromisoformat(timestamp).replace(
            tzinfo=ZoneInfo(profile["timezone"])
        )
        rows.append(
            (
                observed_at,
                profile["location_name"],
                profile["latitude"],
                profile["longitude"],
                "open-meteo-historical-reanalysis",
                "ERA5",
                True,
                air_temp,
                humidity,
                None if values["soil_moisture_0_to_7cm"] is None else values["soil_moisture_0_to_7cm"] * 100,
                None if values["soil_moisture_7_to_28cm"] is None else values["soil_moisture_7_to_28cm"] * 100,
                values["soil_temperature_0_to_7cm"],
                values["shortwave_radiation"],
                rain,
                leaf_wetness,
                True,
                json.dumps({"time": timestamp, **values, "leaf_wetness_proxy_pct": leaf_wetness,
                            "source_kind": "weather_model", "soil_moisture_semantics": "volumetric_water_content_pct",
                            "measurement_metadata": metadata}),
                json.dumps(metadata),
                datetime.now(ZoneInfo(profile["timezone"])),
                0, 7, 7, 28,
            )
        )

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sensor_readings (
                observed_at, location_name, latitude, longitude, source, source_model,
                is_simulated, air_temp_c, air_humidity_pct,
                soil_moisture_shallow_pct, soil_moisture_deep_pct, soil_temp_c,
                solar_radiation_w_m2, rainfall_mm, leaf_wetness_pct,
                leaf_wetness_is_estimated, raw_json,measurement_metadata,
                source_kind,received_at,timestamp_quality,
                soil_moisture_semantics,soil_moisture_shallow_top_cm,
                soil_moisture_shallow_bottom_cm,soil_moisture_deep_top_cm,
                soil_moisture_deep_bottom_cm,rainfall_interval_seconds,rainfall_semantics,quality_status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,%s::jsonb,
                'weather_model',%s,'model_timestamp','volumetric_water_content_pct',%s,%s,%s,%s,
                3600,'preceding_interval_sum_mm','valid'
            ) ON CONFLICT (observed_at, location_name, source_model) DO NOTHING
            """,
            rows,
        )
        return cur.rowcount


def main() -> None:
    if not DATABASE_URL:
        raise SystemExit("Set DATABASE_URL in .env before running this importer")

    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute("SELECT location_name,latitude,longitude,timezone FROM site_profile WHERE id=1").fetchone()
        if not row:
            raise SystemExit("No active site profile exists")
        profile = dict(zip(("location_name","latitude","longitude","timezone"),row))
        # ERA5 archive updates with a several-day delay; stop at its latest complete day locally.
        end_date = datetime.now(ZoneInfo(profile["timezone"])).date() - timedelta(days=6)
        start_date = end_date - timedelta(days=364)
        print("Fetching", start_date, "through", end_date, "for", profile["location_name"], flush=True)
        payload = fetch_period(start_date, end_date, profile)
        inserted = import_rows(conn, payload, profile)
        conn.commit()

    print(f"Imported {inserted} hourly rows for {profile['location_name']}.")
    print("All environmental values are reanalysis/model estimates, not Wokwi hardware measurements.")


if __name__ == "__main__":
    main()
