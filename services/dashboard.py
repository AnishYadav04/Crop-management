"""Local read/write dashboard API for the crop maintenance simulation."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import psycopg
import joblib
import numpy as np
import pandas as pd

DB = os.environ["DATABASE_URL"]
HTML = Path(__file__).resolve().parents[1] / "dashboard" / "index.html"
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "forecasting"
TARGETS = ("air_temp_c", "air_humidity_pct", "soil_moisture_shallow_pct", "soil_moisture_deep_pct", "soil_temp_c", "rainfall_mm", "solar_radiation_w_m2")


def encode(rows):
    return json.loads(json.dumps(rows, default=lambda x: x.isoformat() if isinstance(x, datetime) else x))


def build_live_features(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    columns = {}
    for target in TARGETS:
        series = frame[target]
        for lag in (0, 1, 2, 3, 6, 12, 24, 48, 168):
            columns[f"{target}_lag_{lag}"] = series.shift(lag)
        for window in (6, 24, 168):
            rolled = series.rolling(window=window, min_periods=window)
            columns[f"{target}_mean_{window}"] = rolled.mean()
            if window in (24, 168):
                columns[f"{target}_std_{window}"] = rolled.std()
    valid = frame.index + pd.Timedelta(hours=horizon)
    columns["forecast_hour_sin"] = np.sin(2 * np.pi * valid.hour / 24)
    columns["forecast_hour_cos"] = np.cos(2 * np.pi * valid.hour / 24)
    columns["forecast_weekday_sin"] = np.sin(2 * np.pi * valid.day_of_week / 7)
    columns["forecast_weekday_cos"] = np.cos(2 * np.pi * valid.day_of_week / 7)
    columns["forecast_year_sin"] = np.sin(2 * np.pi * valid.day_of_year / 365.25)
    columns["forecast_year_cos"] = np.cos(2 * np.pi * valid.day_of_year / 365.25)
    return pd.DataFrame(columns, index=frame.index)


def compute_forecasts(conn):
    query = "SELECT observed_at,air_temp_c,air_humidity_pct,soil_moisture_shallow_pct,soil_moisture_deep_pct,soil_temp_c,rainfall_mm,solar_radiation_w_m2 FROM sensor_readings WHERE source_model='open-meteo-current' AND observed_at>now()-interval '30 days' ORDER BY observed_at"
    rows = conn.execute(query).fetchall()
    if not rows:
        return {"available": False, "message": "Waiting for live weather readings."}
    names = ["observed_at", *TARGETS]
    raw = pd.DataFrame(rows, columns=names).set_index("observed_at").sort_index()
    raw.index = raw.index.tz_convert("Asia/Kolkata")
    frame = raw.resample("h").mean()
    frame = frame.dropna(how="all")
    frame = frame.loc[:, list(TARGETS)]
    # Require a contiguous seven-day live window. Never bridge gaps with ERA5.
    if len(frame) < 168 or frame.iloc[-168:].isna().any().any() or frame.index[-1] - frame.index[-168] < pd.Timedelta(hours=167):
        count = int(frame.notna().any(axis=1).sum())
        return {"available": False, "recent_weather_rows": count, "message": f"Forecasts need 168 consecutive hourly live weather readings; {count} usable hours are currently available. Historical ERA5 data is not mixed into this live window."}
    live = frame.iloc[-168:]
    metrics_path = ARTIFACTS / "metrics.csv"
    if not metrics_path.exists():
        return {"available": False, "message": "Live history is ready, but offline model artifacts are missing. Run scripts/train_forecasters.py, then restart the dashboard."}
    metrics = pd.read_csv(metrics_path).set_index(["target", "horizon_hours"])
    forecasts = []
    for horizon in (1, 6, 24):
        features = build_live_features(live, horizon).iloc[[-1]]
        valid_time = live.index[-1] + pd.Timedelta(hours=horizon)
        for target in TARGETS:
            filename = ARTIFACTS / "models" / f"{target}_h{horizon}.joblib"
            if not filename.exists() or (target, horizon) not in metrics.index:
                continue
            package = joblib.load(filename)
            row = metrics.loc[(target, horizon)]
            winner = row["selected_by_mae"]
            if winner == "hist_gradient_boosting":
                x = features.reindex(columns=package["feature_columns"])
                value = float(package["model"].predict(x)[0])
            elif winner == "persistence":
                value = float(live[target].iloc[-1])
            elif winner == "seasonal_daily_24h":
                at = valid_time - pd.Timedelta(hours=24)
                value = float(frame.loc[at, target]) if at in frame.index and pd.notna(frame.loc[at, target]) else float(live[target].iloc[-1])
            else:
                at = valid_time - pd.Timedelta(hours=168)
                value = float(frame.loc[at, target]) if at in frame.index and pd.notna(frame.loc[at, target]) else float(live[target].iloc[-1])
            forecasts.append({"target": target, "horizon_hours": horizon, "predicted": value, "valid_at": valid_time.isoformat(), "method": winner, "validation_mae": float(row[f"{winner}_mae"]), "training_source": "ERA5; selected by historical time-series validation"})
    return {"available": bool(forecasts), "live_hours": len(live), "latest_live_reading": live.index[-1].isoformat(), "forecasts": forecasts, "message": "Advisory environmental forecasts only. Model or seasonal baseline is selected using historical cross-validation; it is not a crop diagnosis." if forecasts else "No trained forecasts are currently available."}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data, default=lambda x: x.isoformat() if isinstance(x, datetime) else x).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.send_header("Access-Control-Allow-Origin", "http://localhost:8000"); self.end_headers(); self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/":
            data = HTML.read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        with psycopg.connect(DB) as conn, conn.cursor() as cur:
            if path.path == "/api/status":
                cur.execute("SELECT source_model, count(*), max(observed_at) FROM sensor_readings WHERE observed_at > now()-interval '24 hours' GROUP BY source_model ORDER BY source_model")
                sources = cur.fetchall(); cur.execute("SELECT count(*) FROM maintenance_events WHERE acknowledged_at IS NULL"); open_events = cur.fetchone()[0]
                self.send_json({"sources": sources, "open_events": open_events, "as_of": datetime.now(timezone.utc)}); return
            if path.path == "/api/readings":
                hours = min(168, max(1, int(parse_qs(path.query).get("hours", [24])[0])))
                cur.execute("SELECT observed_at,source_model,air_temp_c,air_humidity_pct,soil_moisture_shallow_pct,soil_moisture_deep_pct,soil_temp_c,light_lux,solar_radiation_w_m2,rainfall_mm,leaf_wetness_pct FROM sensor_readings WHERE observed_at>now()-(%s*interval '1 hour') ORDER BY observed_at DESC LIMIT 3000", (hours,)); self.send_json(encode(cur.fetchall())); return
            if path.path == "/api/events":
                limit = min(100, max(1, int(parse_qs(path.query).get("limit", [50])[0])))
                cur.execute("SELECT id,event_type,location_name,detected_at,severity,summary,details,research_status,search_query,research_note,sources,research_error,acknowledged_at,action_taken,action_notes,action_recorded_at FROM maintenance_events ORDER BY detected_at DESC LIMIT %s", (limit,)); rows = cur.fetchall(); self.send_json(encode(rows)); return
            if path.path == "/api/profile":
                cur.execute("SELECT id,location_name,latitude,longitude,timezone,crop_name,growth_stage,soil_type,rainfall_onset_mm,temperature_drop_c,temperature_window_hours,event_cooldown_hours,updated_at FROM site_profile WHERE id=1"); self.send_json(encode(cur.fetchone())); return
            if path.path == "/api/forecasts":
                self.send_json(compute_forecasts(conn)); return
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path); parts = parsed.path.strip("/").split("/"); data = self.body()
        if parsed.path == "/api/demo-alert" and os.getenv("DEMO_MODE", "true").lower() == "true":
            with psycopg.connect(DB) as conn, conn.cursor() as cur:
                cur.execute("SELECT location_name,crop_name,growth_stage,soil_type FROM site_profile WHERE id=1")
                location,crop,stage,soil=cur.fetchone()
                details={"demo": True, "source_model": "dashboard-demo", "crop_name": crop, "growth_stage": stage, "soil_type": soil, "temperature_before_c": 31.0, "temperature_now_c": 28.5, "temperature_drop_c": 2.5, "temperature_window_hours": 6, "rainfall_interval_mm": 4.2, "note": "Synthetic event for presentation; not an observed field condition."}
                key=f"demo|{uuid.uuid4()}"
                cur.execute("INSERT INTO maintenance_events (event_key,event_type,location_name,detected_at,severity,summary,details,research_status) VALUES (%s,'demo_sugarcane_weather_alert',%s,now(),'low',%s,%s::jsonb,'pending') RETURNING id", (key,location,"DEMO ONLY — simulated sugarcane weather alert created for presentation. This is not a real field warning.",json.dumps(details)))
                event_id=cur.fetchone()[0]; conn.commit()
            self.send_json({"created": True, "id": event_id, "message": "Demo alert created. The research worker will attach guidance shortly."}); return
        if len(parts) == 4 and parts[:2] == ["api", "events"] and parts[3] in ("acknowledge", "action"):
            event_id = int(parts[2])
            with psycopg.connect(DB) as conn, conn.cursor() as cur:
                if parts[3] == "acknowledge":
                    cur.execute("UPDATE maintenance_events SET acknowledged_at=now() WHERE id=%s RETURNING id", (event_id,))
                else:
                    cur.execute("UPDATE maintenance_events SET action_taken=%s,action_notes=%s,action_recorded_at=now() WHERE id=%s RETURNING id", (data.get("action_taken", "Reviewed"), data.get("action_notes", ""), event_id))
                updated = cur.fetchone(); conn.commit()
            self.send_json({"updated": bool(updated)}, 200 if updated else 404); return
        if parsed.path == "/api/profile":
            allowed = ("crop_name", "growth_stage", "soil_type", "rainfall_onset_mm", "temperature_drop_c", "temperature_window_hours", "event_cooldown_hours")
            with psycopg.connect(DB) as conn, conn.cursor() as cur:
                cur.execute("UPDATE site_profile SET crop_name=%s,growth_stage=%s,soil_type=%s,rainfall_onset_mm=%s,temperature_drop_c=%s,temperature_window_hours=%s,event_cooldown_hours=%s,updated_at=now() WHERE id=1 RETURNING id", tuple(data.get(k) for k in allowed)); conn.commit()
            self.send_json({"saved": True}); return
        self.send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        print(f"dashboard: {fmt % args}", flush=True)


def main():
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    print(f"Crop dashboard listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__": main()
