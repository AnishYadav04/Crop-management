from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ml.features import FEATURE_TARGETS, REQUIRED_HISTORY_ROWS, exact_hourly_window, make_features
from services.data_quality import build_measurement_metadata, normalize_sensor_values, parse_observation_time
from services.dashboard import aggregate_weather_observations, hourly_source_is_fresh
from services.event_rules import build_event_key
from services.research_agent import _is_trusted, summarize_event


def sample_frame(rows=200):
    index = pd.date_range("2025-01-01", periods=rows, freq="h", tz="Asia/Kolkata")
    return pd.DataFrame({name: np.arange(rows, dtype=float) + n for n, name in enumerate(FEATURE_TARGETS)}, index=index)


class FeatureContractTests(unittest.TestCase):
    def test_weekly_lag_requires_169_rows_and_matches_first_value(self):
        frame = sample_frame(REQUIRED_HISTORY_ROWS)
        features = make_features(frame, 1)
        self.assertEqual(REQUIRED_HISTORY_ROWS, 169)
        self.assertEqual(features.iloc[-1]["air_temp_c_lag_168"], frame.iloc[0]["air_temp_c"])

    def test_missing_hour_is_rejected_instead_of_bridged(self):
        frame = sample_frame()
        broken = frame.drop(frame.index[45])
        with self.assertRaisesRegex(ValueError, "complete hourly"):
            make_features(broken, 1)
        self.assertIsNone(exact_hourly_window(broken, REQUIRED_HISTORY_ROWS))

    def test_training_and_serving_feature_function_are_same_and_causal(self):
        frame = sample_frame()
        train_features = make_features(frame, 6)
        serving_features = make_features(frame.iloc[-REQUIRED_HISTORY_ROWS:], 6)
        pd.testing.assert_series_equal(train_features.iloc[-1], serving_features.iloc[-1])
        changed_future = frame.copy()
        changed_future.iloc[-1, :] = -999
        before = make_features(frame, 6).iloc[-2]
        after = make_features(changed_future, 6).iloc[-2]
        pd.testing.assert_series_equal(before, after)


class QualityAndAggregationTests(unittest.TestCase):
    def test_measurement_provenance_can_mark_derived_channels(self):
        metadata = build_measurement_metadata(
            {"air_temp_c": 20, "leaf_wetness_pct": 55}, "weather_model",
            {"leaf_wetness_pct":{"source_kind":"derived_estimate","is_estimated":True}},
        )
        self.assertEqual(metadata["air_temp_c"]["source_kind"], "weather_model")
        self.assertEqual(metadata["leaf_wetness_pct"]["source_kind"], "derived_estimate")

    def test_hourly_rain_is_accumulated_and_hour_is_coverage_gated(self):
        times = pd.date_range("2025-01-01T00:00:00+05:30", periods=4, freq="15min")
        raw = pd.DataFrame({name: [10.0, 12.0, 14.0, 16.0] for name in FEATURE_TARGETS}, index=times)
        raw["rainfall_mm"] = [1.0, 2.0, 0.0, 3.0]
        raw["rainfall_interval_seconds"] = [900] * 4
        result = aggregate_weather_observations(raw)
        self.assertAlmostEqual(result.iloc[0]["rainfall_mm"], 6.0)
        self.assertTrue(bool(result.iloc[0]["coverage_ok"]))
        self.assertAlmostEqual(result.iloc[0]["air_temp_c"], 13.0)

        incomplete = aggregate_weather_observations(raw.iloc[:3])
        self.assertFalse(bool(incomplete.iloc[0]["coverage_ok"]))

    def test_sensor_values_reject_nonfinite_or_out_of_range_data(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            normalize_sensor_values({"rainfall_mm": -1})
        with self.assertRaisesRegex(ValueError, "outside"):
            normalize_sensor_values({"air_temp_c": float("nan")})

    def test_timestamp_fallback_is_explicit_and_timezone_aware(self):
        received = datetime(2026, 11, 14, tzinfo=timezone.utc)
        observed, quality = parse_observation_time("uptime+50s", received)
        self.assertEqual(observed, received)
        self.assertEqual(quality, "receive_time_no_device_clock")

    def test_stale_hourly_source_is_rejected(self):
        now = pd.Timestamp("2026-11-14T12:00:00+05:30")
        self.assertTrue(hourly_source_is_fresh(now - pd.Timedelta(hours=2), now))
        self.assertFalse(hourly_source_is_fresh(now - pd.Timedelta(hours=4), now))


class ResearchEvidenceTests(unittest.TestCase):
    def test_event_identity_and_cooldown_scope_include_source(self):
        stamp = pd.Timestamp("2026-11-14T12:00:00+05:30").to_pydatetime()
        simulation = build_event_key("Field A", "wokwi-simulation", "rain", stamp)
        weather = build_event_key("Field A", "open-meteo-current", "rain", stamp)
        self.assertNotEqual(simulation, weather)

    def test_trusted_domain_matching_uses_label_boundaries(self):
        self.assertTrue(_is_trusted("https://hau.ac.in/advisory"))
        self.assertFalse(_is_trusted("https://fakehau.ac.in.attacker.example/advisory"))
        self.assertFalse(_is_trusted("https://icar.org.in.attacker.example/advisory"))

    def test_research_summary_uses_actual_intervals_and_windows(self):
        text = summarize_event(
            "weather_rain_onset_after_temperature_drop",
            {"source_kind": "weather_model", "rainfall_interval_mm": 2.0,
             "rainfall_interval_seconds": 900, "temperature_drop_c": 2.0,
             "temperature_window_hours": 3, "crop_name": "Sugarcane", "growth_stage": "tillering"},
            [],
        )
        self.assertIn("900-second interval", text)
        self.assertIn("over 3 hours", text)
        self.assertNotIn("over six hours", text)


if __name__ == "__main__":
    unittest.main()
