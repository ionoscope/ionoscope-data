import json
import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from incremental_update import validate_generation
from scripts import run_live_worker as worker


class LiveWorkerTests(unittest.TestCase):
    def test_background_status_failure_does_not_break_live_status(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            worker.save_status(root/'worker_status.json', {'phase':'ok','successful_cycles':3})
            (root/'discovery').mkdir()
            (root/'discovery/status.json').write_text('{broken')
            with patch.dict('os.environ', IONOSCOPE_DATA_WORKSPACE=directory):
                response=TestClient(app).get('/api/v1/live/status')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['phase'],'ok')
            self.assertEqual(response.json()['discovery']['phase'],'unavailable')

    def test_catalog_hints_never_override_confirmed_inactivity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'state.json').write_text(json.dumps({'available_stations': ['OLD00','RETRY','FALL0']}))
            path = root/'catalog.json'
            now = datetime.now(UTC).isoformat()
            path.write_text(json.dumps({'checked_at': now, 'stations': [
                {'station':'OLD00','active':False,'reason':'no_recent_verified_fof2'},
                {'station':'RETRY','active':False,'reason':'source_unavailable'},
                {'station':'NEW00','active':True,'reason':'fresh_fof2'}]}))
            self.assertEqual(worker.select_candidates(path, root), ['FALL0','NEW00','RETRY'])

    def test_missing_catalog_can_recheck_previous_stations_without_full_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'state.json').write_text(json.dumps({'available_stations':['TR169']}))
            self.assertEqual(worker.select_candidates(root/'missing.json', root), ['TR169'])

    def test_stale_catalog_cannot_add_unverified_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'catalog.json'
            path.write_text(json.dumps({'checked_at':(datetime.now(UTC)-timedelta(hours=3)).isoformat(),
                'stations':[{'station':'OLD00','active':True}]}))
            self.assertEqual(worker.select_candidates(path, root), [])

    def test_station_expiring_during_preparation_is_not_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'state.json').write_text(json.dumps({'generation': 'generations/new', 'available_stations': ['GOOD0', 'OLD00']}))
            args = argparse.Namespace(stations='auto', workspace=directory, sources='giro', initial_hours=24,
                overlap_hours=3, job_timeout=10, api_url='http://api', http_timeout=5)
            with patch.object(worker, 'run_job'), \
                    patch('station_activity.activity_map', return_value={'GOOD0': {'active': True}, 'OLD00': {'active': False}}), \
                    patch('api.services.forecast_ready', return_value=True), \
                    patch.object(worker, 'refresh_forecasts', return_value=[]) as refresh:
                worker.run_cycle(args, ['GOOD0', 'OLD00'])
            refresh.assert_called_once_with('http://api', ['GOOD0'], 5)

    def test_one_failed_station_does_not_block_other_refreshes(self):
        good = MagicMock()
        good.__enter__.return_value.read.return_value = b'{"station":"TR169","current_fof2_available":true}'
        with patch.object(worker, 'urlopen', side_effect=[OSError('offline'), good]) as request:
            with self.assertRaises(worker.ForecastBatchError) as error:
                worker.refresh_forecasts('http://api', ['BAD00', 'TR169'], 1)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(error.exception.forecasts[0]['station'], 'TR169')

    def test_timeout_kills_child_process_group(self):
        process = MagicMock(pid=1234)
        process.wait.side_effect = [subprocess.TimeoutExpired('collector', 1), 0]
        with patch.object(worker.subprocess, 'Popen') as popen, \
                patch.object(worker.os, 'killpg', create=True) as kill, \
                patch.object(worker.os, 'name', 'posix'):
            popen.return_value.__enter__.return_value = process
            with self.assertRaises(subprocess.TimeoutExpired):
                worker.run_job(['collector'], 1)
        kill.assert_called_once_with(1234, worker.signal.SIGKILL)

    def test_failed_preparation_never_requests_forecast(self):
        args = argparse.Namespace(workspace='/tmp/live', sources='giro', initial_hours=24,
                                  overlap_hours=3, job_timeout=10, api_url='http://api', http_timeout=5)
        with patch.object(worker, 'run_job', side_effect=RuntimeError('failed')), \
                patch.object(worker, 'refresh_forecasts') as refresh:
            with self.assertRaises(RuntimeError):
                worker.run_cycle(args, ['TR169'])
        refresh.assert_not_called()

    def test_status_api_reads_persistent_status(self):
        from fastapi.testclient import TestClient
        from api.main import app
        with tempfile.TemporaryDirectory() as directory:
            worker.save_status(Path(directory) / 'worker_status.json', {'phase': 'ok', 'successful_cycles': 2})
            with patch.dict(worker.os.environ, IONOSCOPE_DATA_WORKSPACE=directory):
                response = TestClient(app).get('/api/v1/live/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['successful_cycles'], 2)

    def test_unusable_generation_is_rejected(self):
        end = datetime.now(UTC)
        for row in ({'foF2': '', 'foF2_source_timestamp': ''},
                    {'foF2': '4', 'foF2_source_timestamp': (end-timedelta(hours=3)).isoformat()}):
            with patch('api.services._read_latest_row', return_value=row):
                with self.assertRaises(ValueError):
                    validate_generation(Path('/tmp/generation'), ['TR169'], end)

    def test_recent_generation_is_accepted(self):
        end = datetime.now(UTC)
        with patch('api.services._read_latest_row', return_value={
                'foF2': '4', 'foF2_source_timestamp': (end-timedelta(minutes=15)).isoformat()}):
            validate_generation(Path('/tmp/generation'), ['TR169'], end)

    def test_failed_cycle_preserves_last_success_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker_status.json'
            worker.save_status(path, {'last_success': 'previous', 'successful_cycles': 2})
            with patch('sys.argv', ['worker', '--once', '--workspace', directory]), \
                    patch.dict(worker.os.environ), \
                    patch.object(worker, 'run_job'), \
                    patch('station_activity.discover', return_value={'active_stations': ['TR169'], 'stations': []}), \
                    patch('station_activity.write_metadata'), \
                    patch.object(worker, 'run_cycle', side_effect=RuntimeError('offline')):
                with self.assertRaises(SystemExit) as error:
                    worker.main()
            status = json.loads(path.read_text())
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(status['last_success'], 'previous')
            self.assertEqual(status['successful_cycles'], 2)
            self.assertEqual(status['phase'], 'failed')

    def test_restart_runs_immediately_and_keeps_success_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker_status.json'
            worker.save_status(path, {'successful_cycles': 2})
            with patch('sys.argv', ['worker', '--once', '--workspace', directory]), \
                    patch.dict(worker.os.environ), \
                    patch.object(worker, 'run_job'), \
                    patch('station_activity.discover', return_value={'active_stations': ['TR169'], 'stations': []}), \
                    patch('station_activity.write_metadata'), \
                    patch.object(worker, 'run_cycle', return_value=[]) as cycle:
                worker.main()
            cycle.assert_called_once()
            self.assertEqual(json.loads(path.read_text())['successful_cycles'], 3)
