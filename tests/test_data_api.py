import csv
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from api.main import app
from api import services


class DataApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / 'stations/TR169_features.csv'
        self.file.parent.mkdir()
        self.metadata = self.root / 'metadata.csv'
        self.metadata.write_text('station,latitude,longitude_180\nTR169,69.6,19.2\n')
        for target, value in [('STATION_METADATA_PATH', self.metadata),
                              ('DEFAULT_FEATURE_DIR', self.root),
                              ('DEFAULT_FEATURE_STATION_DIR', self.root / 'stations')]:
            patcher = patch.object(services, target, value)
            patcher.start(); self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {'IONOSCOPE_DATA_WORKSPACE': '', 'IONOSCOPE_FEATURES_DIR': ''})
        env.start(); self.addCleanup(env.stop)
        self.client = TestClient(app)

    def write(self, rows):
        with self.file.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['time_utc', 'foF2', 'foF2_source_timestamp', 'foF2_target_15min', 'other'])
            writer.writeheader(); writer.writerows(rows)

    def test_latest_uses_completed_interval_and_sorted_time(self):
        now = datetime.now(UTC)
        start = now - timedelta(minutes=20)
        self.write([
            {'time_utc': start.isoformat(), 'foF2': 4, 'foF2_source_timestamp': (start+timedelta(minutes=5)).isoformat(), 'foF2_target_15min': 99, 'other': 'inf'},
            {'time_utc': (now-timedelta(minutes=2)).isoformat(), 'foF2': 9},
            {'time_utc': (start-timedelta(minutes=15)).isoformat(), 'foF2': 3},
        ])
        response = self.client.get('/api/v1/features/tr169/latest')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['features'], {'foF2': 4})
        self.assertEqual(payload['timestamp'], start.isoformat().replace('+00:00', 'Z'))
        self.assertEqual(payload['source_interval_end'], (start+timedelta(minutes=15)).isoformat().replace('+00:00', 'Z'))

    def test_unknown_station_and_missing_table_are_distinct(self):
        unknown = self.client.get('/api/v1/features/UNKNOWN/latest')
        missing = self.client.get('/api/v1/features/TR169/latest')
        self.assertEqual(unknown.json()['detail']['code'], 'station_not_found')
        self.assertEqual(missing.json()['detail']['code'], 'features_unavailable')

    def test_stations_refresh_data_availability(self):
        self.assertFalse(self.client.get('/api/v1/stations').json()['stations'][0]['data_available'])
        self.write([{'time_utc': '2025-09-01T00:00:00Z', 'foF2': 4}])
        self.assertTrue(self.client.get('/api/v1/stations').json()['stations'][0]['data_available'])

    def test_range_accepts_mixed_timezone_and_has_observation_provenance(self):
        self.write([{'time_utc': '2025-09-01T00:00:00Z', 'foF2': 4,
                     'foF2_source_timestamp': '2025-09-01T00:10:00Z'}])
        params = {'start': '2025-09-01T00:00:00', 'end': '2025-09-01T03:00:00+03:00'}
        response = self.client.get('/api/v1/features/TR169', params=params)
        self.assertEqual(response.status_code, 200)
        row = response.json()['records'][0]
        self.assertTrue(row['current_fof2_available'])
        self.assertEqual(row['observation_timestamp'], '2025-09-01T00:10:00Z')
        self.assertEqual(self.client.get('/api/v1/availability/TR169', params=params).status_code, 200)
        params['end'] = '2025-08-31T00:00:00Z'
        self.assertEqual(self.client.get('/api/v1/features/TR169', params=params).status_code, 400)

    def test_empty_table_or_no_completed_rows_reports_missing_features(self):
        self.write([{'time_utc': (datetime.now(UTC)+timedelta(minutes=1)).isoformat(), 'foF2': 4}])
        self.assertEqual(self.client.get('/api/v1/features/TR169/latest').status_code, 404)
        self.write([])
        self.assertEqual(self.client.get('/api/v1/features/TR169/latest').status_code, 404)

    def test_missing_workspace_state_does_not_crash_station_listing(self):
        with patch.dict(os.environ, IONOSCOPE_DATA_WORKSPACE=str(self.root)):
            self.assertEqual(self.client.get('/api/v1/stations').status_code, 200)
            self.assertEqual(self.client.get('/api/v1/features/TR169/latest').status_code, 404)
