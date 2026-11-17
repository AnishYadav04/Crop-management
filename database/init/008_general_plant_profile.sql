-- General plant-care setup; retain Kapriwas coordinates and historical observations/events.
-- A plant must be selected before plant-specific advice can be justified.
UPDATE site_profile
SET crop_name='unspecified', crop_variety=NULL, growth_stage='unspecified',
    planting_date=NULL, rooting_depth_m=NULL, moisture_warning_threshold_pct=NULL,
    threshold_evidence=NULL, moisture_calibration_version=NULL, updated_at=now()
WHERE id=1 AND lower(crop_name)='sugarcane';
