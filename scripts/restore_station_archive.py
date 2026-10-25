"""Download resumable historical GIRO raw files without accumulating parsed rows."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collect_hf_data import fetch_url, GIRO_DATA_URL, giro_characteristic_ids, iter_time_windows, parse_dt


def valid_archive(raw, station, start, end):
    header=raw[:6000].decode('latin-1',errors='replace')
    return ('GIRO' in header and f'URSI-Code {station}' in header
            and start.strftime('%Y-%m-%dT%H:%M') in header
            and end.strftime('%Y-%m-%dT%H:%M') in header
            and b'ERROR:' not in raw and b'<html' not in raw.lower())


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    parser.add_argument('--report-name', default='restoration.json')
    args=parser.parse_args();config=tomllib.loads(Path(args.config).read_text())
    root=Path(config['run']['output_dir']);root.mkdir(parents=True,exist_ok=True)
    start,end=[parse_dt(config['run'][key]) for key in ['start_utc','end_utc']]
    windows=iter_time_windows(start,end,config['giro'].get('chunk_days',365))
    chars=giro_characteristic_ids(config['giro']['characteristics']);report={'files':[]}
    def restore(job):
            code,begin,finish=job
            path=root/f'raw/giro/station={code}/year={begin:%Y}/{code}_{begin:%Y%m%dT%H%M}_{finish:%Y%m%dT%H%M}.txt'
            entry={'station':code,'start':begin.isoformat(),'end':finish.isoformat(),'path':str(path)}
            try:
                raw=path.read_bytes() if path.exists() else b''
                reused=valid_archive(raw,code,begin,finish)
                if not reused:
                    raw=fetch_url(GIRO_DATA_URL,data={'query_submit':'Search',
                        'date_start':begin.strftime('%Y-%m-%d %H:%M'),'date_end':finish.strftime('%Y-%m-%d %H:%M'),
                        'location':code,'DMUF':'3000','chosenchars[]':chars},timeout=120)
                    if not valid_archive(raw,code,begin,finish):raise ValueError('Invalid GIRO archive response')
                    path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=path.with_suffix('.tmp');temporary.write_bytes(raw);temporary.replace(path)
                entry.update(status='complete',reused=reused,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                del raw
            except Exception as exc:
                entry.update(status='failed',error=str(exc))
            entry['finished_at']=datetime.now(timezone.utc).isoformat()
            return entry
    jobs=[(code,begin,finish) for code in config['giro']['stations'] for begin,finish in windows]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(restore,job) for job in jobs]
        for future in as_completed(futures):
            entry=future.result();report['files'].append(entry)
            (root/args.report_name).write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(entry),flush=True)
    if any(row['status']=='failed' for row in report['files']):raise SystemExit(1)


if __name__=='__main__':main()
