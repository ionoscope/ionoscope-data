"""Frozen CS50/period-end input contract used by holdout and live serving."""
from pathlib import Path

import numpy as np
import pandas as pd

from data_preparation.normalize_time_grid import normalize_giro, propagate_interval_features

PROFILE = 'candidate-cs50-period-end-v1'


def driver_rules():
    periods = {'gfz_Kp': '3h', 'gfz_ap': '3h', 'gfz_Ap': '1D',
               'gfz_Fobs': '1D', 'gfz_Fadj': '1D', 'bz_gsm_nT': '1h',
               'dst_nT': '1h', 'flow_speed_km_s': '1h', 'proton_density_n_cc': '1h'}
    return {c: {'valid_for': p, 'availability_delay': p, 'preserve_missing': True}
            for c, p in periods.items()}


def read_drivers(directory, grid, *, require_observations=True):
    rules = driver_rules()
    pieces = []
    directory = Path(directory)
    gfz_path = directory / 'geophysical_indices.csv'
    if gfz_path.exists() and gfz_path.stat().st_size > 2:
        gfz = pd.read_csv(gfz_path)
        gfz['time_utc'] = pd.to_datetime(gfz.time_utc, utc=True)
        for name, rows in gfz.groupby('index'):
            column = 'gfz_' + name
            if column not in rules:
                continue
            series = rows.sort_values('time_utc').drop_duplicates('time_utc').set_index('time_utc')[['value']].rename(columns={'value': column})
            series[column] = pd.to_numeric(series[column], errors='coerce').where(pd.to_numeric(series[column], errors='coerce') >= 0)
            pieces.append(propagate_interval_features(series, '15min', rules).reindex(grid))
    omni_path = directory / 'omni_solar_wind.csv'
    if omni_path.exists() and omni_path.stat().st_size > 2:
        omni = pd.read_csv(omni_path)
        omni['time_utc'] = pd.to_datetime(omni.time_utc, utc=True)
        columns = ['bz_gsm_nT', 'dst_nT', 'flow_speed_km_s', 'proton_density_n_cc']
        hourly = omni.set_index('time_utc')[columns].apply(pd.to_numeric, errors='coerce')
        pieces.append(propagate_interval_features(hourly, '15min', rules).reindex(grid))
    drivers = pd.concat(pieces, axis=1) if pieces else pd.DataFrame(index=grid)
    for column in rules:
        if column not in drivers:
            drivers[column] = np.nan
        if require_observations and not drivers[column].notna().any():
            raise ValueError(f'No observations for driver {column}')
    return drivers


def station_grid(raw, grid, drivers):
    raw = raw.copy()
    raw = raw[np.isfinite(raw.CS) & raw.CS.ge(50)]
    for column, bounds in {'foF2': (.5, 25), 'hmF2': (100, 700), 'TEC': (0, 300)}.items():
        raw.loc[~raw[column].between(*bounds), column] = np.nan
    normalized = normalize_giro(raw, '15min', None, '30min')
    if normalized.empty:
        return None
    normalized = normalized.set_index('time_utc').reindex(grid)
    normalized['had_original_giro_row'] = normalized.had_original_giro_row.fillna(False).astype(bool)
    normalized['giro_rows_in_interval'] = normalized.giro_rows_in_interval.fillna(0).astype(int)
    return normalized.join(drivers).rename_axis('time_utc').reset_index()


def prepare_live(data_dir, normalized_dir, features_dir, stations, end, *, history_days=8):
    """Read only collected values, retain missing drivers, never fill observations."""
    import json
    import hashlib
    import os
    from data_preparation.normalize_time_grid import read_giro_raw
    from feature_engineering.build_features import build_station_features, load_config

    root = Path(__file__).resolve().parents[1]
    end = pd.Timestamp(end).tz_convert('UTC').floor('15min')
    grid = pd.date_range(end - pd.Timedelta(days=history_days), end, inclusive='left', freq='15min')
    drivers = read_drivers(Path(data_dir) / 'processed', grid, require_observations=False)
    config = load_config(root / 'feature_engineering/configs/feature_engineering.json')
    metadata = pd.read_csv(os.getenv('IONOSCOPE_STATION_METADATA', str(root / 'data_preparation/configs/stations_metadata.csv')))[['station', 'latitude', 'longitude_180']].drop_duplicates('station')
    normalized_dir, features_dir = Path(normalized_dir), Path(features_dir)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (features_dir / 'stations').mkdir(parents=True, exist_ok=True)
    for code in stations:
        raw = read_giro_raw(Path(data_dir) / f'raw/giro/station={code}', include_stations={code})
        if raw.empty:
            continue
        raw = raw[(raw.time_utc >= grid.min()) & (raw.time_utc < end)]
        frame = station_grid(raw, grid, drivers)
        if frame is None:
            continue
        frame['station'] = code
        path = normalized_dir / f'{code}_time_grid.csv'
        frame.to_csv(path, index=False)
        build_station_features(path, config, metadata).to_csv(features_dir / 'stations' / f'{code}_features.csv', index=False)
    (features_dir / 'input_contract.json').write_text(json.dumps({
        'profile': PROFILE, 'end': str(end), 'history_days': history_days,
        'implementation_sha256': hashlib.sha256(b''.join(p.read_bytes() for p in [
            Path(__file__), root / 'data_preparation/normalize_time_grid.py',
            root / 'feature_engineering/build_features.py',
            root / 'feature_engineering/configs/feature_engineering.json'])).hexdigest(),
        'driver_rules': driver_rules(), 'limits': ['Actual receipt times supersede period-end lower bounds',
        'Unavailable drivers remain missing; no substitution with NOAA products']}), encoding='utf-8')
