import unittest
from datetime import datetime, timezone, timedelta
from services.plant_care import evaluate


class PlantCareRules(unittest.TestCase):
    def state(self, moisture=20):
        now=datetime.now(timezone.utc)
        return {'online':True,'low':30,'high':80,'recent':[
            {'observed_at':now-timedelta(seconds=offset),'soil_moisture_shallow_pct':moisture,
             'air_humidity_pct':50,'leaf_wetness_pct':0} for offset in (0,10,20)]}

    def test_persistent_dry_and_wet_conditions(self):
        self.assertEqual(evaluate(self.state())[0][0],'dry_root_zone')
        self.assertEqual(evaluate(self.state(90))[0][0],'wet_root_zone')
        self.assertEqual(evaluate(self.state(50)),[])

    def test_missing_or_stale_device_is_not_a_plant_diagnosis(self):
        state=self.state(); state['online']=False
        self.assertEqual(evaluate(state),[])
        state=self.state(); state['recent'][1]['soil_moisture_shallow_pct']=None
        self.assertEqual(evaluate(state),[])

    def test_single_spike_and_disconnected_samples_do_not_trigger(self):
        state=self.state(); state['recent'][1]['soil_moisture_shallow_pct']=50
        self.assertEqual(evaluate(state),[])
        state=self.state(); state['recent'][-1]['observed_at']-=timedelta(hours=2)
        self.assertEqual(evaluate(state),[])

    def test_humidity_alone_does_not_trigger_wet_canopy(self):
        state=self.state(50)
        for row in state['recent']: row['air_humidity_pct']=90
        self.assertEqual(evaluate(state),[])
        for row in state['recent']: row['leaf_wetness_pct']=70
        self.assertEqual(evaluate(state)[0][0],'wet_canopy')
