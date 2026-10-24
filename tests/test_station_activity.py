from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from station_activity import assess_rows, activity_map, discover


class StationActivityTests(unittest.TestCase):
    def test_removed_station_is_excluded_before_background_scan_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'station_registry.json').write_text(json.dumps({'checked_at':datetime.now(UTC).isoformat(),
                'stations':[{'station':'OLD00','active':True,'reason':'fresh_fof2'}]}))
            def probe(code, now, age):
                published=json.loads((root/'station_registry.json').read_text())
                old=next(row for row in published['stations'] if row['station']=='OLD00')
                self.assertFalse(old['active'])
                self.assertEqual(old['reason'],'not_in_current_giro_catalog')
                return {'station':code,'active':False,'reason':'no_recent_verified_fof2','last_observation':None}
            with patch('station_activity.resolve_giro_stations',return_value=['TR169']), \
                    patch('station_activity.check_station',side_effect=probe):
                discover(root,['OLD00','TR169'],publish_progress=True)

    def test_background_publishes_candidates_before_full_scan_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def probe(code, now, age):
                if code == 'B0000':
                    partial = json.loads((root/'station_registry.json').read_text())
                    self.assertEqual(partial['active_stations'], ['A0000'])
                return {'station':code,'active':True,'reason':'fresh_fof2','diagnosis':'fresh_fof2',
                        'last_observation':(now-timedelta(minutes=15)).isoformat()}
            with patch.dict('os.environ', IONOSCOPE_GIRO_BACKGROUND='1'), \
                    patch('station_activity.resolve_giro_stations', return_value=['A0000','B0000']), \
                    patch('station_activity.check_station', side_effect=probe):
                result = discover(root, ['A0000','B0000'], publish_progress=True)
            self.assertEqual(result['active_stations'], ['A0000','B0000'])

    def test_selected_update_deactivates_unselected_previous_station(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'station_registry.json').write_text(json.dumps({'checked_at':datetime.now(UTC).isoformat(),
                'stations':[{'station':'OLD00','active':True,'reason':'fresh_fof2',
                             'last_observation':datetime.now(UTC).isoformat()}]}))
            with patch('station_activity.check_station', return_value={'station':'TR169','active':False,
                    'reason':'no_recent_verified_fof2','last_observation':None}):
                result=discover(root,['TR169'],selected_only=True)
            old=next(row for row in result['stations'] if row['station']=='OLD00')
            self.assertFalse(old['active'])
            self.assertEqual(old['reason'],'not_selected')

    def test_selected_station_update_never_scans_catalog(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('station_activity.resolve_giro_stations') as catalog, \
                patch('station_activity.check_station', return_value={'station': 'TR169', 'active': False,
                    'reason': 'no_recent_verified_fof2', 'diagnosis': 'low_cs', 'last_observation': None}) as probe:
            result = discover(Path(directory), ['TR169'], selected_only=True)
        catalog.assert_not_called()
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(result['active_stations'], [])

    def test_each_probe_uses_its_actual_start_time_after_waiting_in_queue(self):
        batch_start = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
        probe_start = batch_start + timedelta(minutes=5)
        finished = probe_start + timedelta(minutes=1)
        with tempfile.TemporaryDirectory() as directory, \
                patch('station_activity.datetime') as clock, \
                patch('station_activity.resolve_giro_stations', return_value=['TR169']), \
                patch('station_activity.check_station', return_value={
                    'station': 'TR169', 'active': False, 'reason': 'no_recent_verified_fof2',
                    'diagnosis': 'no_measurements_in_window', 'last_observation': None}) as probe:
            clock.now.side_effect = [batch_start, probe_start, finished]
            registry = discover(Path(directory), ['TR169'])
        probe.assert_called_once_with('TR169', probe_start, 60)
        self.assertEqual(registry['stations'][0]['checked_at'], probe_start.isoformat())
        self.assertEqual(registry['checked_at'], finished.isoformat())

    def test_diagnosis_separates_time_quality_and_absence(self):
        cases = [([], 'no_measurements_in_window'), ([self.row(120)], 'stale_fof2'),
                 ([self.row(15, cs='20')], 'low_cs'), ([self.row(15, cs='-1')], 'missing_cs'),
                 ([self.row(15, value='---')], 'fof2_missing'),
                 ([self.row(15, value='100')], 'invalid_fof2'),
                 ([self.row(1)], 'incomplete_interval'), ([self.row(15)], 'fresh_fof2')]
        for rows, diagnosis in cases:
            with self.subTest(diagnosis=diagnosis):
                self.assertEqual(assess_rows(rows, self.now)['diagnosis'], diagnosis)

    def test_future_forecast_requires_recent_enough_completed_interval(self):
        from api.services import forecast_ready
        now = datetime.now(UTC)
        for age, expected in ((45, False), (20, True), (5, False)):
            row = {'time_utc': (now-timedelta(minutes=age)).isoformat(), 'foF2': '5',
                   'foF2_source_timestamp': (now-timedelta(minutes=age-2)).isoformat()}
            with patch('api.services._read_latest_row', return_value=row):
                self.assertEqual(forecast_ready(Path('/tmp/features.csv')), expected)

    def test_recent_verified_probe_is_reused_without_more_source_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.now(UTC)
            registry = {'checked_at': now.isoformat(), 'stations': [{'station': 'TR169', 'active': True,
                'reason': 'fresh_fof2', 'diagnosis': 'fresh_fof2', 'last_observation': (now-timedelta(minutes=15)).isoformat()}]}
            (root/'station_registry.json').write_text(json.dumps(registry))
            with patch('station_activity.resolve_giro_stations', return_value=['TR169']), \
                    patch('station_activity.check_station') as probe:
                result = discover(root, ['TR169'])
            probe.assert_not_called()
            self.assertEqual(result['active_stations'], ['TR169'])

    def test_probe_cache_never_extends_old_observation_lifetime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.now(UTC)
            registry = {'checked_at': now.isoformat(), 'stations': [{'station': 'TR169', 'active': True,
                'reason': 'fresh_fof2', 'diagnosis': 'fresh_fof2', 'last_observation': (now-timedelta(minutes=65)).isoformat()}]}
            (root/'station_registry.json').write_text(json.dumps(registry))
            with patch('station_activity.resolve_giro_stations', return_value=['TR169']), \
                    patch('station_activity.check_station', return_value={'station': 'TR169', 'active': False,
                        'reason': 'no_recent_verified_fof2', 'last_observation': None}) as probe:
                result = discover(root, ['TR169'])
            probe.assert_called_once()
            self.assertEqual(result['active_stations'], [])

    def test_discovered_metadata_is_visible_during_preparation(self):
        from api.services import list_stations
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'stations.csv'
            path.write_text('station,location,latitude,longitude_180\nNEW00,New station,40,20\n')
            with patch.dict('os.environ', IONOSCOPE_STATION_METADATA=str(path), IONOSCOPE_DATA_WORKSPACE=''):
                stations = list_stations()
            self.assertEqual([station.code for station in stations], ['NEW00'])

    def test_dynamic_preparation_skips_bad_station_and_publishes_good_one(self):
        from incremental_update import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'state.json').write_text(json.dumps({'stations': ['OLD00'], 'sources': 'giro',
                'batches': ['batches/old'], 'last_end': '2026-10-23T00:00:00Z'}))
            def validate(generation, stations, end):
                if stations == ['BAD00']:
                    raise ValueError('no measurements')
            with patch('sys.argv', ['incremental_update.py', '--dynamic-stations', '--stations', 'BAD00,GOOD0',
                    '--workspace', directory, '--sources', 'giro', '--end', datetime.now(UTC).isoformat()]), \
                    patch('incremental_update.worker_lock') as lock, \
                    patch('incremental_update.run_command'), \
                    patch('incremental_update.validate_generation', side_effect=validate), \
                    patch('api.services.get_latest_features') as latest, \
                    patch('api.services.DEFAULT_FEATURE_DIR'), patch('api.services.DEFAULT_FEATURE_STATION_DIR'):
                lock.return_value.__enter__.return_value = True
                latest.return_value.model_dump.return_value = {}
                main()
            self.assertEqual(json.loads((root/'state.json').read_text())['available_stations'], ['GOOD0'])

    def setUp(self):
        self.now = datetime(2026, 10, 24, 12, 7, tzinfo=UTC)

    def row(self, age, value='5', cs='70'):
        return {'time_utc': (self.now-timedelta(minutes=age)).isoformat(), 'foF2': value, 'CS': cs}

    def test_actual_measurement_qualifies(self):
        self.assertTrue(assess_rows([self.row(15)], self.now)['active'])

    def test_bad_or_stale_data_never_qualifies(self):
        for row in (self.row(120), self.row(15, 'nan'), self.row(15, '---'),
                    self.row(15, cs='20'), self.row(-30), self.row(1), self.row(15, '100')):
            with self.subTest(row=row):
                self.assertFalse(assess_rows([row], self.now)['active'])

    def test_registry_expires_without_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            now = datetime.now(UTC)
            registry = {'checked_at': (now-timedelta(hours=1)).isoformat(), 'max_age_minutes': 60,
                        'stations': [{'station': 'TR169', 'active': True, 'reason': 'fresh_fof2',
                                      'last_observation': (now-timedelta(minutes=15)).isoformat()}]}
            (path/'station_registry.json').write_text(json.dumps(registry))
            self.assertFalse(activity_map(path)['TR169']['active'])

    def test_old_station_removed_and_new_station_can_rejoin(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('station_activity.resolve_giro_stations', return_value=['TR169']), \
                    patch('station_activity.check_station', return_value={
                        'station': 'TR169', 'active': True, 'reason': 'fresh_fof2', 'last_observation': self.now.isoformat()}):
                registry = discover(Path(directory), ['TR169', 'OLD00'])
            self.assertEqual(registry['active_stations'], ['TR169'])
            self.assertEqual(next(row for row in registry['stations'] if row['station']=='OLD00')['reason'],
                             'not_in_current_giro_catalog')

    def test_new_giro_station_requires_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('station_activity.resolve_giro_stations', return_value=['NEW00']), \
                    patch('station_activity.check_station', return_value={
                        'station': 'NEW00', 'active': True, 'reason': 'fresh_fof2', 'last_observation': self.now.isoformat()}):
                registry = discover(Path(directory), ['TR169'], include_uncatalogued=True)
            self.assertEqual(registry['active_stations'], [])

    def test_corrupt_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'station_registry.json').write_text('broken')
            self.assertEqual(activity_map(root), {})

    def test_inactive_stations_have_no_served_feature_path(self):
        from api.services import _feature_file
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'state.json').write_text(json.dumps({'generation': 'generations/old'}))
            (root/'station_registry.json').write_text(json.dumps({
                'checked_at': datetime.now(UTC).isoformat(), 'max_age_minutes': 60,
                'stations': [{'station': 'OLD00', 'active': False, 'reason': 'no_recent_verified_fof2',
                              'last_observation': None}]}))
            with patch.dict('os.environ', IONOSCOPE_DATA_WORKSPACE=directory):
                self.assertEqual(_feature_file('OLD00'), root/'inactive'/'OLD00_features.csv')
