ALTER TABLE sensor_readings
    ADD COLUMN IF NOT EXISTS light_lux DOUBLE PRECISION;

ALTER TABLE maintenance_events
    ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ;
ALTER TABLE maintenance_events
    ADD COLUMN IF NOT EXISTS action_taken TEXT;
ALTER TABLE maintenance_events
    ADD COLUMN IF NOT EXISTS action_notes TEXT;
ALTER TABLE maintenance_events
    ADD COLUMN IF NOT EXISTS action_recorded_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS site_profile (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    location_name TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    crop_name TEXT NOT NULL DEFAULT 'unspecified',
    growth_stage TEXT NOT NULL DEFAULT 'unspecified',
    soil_type TEXT NOT NULL DEFAULT 'unspecified',
    rainfall_onset_mm DOUBLE PRECISION NOT NULL DEFAULT 0.1 CHECK (rainfall_onset_mm >= 0),
    temperature_drop_c DOUBLE PRECISION NOT NULL DEFAULT 2.0 CHECK (temperature_drop_c >= 0),
    temperature_window_hours SMALLINT NOT NULL DEFAULT 6 CHECK (temperature_window_hours > 0),
    event_cooldown_hours SMALLINT NOT NULL DEFAULT 6 CHECK (event_cooldown_hours >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO site_profile (
    id, location_name, latitude, longitude, crop_name, growth_stage, soil_type
)
VALUES (
    1, 'Kapriwas, Rewari, Haryana, India', 28.24331, 76.80864,
    'Sugarcane', 'unspecified', 'unspecified'
)
ON CONFLICT (id) DO NOTHING;
