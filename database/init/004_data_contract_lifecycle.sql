ALTER TABLE sensor_readings
    ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS timestamp_quality TEXT NOT NULL DEFAULT 'legacy_unknown',
    ADD COLUMN IF NOT EXISTS device_id TEXT,
    ADD COLUMN IF NOT EXISTS device_session_id TEXT,
    ADD COLUMN IF NOT EXISTS sample_number BIGINT,
    ADD COLUMN IF NOT EXISTS soil_moisture_semantics TEXT NOT NULL DEFAULT 'unspecified',
    ADD COLUMN IF NOT EXISTS soil_moisture_shallow_top_cm DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS soil_moisture_shallow_bottom_cm DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS soil_moisture_deep_top_cm DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS soil_moisture_deep_bottom_cm DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS rainfall_interval_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS rainfall_semantics TEXT NOT NULL DEFAULT 'unspecified',
    ADD COLUMN IF NOT EXISTS quality_status TEXT NOT NULL DEFAULT 'unreviewed',
    ADD COLUMN IF NOT EXISTS quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE sensor_readings
SET source_kind = CASE
        WHEN source_model = 'wokwi-simulation' THEN 'device_simulation'
        WHEN source_model = 'dashboard-demo' THEN 'demo'
        WHEN source_model IN ('ERA5', 'open-meteo-current') THEN 'weather_model'
        ELSE 'unknown'
    END,
    received_at = COALESCE(received_at, inserted_at),
    soil_moisture_semantics = CASE
        WHEN source_model = 'wokwi-simulation' THEN 'relative_simulation_index_pct'
        WHEN source_model IN ('ERA5', 'open-meteo-current') THEN 'volumetric_water_content_pct'
        ELSE soil_moisture_semantics
    END,
    soil_moisture_shallow_top_cm = CASE
        WHEN source_model = 'ERA5' THEN 0
        WHEN source_model = 'open-meteo-current' THEN 3
        ELSE soil_moisture_shallow_top_cm
    END,
    soil_moisture_shallow_bottom_cm = CASE
        WHEN source_model = 'ERA5' THEN 7
        WHEN source_model = 'open-meteo-current' THEN 9
        ELSE soil_moisture_shallow_bottom_cm
    END,
    soil_moisture_deep_top_cm = CASE
        WHEN source_model = 'ERA5' THEN 7
        WHEN source_model = 'open-meteo-current' THEN 9
        ELSE soil_moisture_deep_top_cm
    END,
    soil_moisture_deep_bottom_cm = CASE
        WHEN source_model = 'ERA5' THEN 28
        WHEN source_model = 'open-meteo-current' THEN 27
        ELSE soil_moisture_deep_bottom_cm
    END,
    rainfall_semantics = CASE
        WHEN source_model IN ('ERA5', 'open-meteo-current') THEN 'preceding_interval_sum_mm'
        WHEN source_model = 'wokwi-simulation' THEN 'simulated_control_per_sample_mm'
        ELSE rainfall_semantics
    END;

CREATE INDEX IF NOT EXISTS sensor_readings_source_kind_time_idx
    ON sensor_readings (source_kind, observed_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS sensor_readings_device_sample_uidx
    ON sensor_readings (device_id, device_session_id, sample_number)
    WHERE device_id IS NOT NULL AND device_session_id IS NOT NULL AND sample_number IS NOT NULL;

ALTER TABLE site_profile
    ADD COLUMN IF NOT EXISTS crop_variety TEXT,
    ADD COLUMN IF NOT EXISTS planting_date DATE,
    ADD COLUMN IF NOT EXISTS soil_ph DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS soil_ec_ds_m DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS field_capacity_vwc DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS wilting_point_vwc DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS rooting_depth_m DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS moisture_warning_threshold_pct DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS threshold_evidence TEXT,
    ADD COLUMN IF NOT EXISTS moisture_calibration_version TEXT;

ALTER TABLE maintenance_events
    ADD COLUMN IF NOT EXISTS source_model TEXT,
    ADD COLUMN IF NOT EXISTS rule_version TEXT,
    ADD COLUMN IF NOT EXISTS event_status TEXT NOT NULL DEFAULT 'open',
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS resolution_note TEXT,
    ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE maintenance_events
    ADD COLUMN IF NOT EXISTS research_attempts SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS research_next_attempt_at TIMESTAMPTZ;

UPDATE maintenance_events
SET source_model = COALESCE(source_model, details->>'source_model'),
    event_status = CASE
        WHEN resolved_at IS NOT NULL THEN 'resolved'
        WHEN acknowledged_at IS NOT NULL THEN 'acknowledged'
        ELSE event_status
    END;

CREATE TABLE IF NOT EXISTS field_actions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT REFERENCES maintenance_events(id) ON DELETE CASCADE,
    location_name TEXT NOT NULL,
    action_type TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT
);
CREATE INDEX IF NOT EXISTS field_actions_event_time_idx
    ON field_actions (event_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS forecast_records (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    location_name TEXT NOT NULL,
    source_model TEXT NOT NULL,
    target TEXT NOT NULL,
    horizon_hours SMALLINT NOT NULL,
    origin_time TIMESTAMPTZ NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_time TIMESTAMPTZ NOT NULL,
    predicted_value DOUBLE PRECISION NOT NULL,
    unit TEXT NOT NULL,
    method TEXT NOT NULL,
    model_version TEXT NOT NULL,
    training_source TEXT NOT NULL,
    historical_validation_mae DOUBLE PRECISION,
    actual_value DOUBLE PRECISION,
    evaluated_at TIMESTAMPTZ,
    UNIQUE(location_name, source_model, target, horizon_hours, origin_time, model_version)
);
CREATE INDEX IF NOT EXISTS forecast_records_valid_idx
    ON forecast_records (location_name, target, valid_time DESC);
