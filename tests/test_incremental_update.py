import csv
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from collect_hf_data import write_csv
from incremental_update import collection_start, merge_batches, main


class IncrementalTests(unittest.TestCase):
    def test_prepare_only_remembers_disabled_cs_filter_and_forwards_it(self):
        from api import services
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'stations': ['TR169'], 'sources': 'giro', 'batches': ['batches/old'],
                     'last_end': '2026-10-23T00:00:00Z', 'generation': 'generations/old',
                     'cs_filter': 'off', 'min_cs': 50}
            path = root / 'state.json'
            path.write_text(json.dumps(state))
            with patch('sys.argv', ['incremental_update.py', '--stations', 'TR169',
                    '--workspace', directory, '--sources', 'giro', '--prepare-only']), \
                    patch('incremental_update.worker_lock') as lock, \
                    patch('incremental_update.merge_batches'), \
                    patch('incremental_update.run_command') as run, \
                    patch.object(services, 'get_latest_features') as latest, \
                    patch.object(services, 'DEFAULT_FEATURE_DIR'), \
                    patch.object(services, 'DEFAULT_FEATURE_STATION_DIR'):
                lock.return_value.__enter__.return_value = True
                latest.return_value.model_dump.return_value = {}
                main()
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('--cs-filter') + 1], 'off')
            self.assertEqual(json.loads(path.read_text())['cs_filter'], 'off')

    def test_failed_preparation_does_not_publish_new_watermark_or_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'stations': ['TR169'], 'sources': 'giro', 'batches': ['batches/old'],
                     'last_end': '2026-10-23T00:00:00Z', 'generation': 'generations/old'}
            path = root / 'state.json'
            original = json.dumps(state)
            path.write_text(original)
            with patch('sys.argv', ['incremental_update.py', '--stations', 'TR169',
                    '--workspace', directory, '--sources', 'giro', '--end', '2026-10-23T01:00:00Z']), \
                    patch('incremental_update.worker_lock') as lock, \
                    patch('incremental_update.prepare_collection_config', return_value=''), \
                    patch('incremental_update.merge_batches'), \
                    patch('incremental_update.run_command', side_effect=[None, RuntimeError('preparation failed')]):
                lock.return_value.__enter__.return_value = True
                with self.assertRaisesRegex(RuntimeError, 'preparation failed'):
                    main()
            self.assertEqual(path.read_text(), original)

    def test_api_follows_committed_generation_pointer(self):
        from api.services import _feature_file
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state.json'
            with patch.dict(os.environ, IONOSCOPE_DATA_WORKSPACE=directory):
                state.write_text(json.dumps({'generation': 'generations/old'}))
                self.assertEqual(_feature_file('TR169'), root / 'generations/old/features/stations/TR169_features.csv')
                state.write_text(json.dumps({'generation': 'generations/new'}))
                self.assertEqual(_feature_file('TR169'), root / 'generations/new/features/stations/TR169_features.csv')

    def test_download_window_uses_checkpoint_and_overlap(self):
        end = datetime(2026, 10, 23, tzinfo=UTC)
        previous = end - timedelta(minutes=15)
        self.assertEqual(collection_start(end, previous, 48), previous - timedelta(hours=48))
        self.assertEqual(collection_start(end, None, 48), end - timedelta(days=8))

    def test_merging_revisions_preserves_history_and_product_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, empty = [root / name for name in ('old', 'new', 'empty')]
            for path in (old, new, empty):
                (path / 'processed').mkdir(parents=True)
            def row(time, energy, flux):
                return dict(source='NOAA', product='xrays', time_utc=time, energy=energy, flux=flux)
            write_csv(old / 'processed/noaa.csv', [row('2026-10-22T00:00:00Z', 'a', 1),
                row('2026-10-23T00:00:00Z', 'a', 2), row('2026-10-23T00:00:00Z', 'b', 3)])
            write_csv(new / 'processed/noaa.csv', [row('2026-10-23T03:00:00+03:00', 'a', 4)])
            write_csv(empty / 'processed/noaa.csv', [])
            merge_batches([old, new, empty], root / 'result')
            with (root / 'result/processed/noaa.csv').open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual([r['flux'] for r in rows], ['1', '4', '3'])

    def test_raw_overlap_deduplicated_and_failed_response_preserves_old_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batches = [root / name for name in ('old', 'new', 'failed')]
            lines = ['2026-10-23T00:00:00Z 75 4.0 //\n2026-10-23T00:15:00Z 75 4.1 //',
                     '2026-10-23T00:15:00Z 75 4.2 //\n2026-10-23T00:30:00Z 75 --- __',
                     'ERROR: upstream unavailable']
            for batch, content in zip(batches, lines):
                path = batch / 'raw/giro/station=TR169/year=2026/test.txt'
                path.parent.mkdir(parents=True)
                path.write_text(content)
            merge_batches(batches, root / 'result')
            rows = (root / 'result/raw/giro/station=TR169/history.txt').read_text().splitlines()
            self.assertEqual(len(rows), 3)
            self.assertIn('4.2', rows[1])
            self.assertIn('---', rows[2])


if __name__ == '__main__':
    unittest.main()
