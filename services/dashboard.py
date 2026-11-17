"""Dashboard API and static UI for the crop maintenance prototype."""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import joblib
import numpy as np
import pandas as pd
import psycopg

from ml.features import FEATURE_TARGETS, REQUIRED_HISTORY_ROWS, exact_hourly_window, make_features
from services.research_agent import answer_user_query
from services.plant_care import snapshot as care_snapshot, evaluate as evaluate_care

DB = os.getenv("DATABASE_URL")
HTML = Path(__file__).resolve().parents[1] / "dashboard" / "index.html"
ASSISTANT_HTML = Path(__file__).resolve().parents[1] / "dashboard" / "assistant.html"
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "forecasting"
TARGETS = FEATURE_TARGETS
TZ = "Asia/Kolkata"
MAX_COMPLETED_HOUR_AGE = pd.Timedelta(hours=3)
UNITS = {
    "air_temp_c": "°C", "air_humidity_pct": "%", "soil_moisture_shallow_pct": "% VWC/model",
    "soil_moisture_deep_pct": "% VWC/model", "soil_temp_c": "°C", "rainfall_mm": "mm/hour",
    "solar_radiation_w_m2": "W/m²",
}
_MODEL_CACHE: dict[str, tuple[float, dict]] = {}
_METRICS_CACHE: tuple[float, pd.DataFrame] | None = None


def _load_model(path: Path) -> dict:
    modified = path.stat().st_mtime
    cached = _MODEL_CACHE.get(str(path))
    if cached and cached[0] == modified:
        return cached[1]
    package = joblib.load(path)
    _MODEL_CACHE[str(path)] = (modified, package)
    return package


def _load_metrics(path: Path) -> pd.DataFrame:
    global _METRICS_CACHE
    modified = path.stat().st_mtime
    if _METRICS_CACHE and _METRICS_CACHE[0] == modified:
        return _METRICS_CACHE[1]
    metrics = pd.read_csv(path).set_index(["target", "horizon_hours"])
    _METRICS_CACHE = (modified, metrics)
    return metrics


def json_safe(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def aggregate_weather_observations(raw: pd.DataFrame) -> pd.DataFrame:
    """Aggregate interval samples: state variables by mean, rainfall by sum."""
    if raw.empty:
        return pd.DataFrame(columns=(*TARGETS, "sample_count", "coverage_ok"))
    raw = raw.sort_index().copy()
    if raw.index.tz is None:
        raise ValueError("weather observations must have timezone-aware timestamps")
    raw.index = raw.index.tz_convert(TZ)
    hourly = raw[list(TARGETS)].resample("h", label="left", closed="left").mean()
    hourly["rainfall_mm"] = raw["rainfall_mm"].resample("h", label="left", closed="left").sum(min_count=1)
    hourly["sample_count"] = raw["air_temp_c"].resample("h", label="left", closed="left").count()
    intervals = raw["rainfall_interval_seconds"].dropna()
    expected_per_hour = max(1, int(3600 / intervals.median())) if not intervals.empty and intervals.median() > 0 else 1
    hourly["coverage_ok"] = hourly["sample_count"] >= expected_per_hour
    cutoff = pd.Timestamp.now(tz=TZ).floor("h")
    return hourly.loc[hourly.index < cutoff]


def hourly_source_is_fresh(latest_hour: pd.Timestamp, now: pd.Timestamp | None = None) -> bool:
    now = now or pd.Timestamp.now(tz=TZ)
    latest_hour = latest_hour.tz_convert(TZ) if latest_hour.tzinfo else latest_hour.tz_localize(TZ)
    return pd.Timedelta(0) <= now - latest_hour <= MAX_COMPLETED_HOUR_AGE


def _hourly_weather(conn):
    rows = conn.execute(
        """SELECT observed_at,air_temp_c,air_humidity_pct,soil_moisture_shallow_pct,
                  soil_moisture_deep_pct,soil_temp_c,rainfall_mm,solar_radiation_w_m2,
                  rainfall_interval_seconds
             FROM sensor_readings
            WHERE source_model='open-meteo-current' AND observed_at>now()-interval '60 days'
            ORDER BY observed_at"""
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=(*TARGETS, "sample_count", "coverage_ok"))
    names = ["observed_at", *TARGETS, "rainfall_interval_seconds"]
    raw = pd.DataFrame(rows, columns=names).set_index("observed_at").sort_index()
    return aggregate_weather_observations(raw)


def reconcile_forecasts(conn, hourly: pd.DataFrame) -> None:
    if hourly.empty:
        return
    pending = conn.execute(
        """SELECT id,target,valid_time FROM forecast_records
             WHERE source_model='open-meteo-current' AND actual_value IS NULL
               AND valid_time < now()"""
    ).fetchall()
    with conn.cursor() as cur:
        for record_id, target, valid_time in pending:
            valid = pd.Timestamp(valid_time).tz_convert(TZ).floor("h")
            if valid in hourly.index and bool(hourly.loc[valid, "coverage_ok"]):
                actual = hourly.loc[valid, target]
                if pd.notna(actual):
                    cur.execute(
                        "UPDATE forecast_records SET actual_value=%s,evaluated_at=now() WHERE id=%s AND actual_value IS NULL",
                        (float(actual), record_id),
                    )


def compute_forecasts(conn):
    hourly = _hourly_weather(conn)
    if hourly.empty:
        return {"available": False, "message": "Waiting for current weather model observations."}
    recent = hourly.tail(REQUIRED_HISTORY_ROWS)
    if len(recent) != REQUIRED_HISTORY_ROWS or not recent["coverage_ok"].all():
        count = int(hourly["coverage_ok"].sum())
        return {
            "available": False, "recent_complete_hours": count,
            "required_hours": REQUIRED_HISTORY_ROWS,
            "message": f"Forecasts need {REQUIRED_HISTORY_ROWS} consecutive completed hourly observations with source coverage. {count} covered hours are currently available.",
        }
    window = exact_hourly_window(recent[list(TARGETS)], REQUIRED_HISTORY_ROWS)
    if window is None:
        return {"available": False, "required_hours": REQUIRED_HISTORY_ROWS,
                "message": "The current weather feed has a missing hour or sensor value; forecasting is paused until the window is complete."}
    if not hourly_source_is_fresh(window.index[-1]):
        return {"available": False, "latest_complete_hour": window.index[-1].isoformat(),
                "message": "Forecasts are paused because the latest completed weather hour is stale. Waiting for a fresh source reading."}
    metrics_path = ARTIFACTS / "metrics.csv"
    if not metrics_path.exists():
        return {"available": False, "message": "Live history is ready, but trained forecast artifacts are missing. Run scripts/train_forecasters.py."}
    metrics = _load_metrics(metrics_path)
    origin = window.index[-1]
    forecasts = []
    with conn.cursor() as cur:
        for horizon in (1, 6, 24):
            feature_row = make_features(window, horizon).iloc[[-1]]
            valid_time = origin + pd.Timedelta(hours=horizon)
            for target in TARGETS:
                filename = ARTIFACTS / "models" / f"{target}_h{horizon}.joblib"
                if not filename.exists() or (target, horizon) not in metrics.index:
                    continue
                package = _load_model(filename)
                if package.get("feature_schema_version") != "hourly-lag-v1":
                    continue
                metric = metrics.loc[(target, horizon)]
                method = metric["selected_by_mae"]
                model = package["model"]
                model_version = f"{package.get('feature_schema_version', 'unknown')}:{package.get('training_end', 'unknown')}"
                if method == "hist_gradient_boosting":
                    x = feature_row.reindex(columns=package["feature_columns"])
                    predicted = float(model.predict(x)[0])
                elif method == "persistence":
                    predicted = float(window[target].iloc[-1])
                elif method == "seasonal_daily_24h":
                    source_time = valid_time - pd.Timedelta(hours=24)
                    predicted = float(window.loc[source_time, target]) if source_time in window.index else float(window[target].iloc[-1])
                elif method == "seasonal_weekly_168h":
                    # The required lag is present in the 169-row history by design.
                    source_time = valid_time - pd.Timedelta(hours=168)
                    predicted = float(window.loc[source_time, target]) if source_time in window.index else float(window[target].iloc[-1])
                else:
                    continue
                mae_field = f"holdout_{method}_mae"
                holdout_mae = float(metric[mae_field]) if mae_field in metric and pd.notna(metric[mae_field]) else None
                cur.execute(
                    """INSERT INTO forecast_records
                       (location_name,source_model,target,horizon_hours,origin_time,valid_time,
                        predicted_value,unit,method,model_version,training_source,historical_validation_mae)
                       SELECT location_name,'open-meteo-current',%s,%s,%s,%s,%s,%s,%s,%s,
                              'Open-Meteo ERA5 reanalysis',%s FROM site_profile WHERE id=1
                       ON CONFLICT(location_name,source_model,target,horizon_hours,origin_time,model_version)
                       DO NOTHING""",
                    (target, horizon, origin.to_pydatetime(), valid_time.to_pydatetime(), predicted,
                     UNITS[target], method, model_version, holdout_mae),
                )
                forecasts.append({
                    "target": target, "horizon_hours": horizon, "predicted_value": predicted,
                    "unit": UNITS[target], "origin_time": origin.isoformat(), "valid_time": valid_time.isoformat(),
                    "method": method, "historical_holdout_mae": holdout_mae,
                    "training_source": "Open-Meteo ERA5 reanalysis", "model_version": model_version,
                })
        reconcile_forecasts(conn, hourly)
    conn.commit()
    return {
        "available": bool(forecasts), "origin_time": origin.isoformat(),
        "live_hours": REQUIRED_HISTORY_ROWS, "forecasts": forecasts,
        "message": "Environmental forecasts only. Historical holdout MAE describes past reanalysis performance; transfer to this current weather-model feed is unvalidated and this is not a crop diagnosis.",
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(json_safe(data), allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 32_768:
            raise ValueError("request body exceeds 32 KiB")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def handle_chat(self, data: dict) -> None:
        question=str(data.get("message", "")).strip()
        if not question:
            self.send_json({"error":"write a question first"},400); return
        if len(question)>2000:
            self.send_json({"error":"questions are limited to 2,000 characters"},400); return
        raw_conversation=data.get("conversation_id")
        try:
            conversation_id=str(UUID(raw_conversation)) if raw_conversation else None
        except (ValueError,TypeError,AttributeError):
            self.send_json({"error":"conversation_id must be a valid UUID"},400); return

        with psycopg.connect(DB) as conn:
            profile=conn.execute("""SELECT location_name,crop_name,crop_variety,growth_stage,soil_type
                                      FROM site_profile WHERE id=1""").fetchone()
            if not profile:
                self.send_json({"error":"no active crop profile is configured"},503); return
            location,crop,variety,stage,soil=profile
            care=care_snapshot(conn)
            latest_readings=conn.execute(
                """SELECT DISTINCT ON (source_model)
                          source_model,observed_at,is_simulated,air_temp_c,air_humidity_pct,
                          soil_moisture_shallow_pct,soil_moisture_deep_pct,soil_temp_c,
                          rainfall_mm,solar_radiation_w_m2,leaf_wetness_pct,leaf_wetness_is_estimated
                     FROM sensor_readings
                    WHERE location_name=%s
                      AND observed_at >= now()-interval '24 hours'
                      AND (source_model='open-meteo-current' OR
                           (source_model='wokwi-simulation' AND device_id=%s))
                    ORDER BY source_model,observed_at DESC""",
                (location,care['device_id']),
            ).fetchall()
            reading_fields=("source_model","observed_at","is_simulated","air_temp_c","air_humidity_pct",
                            "soil_moisture_shallow_pct","soil_moisture_deep_pct","soil_temp_c",
                            "rainfall_mm","solar_radiation_w_m2","leaf_wetness_pct","leaf_wetness_is_estimated")
            latest_readings=[dict(zip(reading_fields,row)) for row in latest_readings]
            now_utc=datetime.now(timezone.utc)
            for reading in latest_readings:
                observed_at=reading["observed_at"]
                if observed_at.tzinfo is None:
                    observed_at=observed_at.replace(tzinfo=timezone.utc)
                reading["age_minutes"]=max(0,int((now_utc-observed_at).total_seconds()/60))
                reading["fresh_for_advice"]=reading["age_minutes"] <= 60
            if conversation_id:
                exists=conn.execute("SELECT id FROM agent_conversations WHERE id=%s",(conversation_id,)).fetchone()
                if not exists:
                    self.send_json({"error":"conversation not found"},404); return
                history=conn.execute("""SELECT role,content FROM agent_messages
                                        WHERE conversation_id=%s ORDER BY created_at DESC,id DESC LIMIT 10""",
                                     (conversation_id,)).fetchall()
                history=[{"role":role,"content":content} for role,content in reversed(history)]
            else:
                title=question.replace("\n"," ")[:100]
                conversation_id=str(conn.execute("""INSERT INTO agent_conversations(title,location_name,crop_name)
                                                  VALUES(%s,%s,%s) RETURNING id""",
                                             (title,location,crop)).fetchone()[0])
                history=[]
            conn.execute("INSERT INTO agent_messages(conversation_id,role,content) VALUES(%s,'user',%s)",
                         (conversation_id,question))
            conn.execute("UPDATE agent_conversations SET updated_at=now() WHERE id=%s",(conversation_id,))
            conn.commit()

        context={"location_name":location,"crop_name":crop,"crop_variety":variety,
                 "growth_stage":stage,"soil_type":soil,"latest_readings":latest_readings,
                 "iot_monitoring":care,
                 "conversation_history":history}
        try:
            def refresh_chat_context():
                with psycopg.connect(DB) as latest_conn:
                    return care_snapshot(latest_conn)
            answer,sources,mode=answer_user_query(question,context,refresh_context=refresh_chat_context)
        except Exception as exc:
            answer=("I couldn't complete the web search just now. Please try again in a moment, "
                    "or open a source from the crop profile's research section.")
            sources=[]; mode="search_failed"
            print(f"Crop assistant search failed: {type(exc).__name__}",flush=True)

        with psycopg.connect(DB) as conn:
            conn.execute("""INSERT INTO agent_messages(conversation_id,role,content,sources,answer_mode)
                              VALUES(%s,'assistant',%s,%s::jsonb,%s)""",
                         (conversation_id,answer,json.dumps(sources),mode))
            conn.execute("UPDATE agent_conversations SET updated_at=now() WHERE id=%s",(conversation_id,))
            conn.commit()
        self.send_json({"conversation_id":conversation_id,"answer":answer,"sources":sources,"answer_mode":mode})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            data = HTML.read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if parsed.path == "/assistant":
            data = ASSISTANT_HTML.read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        query = parse_qs(parsed.query)
        try:
            with psycopg.connect(DB) as conn:
                if parsed.path == "/api/status":
                    sources = conn.execute(
                        """SELECT source_model,source_kind,
                                  count(*) FILTER (WHERE observed_at>now()-interval '24 hours') AS rows_24h,
                                  max(observed_at) AS latest,max(received_at) AS last_received
                             FROM sensor_readings WHERE observed_at>now()-interval '30 days'
                             GROUP BY source_model,source_kind ORDER BY source_model"""
                    ).fetchall()
                    open_count = conn.execute("SELECT count(*) FROM maintenance_events WHERE event_status <> 'resolved'").fetchone()[0]
                    self.send_json({"sources": [dict(zip(("source_model","source_kind","rows_24h","latest_observed_at","last_received_at"), row)) for row in sources], "open_events": open_count, "as_of": datetime.now(timezone.utc)}); return
                if parsed.path == "/api/agent/status":
                    search_ready=bool(os.getenv("TAVILY_API_KEY"))
                    synthesis_ready=bool(os.getenv("GOOGLE_API_KEY") or os.getenv("OPENAI_API_KEY"))
                    self.send_json({"search_configured":search_ready,
                                    "synthesis_configured":synthesis_ready,
                                    "mode":"tavily_gemini" if search_ready and os.getenv("GOOGLE_API_KEY") else "tavily_search_excerpts" if search_ready else "search_result_excerpts",
                                    "message":"Tavily search and Gemini synthesis are configured. If Gemini is unreachable, the agent reports local monitoring status and source links." if search_ready and os.getenv("GOOGLE_API_KEY") else "Tavily web search is configured. Add GOOGLE_API_KEY to enable Gemini-synthesized answers." if search_ready else "Tavily is not configured; the agent uses the built-in search fallback."}); return
                if parsed.path == "/api/actions":
                    state=care_snapshot(conn)
                    rows=conn.execute("""SELECT id,event_type,summary,details,detected_at,event_status
                        FROM maintenance_events WHERE location_name=%s AND source_model='wokwi-simulation'
                        AND event_type LIKE 'plant_%%' AND details->>'device_id'=%s AND details->>'crop_name'=%s
                        AND event_status<>'resolved' ORDER BY detected_at DESC LIMIT 10""",
                        (state['location'],state['device_id'],state['plant'])).fetchall()
                    state['actions']=[dict(zip(('id','type','summary','details','detected_at','status'),row)) for row in rows]
                    state['current_conditions']=[{'type':kind,'action':action,'reason':reason}
                                                 for kind,action,reason in evaluate_care(state)]
                    self.send_json(state); return
                if parsed.path == "/api/conversations":
                    rows=conn.execute("""SELECT c.id,c.title,c.crop_name,c.updated_at,
                                               count(m.id) AS message_count
                                          FROM agent_conversations c
                                          LEFT JOIN agent_messages m ON m.conversation_id=c.id
                                         GROUP BY c.id ORDER BY c.updated_at DESC LIMIT 50""").fetchall()
                    fields=("id","title","crop_name","updated_at","message_count")
                    self.send_json([dict(zip(fields,row)) for row in rows]); return
                if parsed.path.startswith("/api/conversations/"):
                    raw_id=parsed.path.rsplit("/",1)[-1]
                    conversation_id=str(UUID(raw_id))
                    exists=conn.execute("SELECT id,title FROM agent_conversations WHERE id=%s",(conversation_id,)).fetchone()
                    if not exists:
                        self.send_json({"error":"conversation not found"},404); return
                    rows=conn.execute("""SELECT id,role,content,sources,answer_mode,created_at
                                          FROM agent_messages WHERE conversation_id=%s
                                         ORDER BY created_at,id""",(conversation_id,)).fetchall()
                    fields=("id","role","content","sources","answer_mode","created_at")
                    self.send_json({"id":exists[0],"title":exists[1],"messages":[dict(zip(fields,row)) for row in rows]}); return
                if parsed.path == "/api/readings":
                    hours = min(720, max(1, int(query.get("hours", [24])[0])))
                    source = query.get("source_model", [None])[0]
                    bucket = min(3600, max(0, int(query.get("bucket_minutes", [0])[0])))
                    params = [hours]
                    source_sql = ""
                    if source:
                        source_sql = " AND source_model=%s"; params.append(source)
                    if bucket:
                        sql = f"""SELECT date_bin(make_interval(mins => %s),observed_at,timestamptz '2000-01-01'),
                                 source_model,source_kind,avg(air_temp_c),avg(air_humidity_pct),
                                 avg(soil_moisture_shallow_pct),avg(soil_moisture_deep_pct),avg(soil_temp_c),
                                 avg(light_lux),sum(rainfall_mm),avg(solar_radiation_w_m2),avg(leaf_wetness_pct),
                                 bool_or(leaf_wetness_is_estimated),(array_agg(measurement_metadata))[1],
                                 count(*) FROM sensor_readings WHERE quality_status<>'invalid'
                                 AND observed_at>now()-(%s*interval '1 hour'){source_sql}
                                 GROUP BY 1,2,3 ORDER BY 1 DESC LIMIT 2000"""
                        args = [bucket, *params]
                    else:
                        sql = f"""SELECT observed_at,source_model,source_kind,air_temp_c,air_humidity_pct,
                                 soil_moisture_shallow_pct,soil_moisture_deep_pct,soil_temp_c,light_lux,
                                 rainfall_mm,solar_radiation_w_m2,leaf_wetness_pct,
                                 leaf_wetness_is_estimated,measurement_metadata,1
                                 FROM sensor_readings WHERE quality_status<>'invalid'
                                 AND observed_at>now()-(%s*interval '1 hour'){source_sql}
                                 ORDER BY observed_at DESC LIMIT 2000"""
                        args = params
                    rows = conn.execute(sql,args).fetchall()
                    fields=("observed_at","source_model","source_kind","air_temp_c","air_humidity_pct","soil_moisture_shallow_pct","soil_moisture_deep_pct","soil_temp_c","light_lux","rainfall_mm","solar_radiation_w_m2","leaf_wetness_pct","leaf_wetness_is_estimated","measurement_metadata","sample_count")
                    self.send_json({"rows":[dict(zip(fields,row)) for row in rows],"source_model":source,"hours":hours,"bucket_minutes":bucket}); return
                if parsed.path == "/api/events":
                    limit = min(100,max(1,int(query.get("limit",[50])[0])))
                    rows=conn.execute("""SELECT id,event_type,location_name,detected_at,severity,summary,details,research_status,
                              search_query,research_note,sources,research_error,event_status,acknowledged_at,
                              resolved_at,resolution_note,source_model,rule_version,evidence
                              FROM maintenance_events ORDER BY detected_at DESC LIMIT %s""",(limit,)).fetchall()
                    fields=("id","event_type","location_name","detected_at","severity","summary","details","research_status","search_query","research_note","sources","research_error","event_status","acknowledged_at","resolved_at","resolution_note","source_model","rule_version","evidence")
                    events=[]
                    for row in rows:
                        event=dict(zip(fields,row))
                        event["actions"]=[dict(zip(("id","action_type","notes","recorded_at","actor"),action)) for action in conn.execute("SELECT id,action_type,notes,recorded_at,actor FROM field_actions WHERE event_id=%s ORDER BY recorded_at DESC",(event["id"],)).fetchall()]
                        events.append(event)
                    self.send_json(events); return
                if parsed.path == "/api/profile":
                    row=conn.execute("SELECT id,location_name,latitude,longitude,timezone,crop_name,growth_stage,soil_type,crop_variety,planting_date,soil_ph,soil_ec_ds_m,field_capacity_vwc,wilting_point_vwc,rooting_depth_m,moisture_warning_threshold_pct,threshold_evidence,moisture_calibration_version,rainfall_onset_mm,temperature_drop_c,temperature_window_hours,event_cooldown_hours,updated_at FROM site_profile WHERE id=1").fetchone()
                    fields=("id","location_name","latitude","longitude","timezone","crop_name","growth_stage","soil_type","crop_variety","planting_date","soil_ph","soil_ec_ds_m","field_capacity_vwc","wilting_point_vwc","rooting_depth_m","moisture_warning_threshold_pct","threshold_evidence","moisture_calibration_version","rainfall_onset_mm","temperature_drop_c","temperature_window_hours","event_cooldown_hours","updated_at")
                    self.send_json(dict(zip(fields,row))); return
                if parsed.path == "/api/forecasts":
                    self.send_json(compute_forecasts(conn)); return
                if parsed.path == "/api/forecast-history":
                    rows=conn.execute("""SELECT target,horizon_hours,origin_time,valid_time,predicted_value,unit,method,
                                    model_version,training_source,historical_validation_mae,actual_value,evaluated_at
                                    FROM forecast_records ORDER BY issued_at DESC LIMIT 500""").fetchall()
                    fields=("target","horizon_hours","origin_time","valid_time","predicted_value","unit","method","model_version","training_source","historical_validation_mae","actual_value","evaluated_at")
                    self.send_json([dict(zip(fields,row)) for row in rows]); return
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error":str(exc)},400); return
        self.send_json({"error":"not found"},404)

    def do_POST(self):
        parsed=urlparse(self.path)
        try:
            data=self.body()
        except (ValueError,json.JSONDecodeError) as exc:
            self.send_json({"error":str(exc)},400); return
        parts=parsed.path.strip("/").split("/")
        if parsed.path=="/api/chat":
            self.handle_chat(data); return
        try:
            with psycopg.connect(DB) as conn:
                if parsed.path=="/api/demo-alert" and os.getenv("DEMO_MODE","true").lower()=="true":
                    profile=conn.execute("SELECT location_name,crop_name,growth_stage,soil_type FROM site_profile WHERE id=1").fetchone()
                    location,crop,stage,soil=profile
                    details={"demo":True,"source_model":"dashboard-demo","source_kind":"demo","crop_name":crop,"growth_stage":stage,"soil_type":soil,"temperature_before_c":31.0,"temperature_now_c":28.5,"temperature_drop_c":2.5,"temperature_window_hours":6,"rainfall_interval_mm":4.2,"note":"Synthetic presentation event, not an observation."}
                    event_id=conn.execute("""INSERT INTO maintenance_events(event_key,event_type,location_name,detected_at,severity,summary,details,research_status,source_model,rule_version,event_status,evidence)
                                      VALUES(%s,'demo_plant_weather_alert',%s,now(),'low',%s,%s::jsonb,'pending','dashboard-demo','demo-v1','open','[]'::jsonb) RETURNING id""",
                                      (f"demo|{uuid.uuid4()}",location,"DEMO ONLY — simulated plant weather alert. This is not a real field warning.",json.dumps(details))).fetchone()[0]
                    self.send_json({"created":True,"id":event_id,"message":"Demo alert created. The research worker will attach guidance shortly."}); return
                if len(parts)==4 and parts[:2]==["api","events"]:
                    event_id=int(parts[2]); action=parts[3]
                    if action=="acknowledge":
                        updated=conn.execute("UPDATE maintenance_events SET acknowledged_at=COALESCE(acknowledged_at,now()),event_status=CASE WHEN event_status='open' THEN 'acknowledged' ELSE event_status END WHERE id=%s RETURNING id",(event_id,)).fetchone()
                    elif action=="resolve":
                        note=str(data.get("resolution_note", ""))[:2000]
                        updated=conn.execute("UPDATE maintenance_events SET event_status='resolved',resolved_at=now(),resolution_note=%s WHERE id=%s RETURNING id",(note,event_id)).fetchone()
                    elif action=="action":
                        action_type=str(data.get("action_type") or "reviewed")[:100]
                        notes=str(data.get("notes") or "")[:2000]
                        event=conn.execute("SELECT id,location_name FROM maintenance_events WHERE id=%s",(event_id,)).fetchone()
                        if event:
                            conn.execute("INSERT INTO field_actions(event_id,location_name,action_type,notes,actor) VALUES(%s,%s,%s,%s,%s)",(event_id,event[1],action_type,notes,str(data.get("actor") or "dashboard")[:100]))
                            conn.execute("UPDATE maintenance_events SET action_taken=%s,action_notes=%s,action_recorded_at=now() WHERE id=%s",(action_type,notes,event_id)); updated=(event_id,)
                        else: updated=None
                    else:
                        self.send_json({"error":"unknown event action"},404); return
                    self.send_json({"updated":bool(updated)},200 if updated else 404); return
                if parsed.path=="/api/profile":
                    allowed=("crop_name","growth_stage","soil_type","crop_variety","planting_date","soil_ph","soil_ec_ds_m","field_capacity_vwc","wilting_point_vwc","rooting_depth_m","moisture_warning_threshold_pct","threshold_evidence","moisture_calibration_version","rainfall_onset_mm","temperature_drop_c","temperature_window_hours","event_cooldown_hours")
                    updates={key:data.get(key) for key in allowed if key in data}
                    for key in ('simulation_device_id','sim_moisture_low','sim_moisture_high'):
                        if key in data: updates[key]=data[key]
                    if not updates:
                        self.send_json({"error":"no supported fields supplied"},400); return
                    if 'planting_date' in updates:
                        updates['planting_date']=date.fromisoformat(updates['planting_date']) if updates['planting_date'] else None
                    for name in ('crop_name','growth_stage','soil_type'):
                        if name in updates:
                            updates[name]=str(updates[name] or '').strip()[:100] or 'unspecified'
                    numeric=("soil_ph","soil_ec_ds_m","field_capacity_vwc","wilting_point_vwc","rooting_depth_m","moisture_warning_threshold_pct","rainfall_onset_mm","temperature_drop_c","temperature_window_hours","event_cooldown_hours")
                    for name in numeric:
                        if name in updates and updates[name] is not None:
                            value=float(updates[name])
                            if not np.isfinite(value): raise ValueError(f"{name} must be finite")
                            updates[name]=value
                    for name in ('sim_moisture_low','sim_moisture_high'):
                        if name in updates:
                            value=float(updates[name])
                            if not np.isfinite(value) or not 0<=value<=100:
                                raise ValueError('Simulation thresholds must be between 0 and 100')
                            updates[name]=value
                    previous=conn.execute('SELECT sim_moisture_low,sim_moisture_high FROM site_profile WHERE id=1').fetchone()
                    if updates.get('sim_moisture_low',previous[0])>=updates.get('sim_moisture_high',previous[1]):
                        raise ValueError('Simulation low moisture threshold must be below the high threshold')
                    if 'simulation_device_id' in updates and not str(updates['simulation_device_id'] or '').strip():
                        raise ValueError('Select a simulation device ID')
                    bounds={"soil_ph":(0,14),"soil_ec_ds_m":(0,1000),"field_capacity_vwc":(0,1),"wilting_point_vwc":(0,1),"rooting_depth_m":(0,10),"moisture_warning_threshold_pct":(0,100),"rainfall_onset_mm":(0,1000),"temperature_drop_c":(0,70),"temperature_window_hours":(1,168),"event_cooldown_hours":(0,720)}
                    for name,(low,high) in bounds.items():
                        if name in updates and updates[name] is not None and not low <= updates[name] <= high:
                            raise ValueError(f"{name} must be between {low} and {high}")
                    current=conn.execute("SELECT threshold_evidence,moisture_calibration_version,moisture_warning_threshold_pct FROM site_profile WHERE id=1").fetchone()
                    evidence=updates.get("threshold_evidence",current[0])
                    calibration=updates.get("moisture_calibration_version",current[1])
                    threshold=updates.get("moisture_warning_threshold_pct",current[2])
                    if threshold is not None and (not evidence or not calibration):
                        raise ValueError("A moisture threshold requires both calibration evidence and a calibration version")
                    assignments=", ".join(f"{name}=%s" for name in updates)
                    conn.execute(f"UPDATE site_profile SET {assignments},updated_at=now() WHERE id=1",tuple(updates.values()))
                    self.send_json({"saved":True}); return
        except (ValueError,TypeError) as exc:
            self.send_json({"error":str(exc)},400); return
        self.send_json({"error":"not found"},404)

    def log_message(self, fmt, *args):
        print(f"dashboard: {fmt % args}",flush=True)


def main():
    if not DB:
        raise SystemExit("Set DATABASE_URL before starting the dashboard")
    port=int(os.getenv("DASHBOARD_PORT","8000"))
    print(f"Crop dashboard listening on :{port}",flush=True)
    ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()


if __name__=="__main__":
    main()
