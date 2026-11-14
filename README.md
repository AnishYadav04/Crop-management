# Crop Monitoring Simulation

An ESP32 crop-monitoring simulation for Wokwi. It publishes eight simulated sensor values over MQTT. The Docker stack stores both Wokwi and model-based weather observations in PostgreSQL, applies configurable maintenance rules, and provides a local dashboard.

## Simulated readings

| Reading | Wokwi input |
| --- | --- |
| Air temperature | DHT22 |
| Air humidity | DHT22 |
| Upper root-zone soil moisture | Potentiometer on GPIO 34 |
| Deeper root-zone soil moisture | Potentiometer on GPIO 33 |
| Soil temperature | NTC temperature sensor on GPIO 32 |
| Light level | Photoresistor on GPIO 35, converted to approximate lux |
| Rainfall | Potentiometer on GPIO 36, mapped to 0–20 mm per sample |
| Leaf wetness | Potentiometer on GPIO 39, mapped to 0–100% |

The soil moisture, rainfall, and leaf wetness controls are simulation inputs. They do not measure real soil or weather. The rainfall value represents the simulated amount for one sample interval. Sensor data is marked with `"simulation": true` in each MQTT message. The project publishes every 10 seconds.

## Run the simulation

1. Install the [Wokwi for VS Code](https://marketplace.visualstudio.com/items?itemName=Wokwi.wokwi-vscode) extension.
2. Open this project in VS Code and choose **Wokwi: Start Simulator**.
3. Select the `esp32` directory.
4. Adjust the DHT22 and NTC temperature controls and the potentiometers while the simulation runs.
5. Start the PostgreSQL and monitor stack (Docker Desktop must be running):

   ```shell
   docker compose up -d --build
   ```

The `mqtt-ingestor` container listens on `broker.hivemq.com:1883`, topic `crop/field1/sensors/demo73921`, and stores readings in PostgreSQL. Keep Wokwi running to collect the manually controlled stream. For a host-based subscriber, configure `.env`, install requirements, then run `python esp32/subscriber.py`.

## Message shape

```json
{
  "device_id": "wokwi-crop-sim-01",
  "device_session_id": "unique-per-simulator-boot",
  "sample_interval_seconds": 10,
  "timestamp_utc": "2026-10-31T00:00:00Z",
  "sample_number": 0,
  "simulation": true,
  "sensors": {
    "air_temp_c": 28.0,
    "air_humidity_pct": 65.0,
    "soil_moisture_shallow_pct": 68.4,
    "soil_moisture_deep_pct": 74.3,
    "soil_temp_c": 24.0,
    "light_lux": 250.0,
    "rainfall_mm": 0.0,
    "leaf_wetness_pct": 0.0
  }
}
```

Light output is approximate and the potentiometer percentages are normalized simulation values, not calibrated agronomic measurements. Thresholds and model outputs should therefore be treated as prototypes.

## Historical dataset in PostgreSQL

The project includes a PostgreSQL 18 container and a one-year hourly historical importer. It reads the location, coordinates, and timezone from the active database site profile, currently Kapriwas, Rewari (28.24331, 76.80864). Start Docker Desktop, then run:

```powershell
docker compose up -d
$dbLine = Get-Content .env | Where-Object { $_ -like 'DATABASE_URL=*' }
$env:DATABASE_URL = $dbLine.Substring('DATABASE_URL='.Length)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\import_historical_weather.py
```

`.env` contains local database credentials and is ignored by Git. The importer uses the Open-Meteo Historical Weather API and writes hourly ERA5 reanalysis for the latest complete year available. It is safe to rerun; duplicate location/model/hour rows are skipped. Data is timestamped in `Asia/Kolkata`.

Historical temperature, humidity, rainfall, solar radiation, soil temperature, and soil moisture are weather-model estimates, not measurements from Wokwi or a physical device. ERA5 provides soil moisture at 0–7 cm and 7–28 cm; leaf wetness is an explicitly estimated proxy based on rain, humidity, and temperature. Historical light is stored as solar radiation in W/m² rather than the Wokwi photoresistor's approximate lux. pH is not available in this weather archive.

One year gives about 8,760 hourly rows and is a useful first dataset for experimenting with environmental time-series forecasts. It does not label crop problems, so it cannot by itself train a reliable model to decide whether a crop needs attention. That will require crop-specific information and observed or expert-labeled stress events. Open-Meteo's historical archive is based on reanalysis and fills gaps with model estimates; see its [documentation](https://open-meteo.com/en/docs/historical-weather-api).

## Train environmental forecasts

Install requirements, ensure PostgreSQL is running, and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\train_forecasters.py
```

The training script audits hourly coverage and missing values, then evaluates direct 1-, 6-, and 24-hour forecasts for air temperature, air humidity, both soil-moisture depths, soil temperature, rainfall, and solar radiation. It compares a gradient-boosted tree model with persistence, daily-seasonal, and weekly-seasonal baselines using expanding-window time splits. It writes `data_quality.json`, `metrics.csv`, `oof_predictions.csv`, `run_summary.json`, and refitted model files under `artifacts/forecasting/`.

These are environmental forecasts. The current archive does not support a trained crop-attention classifier. Such a classifier needs crop-specific context and observed stress labels. The evaluation uses chronological folds to keep future observations out of each training window; see [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

The Wokwi ESP32 MQTT stream and historical weather archive now share PostgreSQL, while retaining separate source labels (`wokwi-simulation`, `ERA5`, and `open-meteo-current`).

See [FORECASTING_PLAN.md](FORECASTING_PLAN.md) for the staged plan from data quality and baselines through crop-specific alert modeling and dashboard integration.

## Live maintenance monitor

The Docker Compose stack runs a monitor, MQTT ingestor, PostgreSQL, a background research agent, and a local dashboard. Start or rebuild it with:

```powershell
docker compose up -d --build
docker compose logs -f live-monitor
```

Open [http://localhost:8000](http://localhost:8000) for current readings, alert history, research links, acknowledgement/action logging, and the crop profile/threshold form. Use **Ask crop agent** in the top navigation or open [http://localhost:8000/assistant](http://localhost:8000/assistant) to ask crop and farm-management questions. Each conversation is saved in PostgreSQL; answers include web-source links. If `OPENAI_API_KEY` is configured, the assistant synthesizes a web-searched answer. Without it, it returns relevant search-result excerpts and clearly labels them as excerpts. Click **Enable notifications** and grant browser permission to receive new event alerts while the dashboard is open. **Create demo alert** creates a clearly labeled synthetic event for presentations. Email, SMS, and push alerts while the dashboard is closed are not configured. The dashboard binds to localhost. The MQTT endpoint is a public demo broker and is only suitable for simulation; use an authenticated private broker with TLS before handling real sensor or private farm data. To configure one, set `MQTT_BROKER`, `MQTT_PORT`, `MQTT_TLS`, `MQTT_USERNAME`, `MQTT_PASSWORD`, and the same `MQTT_TOPIC` in `.env` and `esp32/main.py`.

To enable synthesized agent answers, add `OPENAI_API_KEY` and (optionally) `OPENAI_RESEARCH_MODEL` to the local, Git-ignored `.env`, then rebuild the dashboard service with `docker compose up -d --build dashboard`. Keep the key out of source files and Git. Web search excerpts continue to work without the key.

Every 15 minutes the service reads Open-Meteo current model conditions, stores the eight sensor-like values in PostgreSQL, and marks them as simulated/model-derived. Air temperature, humidity, rainfall, solar radiation, soil temperature, and soil moisture come from the weather model. Leaf wetness is a simple synthetic proxy. These are not direct Wokwi or physical-sensor readings; Open-Meteo current conditions are based on weather-model data. Wokwi MQTT readings are also stored in PostgreSQL as a separately labeled simulation source.

The versioned informational rule creates an event when rain starts after a configurable temperature drop, with missing-rain and source-gap checks, bounded temperature matching, and source-specific cooldown. Research uses the no-key web-search fallback by default. If `OPENAI_API_KEY` is configured locally, the agent uses the OpenAI Responses API web-search tool to write a cautious cited summary; source links are stored with the event. Trusted-domain matching uses hostname boundaries, and stale `searching` jobs return to the queue after a worker crash. The site profile is set to Sugarcane for Kapriwas; growth stage and soil remain unspecified until provided. Crop context can be changed in the dashboard. Moisture thresholds remain unset until field calibration and evidence are available. A weather pattern is not a confirmed crop diagnosis. See [SUGARCANE_PROFILE.md](SUGARCANE_PROFILE.md) for the local research summary and presentation guidance.

The forecast panel is gated on 169 consecutive completed hourly observations with source coverage, including the 168-hour lag required by the model. Training and serving share the same causal feature code. Rainfall intervals are summed while state variables are averaged. The app persists forecast origin/valid times, model version, method, training source, and historical holdout MAE, then attaches later hourly readings for prospective evaluation. The initial models were trained on ERA5 archive rows; transfer to the live weather-model feed is unvalidated, so forecasts do not trigger agronomic actions.

Database changes are versioned in `database/init/*.sql` and applied by the one-off `migrate` Compose service. Observations record source category, receive time, timestamp quality, soil-moisture meaning/depth, rainfall interval semantics, per-measurement units/provenance, and quality status. Derived leaf-wetness estimates are identified separately from source weather data. Reading APIs return named fields and support source-isolated queries. Event acknowledgement and resolution are separate, and field actions appear in history even when notes are blank.

To inspect readings and events:

```powershell
docker compose exec postgres psql -U crop_app -d crop_management
```

```sql
SELECT observed_at, air_temp_c, air_humidity_pct, rainfall_mm,
       soil_moisture_shallow_pct, soil_moisture_deep_pct,
       leaf_wetness_pct, source_model
FROM sensor_readings
WHERE location_name = 'Kapriwas, Rewari, Haryana, India'
ORDER BY observed_at DESC
LIMIT 20;

SELECT detected_at, event_type, severity, research_status,
       research_note, sources
FROM maintenance_events
ORDER BY detected_at DESC;
```
