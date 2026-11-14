CREATE TABLE IF NOT EXISTS sensor_readings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    location_name TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL,
    source_model TEXT NOT NULL,
    is_simulated BOOLEAN NOT NULL DEFAULT TRUE,
    air_temp_c DOUBLE PRECISION,
    air_humidity_pct DOUBLE PRECISION,
    soil_moisture_shallow_pct DOUBLE PRECISION,
    soil_moisture_deep_pct DOUBLE PRECISION,
    soil_temp_c DOUBLE PRECISION,
    light_w_m2 DOUBLE PRECISION,
    solar_radiation_w_m2 DOUBLE PRECISION,
    rainfall_mm DOUBLE PRECISION,
    leaf_wetness_pct DOUBLE PRECISION,
    leaf_wetness_is_estimated BOOLEAN NOT NULL DEFAULT TRUE,
    raw_json JSONB NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (observed_at, location_name, source_model)
);

CREATE INDEX IF NOT EXISTS sensor_readings_location_time_idx
    ON sensor_readings (location_name, observed_at);
