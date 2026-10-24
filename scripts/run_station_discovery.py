"""Discover catalog candidates independently, yielding GIRO slots to live work."""
import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from station_activity import discover, write_metadata
from api.services import list_stations, STATION_METADATA_PATH
from scripts.run_live_worker import save_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default='/app/live_data/discovery')
    parser.add_argument('--interval-seconds', type=float, default=3600)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.interval_seconds <= 0:
        parser.error('Interval must be positive')
    os.environ['IONOSCOPE_GIRO_BACKGROUND'] = '1'
    workspace = Path(args.workspace)
    while True:
        started = time.monotonic()
        status = {'phase': 'running', 'started_at': datetime.now(UTC).isoformat()}
        save_status(workspace/'status.json', status)
        try:
            registry = discover(workspace, [s.code for s in list_stations()],
                                include_uncatalogued=True, publish_progress=True)
            write_metadata(workspace, STATION_METADATA_PATH, registry)
            status.update(phase='ok', active_count=len(registry['active_stations']))
        except Exception as exc:
            status.update(phase='failed', error=str(exc))
        status.update(finished_at=datetime.now(UTC).isoformat(), duration_seconds=round(time.monotonic()-started, 3))
        save_status(workspace/'status.json', status)
        if args.once:
            if status['phase'] != 'ok':
                raise SystemExit(1)
            return
        time.sleep(max(1, args.interval_seconds-(time.monotonic()-started)) if status['phase']=='ok' else 60)


if __name__ == '__main__':
    main()
