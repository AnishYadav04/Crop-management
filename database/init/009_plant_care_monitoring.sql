ALTER TABLE site_profile
    ADD COLUMN IF NOT EXISTS simulation_device_id TEXT NOT NULL DEFAULT 'wokwi-crop-sim-01',
    ADD COLUMN IF NOT EXISTS sim_moisture_low DOUBLE PRECISION NOT NULL DEFAULT 30,
    ADD COLUMN IF NOT EXISTS sim_moisture_high DOUBLE PRECISION NOT NULL DEFAULT 80;
CREATE TABLE IF NOT EXISTS sensor_hourly (
    location_name TEXT NOT NULL, source_model TEXT NOT NULL, device_id TEXT NOT NULL,
    hour_start TIMESTAMPTZ NOT NULL, sample_count INTEGER NOT NULL,
    estimated_coverage DOUBLE PRECISION NOT NULL, values_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(location_name,source_model,device_id,hour_start)
);
CREATE TABLE IF NOT EXISTS weather_outlook (
    location_name TEXT PRIMARY KEY, fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    hourly JSONB NOT NULL, timezone TEXT NOT NULL
);
