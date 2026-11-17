"""Transaction-isolated monitoring integration check; all fixture changes roll back."""
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import psycopg
from services.plant_care import monitoring_cycle, snapshot

with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    with conn.transaction(force_rollback=True):
        conn.execute("UPDATE site_profile SET simulation_device_id='qa-plant-care',sim_moisture_low=30,sim_moisture_high=80 WHERE id=1")
        location=conn.execute('SELECT location_name FROM site_profile WHERE id=1').fetchone()[0]
        now=datetime.now(timezone.utc)
        previous=now.astimezone(ZoneInfo('Asia/Kolkata')).replace(minute=0,second=0,microsecond=0)-timedelta(hours=1)
        stamps=[previous+timedelta(minutes=10,seconds=i*10) for i in range(3)]+[now-timedelta(seconds=n) for n in (40,30,20)]
        for stamp in stamps:
            conn.execute("""INSERT INTO sensor_readings(observed_at,location_name,latitude,longitude,source,source_model,
                is_simulated,air_temp_c,air_humidity_pct,soil_moisture_shallow_pct,soil_moisture_deep_pct,
                rainfall_mm,leaf_wetness_pct,raw_json,device_id,quality_status,rainfall_interval_seconds)
                VALUES(%s,%s,28.24331,76.80864,'qa','wokwi-simulation',true,24,50,20,35,0.1,0,'{}','qa-plant-care','valid',10)""",(stamp,location))
        monitoring_cycle(conn)
        state=snapshot(conn)
        assert state['online'] and len(state['hourly'])==1
        assert state['hourly'][0]['sample_count']==3
        assert abs(state['hourly'][0]['values']['rainfall_mm']-0.3)<0.00001
        count=lambda: conn.execute("SELECT count(*) FROM maintenance_events WHERE details->>'device_id'='qa-plant-care'").fetchone()[0]
        assert count()==1
        monitoring_cycle(conn)
        assert count()==1, 'Duplicate action event'
        conn.execute("DELETE FROM sensor_readings WHERE device_id='qa-plant-care' AND observed_at>now()-interval '10 minutes'")
        assert not snapshot(conn)['online']
        print('PASS: hourly aggregation, rainfall sum, device freshness, dry-condition event and event deduplication; fixtures rolled back.')
