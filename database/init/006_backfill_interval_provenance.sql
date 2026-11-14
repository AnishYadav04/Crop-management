UPDATE sensor_readings
SET rainfall_interval_seconds = COALESCE(
    rainfall_interval_seconds,
    CASE
        WHEN raw_json->'weather_api'->>'current_interval_seconds' ~ '^[0-9]+$'
            THEN (raw_json->'weather_api'->>'current_interval_seconds')::integer
        WHEN source_model = 'ERA5' THEN 3600
        WHEN raw_json->>'sample_interval_seconds' ~ '^[0-9]+$'
            THEN (raw_json->>'sample_interval_seconds')::integer
        ELSE NULL
    END
)
WHERE rainfall_interval_seconds IS NULL;

UPDATE sensor_readings
SET measurement_metadata = measurement_metadata
    - CASE WHEN air_temp_c IS NULL THEN 'air_temp_c' ELSE '' END
    - CASE WHEN air_humidity_pct IS NULL THEN 'air_humidity_pct' ELSE '' END
    - CASE WHEN soil_moisture_shallow_pct IS NULL THEN 'soil_moisture_shallow_pct' ELSE '' END
    - CASE WHEN soil_moisture_deep_pct IS NULL THEN 'soil_moisture_deep_pct' ELSE '' END
    - CASE WHEN soil_temp_c IS NULL THEN 'soil_temp_c' ELSE '' END
    - CASE WHEN light_lux IS NULL THEN 'light_lux' ELSE '' END
    - CASE WHEN solar_radiation_w_m2 IS NULL THEN 'solar_radiation_w_m2' ELSE '' END
    - CASE WHEN rainfall_mm IS NULL THEN 'rainfall_mm' ELSE '' END
    - CASE WHEN leaf_wetness_pct IS NULL THEN 'leaf_wetness_pct' ELSE '' END;

UPDATE sensor_readings
SET measurement_metadata = jsonb_set(
        measurement_metadata, '{rainfall_mm,interval_seconds}',
        to_jsonb(rainfall_interval_seconds), true
    )
WHERE rainfall_interval_seconds IS NOT NULL
  AND measurement_metadata ? 'rainfall_mm';
