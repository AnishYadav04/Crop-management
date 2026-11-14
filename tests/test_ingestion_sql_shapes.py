from __future__ import annotations

import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.import_historical_weather import import_rows
from services.live_monitor import persist_reading


class FakeCursor:
    def __init__(self):
        self.sql_calls = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self.sql_calls.append((sql, params))

    def executemany(self, sql, rows):
        self.sql_calls.append((sql, rows))

    def fetchone(self):
        return None


class FakeConnection:
    def __init__(self):
        self.fake_cursor = FakeCursor()
        self.committed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True


def placeholders(sql):
    return len(re.findall(r"%s", sql))


class IngestionInsertShapeTests(unittest.TestCase):
    def test_live_monitor_insert_parameter_count_matches_columns(self):
        conn = FakeConnection()
        payload = {"current": {
            "time": "2026-11-14T12:00", "interval": 900, "temperature_2m": 25.0,
            "relative_humidity_2m": 60.0, "precipitation": 0.2,
            "shortwave_radiation": 50.0, "soil_temperature_6cm": 20.0,
            "soil_moisture_3_to_9cm": 0.25, "soil_moisture_9_to_27cm": 0.3,
        }, "latitude": 28.2, "longitude": 76.8, "timezone": "Asia/Kolkata"}
        profile = {"location_name":"Test field","latitude":28.2,"longitude":76.8,"timezone":"Asia/Kolkata"}
        _observed_at, inserted, _sensors = persist_reading(conn, payload, profile)
        insert_sql, params = conn.fake_cursor.sql_calls[-1]
        self.assertFalse(inserted)
        self.assertEqual(placeholders(insert_sql), len(params))

    def test_historical_import_insert_parameter_count_matches_row(self):
        conn = FakeConnection()
        fields = (
            "temperature_2m", "relative_humidity_2m", "precipitation", "shortwave_radiation",
            "soil_temperature_0_to_7cm", "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm",
        )
        values = (25.0, 60.0, 0.1, 50.0, 20.0, 0.25, 0.3)
        payload = {"hourly":{"time":["2026-11-14T12:00"], **{k:[v] for k,v in zip(fields,values)}}}
        profile = {"location_name":"Test field","latitude":28.2,"longitude":76.8,"timezone":"Asia/Kolkata"}
        import_rows(conn, payload, profile)
        insert_sql, rows = conn.fake_cursor.sql_calls[-1]
        self.assertEqual(placeholders(insert_sql), len(rows[0]))
        self.assertIsNotNone(ZoneInfo("Asia/Kolkata"))


if __name__ == "__main__":
    unittest.main()
