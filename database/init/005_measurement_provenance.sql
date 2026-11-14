ALTER TABLE sensor_readings
    ADD COLUMN IF NOT EXISTS measurement_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE sensor_readings
SET measurement_metadata = jsonb_build_object(
    'air_temp_c', jsonb_build_object('source_kind', source_kind, 'unit', 'degC'),
    'air_humidity_pct', jsonb_build_object('source_kind', source_kind, 'unit', 'percent'),
    'soil_moisture_shallow_pct', jsonb_build_object(
        'source_kind', source_kind, 'unit', 'percent',
        'semantics', soil_moisture_semantics,
        'depth_cm', jsonb_build_array(soil_moisture_shallow_top_cm, soil_moisture_shallow_bottom_cm)
    ),
    'soil_moisture_deep_pct', jsonb_build_object(
        'source_kind', source_kind, 'unit', 'percent',
        'semantics', soil_moisture_semantics,
        'depth_cm', jsonb_build_array(soil_moisture_deep_top_cm, soil_moisture_deep_bottom_cm)
    ),
    'soil_temp_c', jsonb_build_object('source_kind', source_kind, 'unit', 'degC'),
    'light_lux', jsonb_build_object('source_kind', source_kind, 'unit', 'lux'),
    'solar_radiation_w_m2', jsonb_build_object('source_kind', source_kind, 'unit', 'W/m2'),
    'rainfall_mm', jsonb_build_object(
        'source_kind', source_kind, 'unit', 'mm',
        'interval_seconds', rainfall_interval_seconds, 'semantics', rainfall_semantics
    ),
    'leaf_wetness_pct', jsonb_build_object(
        'source_kind', CASE WHEN leaf_wetness_is_estimated THEN 'derived_estimate' ELSE source_kind END,
        'unit', 'percent', 'is_estimated', leaf_wetness_is_estimated
    )
)
WHERE measurement_metadata = '{}'::jsonb;
