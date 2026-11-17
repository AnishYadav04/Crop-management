"""Simulation monitoring: deterministic evidence first, research as a separate step."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import json


def aggregate_hours(conn):
    # Refresh a bounded window for late arrivals; retain previously completed history.
    conn.execute("""INSERT INTO sensor_hourly
        (location_name,source_model,device_id,hour_start,sample_count,estimated_coverage,values_json)
        SELECT location_name,source_model,COALESCE(device_id,'unknown'),
               date_trunc('hour', observed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata',
               count(*),LEAST(1.0,COALESCE(sum(rainfall_interval_seconds),0)/3600.0),
               jsonb_build_object('air_temp_c',avg(air_temp_c),'temp_min_c',min(air_temp_c),
                 'temp_max_c',max(air_temp_c),'air_humidity_pct',avg(air_humidity_pct),
                 'soil_moisture_shallow_pct',avg(soil_moisture_shallow_pct),
                 'soil_moisture_deep_pct',avg(soil_moisture_deep_pct),'soil_temp_c',avg(soil_temp_c),
                 'light_lux',avg(light_lux),'leaf_wetness_pct',avg(leaf_wetness_pct),
                 'rainfall_mm',CASE WHEN count(rainfall_interval_seconds)=count(*) THEN sum(rainfall_mm) END,
                 'rainfall_note','Sum of reported sample intervals; simulated rain is not a gauge measurement')
          FROM sensor_readings WHERE source_model='wokwi-simulation' AND quality_status='valid'
           AND observed_at >= now()-interval '48 hours'
           AND observed_at < (date_trunc('hour',now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata')
          GROUP BY 1,2,3,4
        ON CONFLICT(location_name,source_model,device_id,hour_start) DO UPDATE
        SET sample_count=excluded.sample_count,estimated_coverage=excluded.estimated_coverage,
            values_json=excluded.values_json,updated_at=now()""")


def weather_context(conn, location, now=None):
    now = now or datetime.now(timezone.utc)
    row = conn.execute('SELECT fetched_at,hourly,timezone FROM weather_outlook WHERE location_name=%s', (location,)).fetchone()
    if not row:
        return {'available':False,'message':'Local forecast has not arrived yet.'}
    fetched, hourly, tz = row
    if now-fetched > timedelta(hours=3):
        return {'available':False,'fetched_at':fetched.isoformat(),'message':'Weather forecast is stale; do not infer no rain.'}
    periods=[]
    for i, stamp in enumerate(hourly.get('time', [])):
        valid=datetime.fromisoformat(stamp)
        if valid.tzinfo is None: valid=valid.replace(tzinfo=ZoneInfo(tz))
        if now < valid <= now+timedelta(hours=6):
            def value(key):
                values=hourly.get(key, [])
                return values[i] if i<len(values) else None
            periods.append({'valid_at':valid.isoformat(),'rain_mm':value('precipitation'),
                            'rain_probability_pct':value('precipitation_probability'),
                            'air_temp_c':value('temperature_2m')})
    complete=len(periods)==6 and all(p['rain_mm'] is not None for p in periods)
    chances=[p['rain_probability_pct'] for p in periods if p['rain_probability_pct'] is not None]
    return {'available':bool(periods),'source':'Open-Meteo weather forecast, model estimate',
            'fetched_at':fetched.isoformat(),'next_hours':periods,'complete_six_hours':complete,
            'rain_next_6h_mm':sum(p['rain_mm'] for p in periods) if complete else None,
            'max_rain_probability_pct':max(chances) if chances else None}


def snapshot(conn):
    row=conn.execute('SELECT location_name,crop_name,growth_stage,simulation_device_id,sim_moisture_low,sim_moisture_high FROM site_profile WHERE id=1').fetchone()
    location,plant,stage,device,low,high=row
    rows=conn.execute("""SELECT id,observed_at,air_temp_c,air_humidity_pct,
              soil_moisture_shallow_pct,soil_moisture_deep_pct,leaf_wetness_pct,rainfall_mm
            FROM sensor_readings WHERE location_name=%s AND device_id=%s
              AND source_model='wokwi-simulation' AND quality_status='valid'
              AND observed_at<=now() AND observed_at>now()-interval '24 hours'
            ORDER BY observed_at DESC LIMIT 3""",(location,device)).fetchall()
    fields=('id','observed_at','air_temp_c','air_humidity_pct','soil_moisture_shallow_pct','soil_moisture_deep_pct','leaf_wetness_pct','rainfall_mm')
    readings=[dict(zip(fields,r)) for r in rows]
    now=datetime.now(timezone.utc)
    online=bool(rows) and now-rows[0][1]<=timedelta(minutes=2)
    history=conn.execute("""SELECT hour_start,sample_count,estimated_coverage,values_json FROM sensor_hourly
        WHERE location_name=%s AND device_id=%s AND source_model='wokwi-simulation'
        ORDER BY hour_start DESC LIMIT 24""",(location,device)).fetchall()
    actions=conn.execute("""SELECT a.action_type,a.notes,a.recorded_at FROM field_actions a
        JOIN maintenance_events e ON e.id=a.event_id
        WHERE e.location_name=%s AND e.details->>'device_id'=%s AND e.details->>'crop_name'=%s
        ORDER BY a.recorded_at DESC LIMIT 5""",(location,device,plant)).fetchall()
    return {'location':location,'plant':plant,'growth_stage':stage,'device_id':device,'online':online,
            'recorded_actions':[dict(zip(('action','notes','recorded_at'),r)) for r in actions],
            'profile_complete':plant not in ('unspecified','',None) and stage not in ('unspecified','',None),
            'source':'Wokwi device simulation','threshold_basis':'Configurable simulation controls, not plant-specific agronomic limits',
            'low':low,'high':high,'latest':readings[0] if readings else None,'recent':readings,
            'hourly':[dict(zip(('hour_start','sample_count','estimated_coverage','values'),r)) for r in history],
            'weather':weather_context(conn,location)}


def evaluate(state):
    """Require fresh consecutive readings spanning time; no claims from weather alone."""
    rows=state['recent']
    if not state['online'] or len(rows)<3: return []
    span=(rows[0]['observed_at']-rows[-1]['observed_at']).total_seconds()
    if not 20<=span<=300: return []
    def all_value(key, predicate):
        return all(r.get(key) is not None and predicate(r[key]) for r in rows)
    suggestions=[]
    if all_value('soil_moisture_shallow_pct',lambda x:x<state['low']):
        suggestions.append(('dry_root_zone','Check root-zone moisture before watering.',
                            'Three consecutive simulated shallow moisture readings are below the configured demo threshold.'))
    if all_value('soil_moisture_shallow_pct',lambda x:x>state['high']):
        suggestions.append(('wet_root_zone','Inspect drainage before adding more water.',
                            'Three consecutive simulated shallow moisture readings are above the configured demo threshold.'))
    if all_value('air_humidity_pct',lambda x:x>=85) and all_value('leaf_wetness_pct',lambda x:x>=60):
        suggestions.append(('wet_canopy','Inspect leaf surfaces and monitor canopy wetness.',
                            'Simulated humidity and leaf wetness remained above demo thresholds; this does not identify a disease.'))
    return suggestions


def monitoring_cycle(conn):
    aggregate_hours(conn)
    state=snapshot(conn)
    for kind, action, reason in evaluate(state):
        existing=conn.execute("""SELECT id FROM maintenance_events WHERE location_name=%s
            AND source_model='wokwi-simulation' AND event_type=%s
            AND details->>'device_id'=%s AND details->>'crop_name'=%s AND
                (event_status<>'resolved' OR detected_at>now()-interval '1 hour') LIMIT 1""",
            (state['location'],'plant_'+kind,state['device_id'],state['plant'])).fetchone()
        if existing: continue
        evidence={'readings':state['recent'],'low_demo_threshold':state['low'],'high_demo_threshold':state['high'],
                  'humidity_demo_threshold':85,'leaf_wetness_demo_threshold':60,'basis':state['threshold_basis']}
        details={'crop_name':state['plant'],'growth_stage':state['growth_stage'],'device_id':state['device_id'],
                 'location_name':state['location'],'simulation':True,'source_kind':'device_simulation',
                 'action':action,'reason':reason,'weather':state['weather'],'evidence':evidence}
        key=f"plant-v1|{state['device_id']}|{state['plant']}|{kind}|{state['latest']['id']}"
        conn.execute("""INSERT INTO maintenance_events(event_key,event_type,location_name,detected_at,severity,
            summary,details,research_status,source_model,rule_version,event_status,evidence)
            VALUES(%s,%s,%s,%s,'low',%s,%s::jsonb,'pending','wokwi-simulation','plant-demo-v1','open',%s::jsonb)
            ON CONFLICT(event_key) DO NOTHING""",(key,'plant_'+kind,state['location'],state['latest']['observed_at'],
            action+' '+reason,json.dumps(details,default=str),json.dumps(evidence,default=str)))


if __name__=='__main__':
    import os
    import time
    import psycopg
    while True:
        try:
            with psycopg.connect(os.environ['DATABASE_URL']) as connection:
                monitoring_cycle(connection)
        except Exception as exc:
            print(f'Plant monitoring cycle failed: {type(exc).__name__}',flush=True)
        time.sleep(15)
