import json
from pathlib import Path

import numpy as np
import pandas as pd

from data_preparation.normalize_time_grid import prepare_index_features, propagate_interval_features


def test_hourly_average_cannot_be_used_until_hour_finishes():
    frame = pd.DataFrame({'bz_gsm_nT': [7., 9.]}, index=pd.date_range('2025-09-01', periods=2, freq='1h', tz='UTC'))
    result = propagate_interval_features(frame, '15min', {'bz_gsm_nT': {
        'valid_for': '1h', 'availability_delay': '1h', 'preserve_missing': True}})
    assert result.loc[:'2025-09-01T00:45Z', 'bz_gsm_nT'].isna().all()
    assert result.loc['2025-09-01T01:00Z', 'bz_gsm_nT'] == 7
    assert result.loc['2025-09-01T02:00Z', 'bz_gsm_nT'] == 9
    assert result.loc['2025-09-01T01:00Z', 'Bz_source_time'] == frame.index[0]


def test_missing_hour_does_not_revive_previous_hour_value():
    frame = pd.DataFrame({'bz_gsm_nT': [7., float('nan'), 9.]},
                         index=pd.date_range('2025-09-01', periods=3, freq='1h', tz='UTC'))
    result = propagate_interval_features(frame, '15min', {'bz_gsm_nT': {
        'valid_for': '1h', 'availability_delay': '1h', 'preserve_missing': True}})
    assert result.loc['2025-09-01T02:00Z':'2025-09-01T02:45Z', 'bz_gsm_nT'].isna().all()


def test_future_driver_cannot_change_previous_inputs():
    frame = pd.DataFrame({'gfz_Kp': [1., 2., 3.]}, index=pd.date_range('2025-09-01', periods=3, freq='3h', tz='UTC'))
    rules = {'gfz_Kp': {'valid_for': '3h', 'availability_delay': '3h', 'preserve_missing': True}}
    before = propagate_interval_features(frame, '15min', rules)
    frame.iloc[-1, 0] = 999
    after = propagate_interval_features(frame, '15min', rules)
    pd.testing.assert_frame_equal(before.loc[:'2025-09-01T08:45Z'], after.loc[:'2025-09-01T08:45Z'])


def test_shipped_rules_dont_release_any_driver_before_its_interval_ends():
    rules = json.loads(
        (Path(__file__).resolve().parents[1] / 'data_preparation/configs/time_normalization.json').read_text(encoding='utf-8')
    )['variables']
    assert not [
        name for name, rule in rules.items()
        if 'valid_for' in rule and pd.Timedelta(rule.get('availability_delay', '0s')) < pd.Timedelta(rule['valid_for'])
    ], 'shipped rules released a driver before its interval ended'


def test_shipped_rules_dont_show_daily_index_during_its_own_day(tmp_path):
    pd.DataFrame({
        'time_utc': pd.date_range('2025-03-05', periods=3, freq='1D', tz='UTC').strftime('%Y-%m-%dT%H:%M:%SZ'),
        'index': ['Ap'] * 3,
        'value': np.random.default_rng().uniform(2.0, 90.0, size=3),
    }).to_csv(tmp_path / 'geophysical_indices.csv', index=False)
    prepared = prepare_index_features(tmp_path, '15min', json.loads(
        (Path(__file__).resolve().parents[1] / 'data_preparation/configs/time_normalization.json').read_text(encoding='utf-8')
    ))
    assert prepared.loc[prepared['time_utc'] < pd.Timestamp('2025-03-06', tz='UTC'), 'gfz_Ap'].isna().all(), \
        'daily index leaked into the day it summarizes'


def test_shipped_rules_dont_show_hourly_driver_inside_its_own_hour(tmp_path):
    pd.DataFrame({
        'time_utc': pd.date_range('2024-08-21T10:00:00Z', periods=4, freq='1h').strftime('%Y-%m-%dT%H:%M:%SZ'),
        'dst_nT': np.random.default_rng().normal(-40.0, 25.0, size=4),
    }).to_csv(tmp_path / 'omni_solar_wind.csv', index=False)
    prepared = prepare_index_features(tmp_path, '15min', json.loads(
        (Path(__file__).resolve().parents[1] / 'data_preparation/configs/time_normalization.json').read_text(encoding='utf-8')
    ))
    assert prepared.loc[prepared['time_utc'] < pd.Timestamp('2024-08-21T11:00:00Z'), 'dst_nT'].isna().all(), \
        'hourly driver leaked into the hour it averages'


def test_shipped_rules_keep_rows_unchanged_when_unfinished_intervals_change():
    rng = np.random.default_rng()
    rules = json.loads(
        (Path(__file__).resolve().parents[1] / 'data_preparation/configs/time_normalization.json').read_text(encoding='utf-8')
    )['variables']
    frame = pd.DataFrame(
        {'bz_gsm_nT': rng.normal(0.0, 6.0, size=48), 'gfz_Kp': rng.uniform(0.0, 9.0, size=48)},
        index=pd.date_range('2025-02-10', periods=48, freq='1h', tz='UTC'),
    )
    cutoff = pd.Timestamp('2025-02-11T05:30:00Z')
    changed = frame.copy()
    changed.loc[changed.index + pd.Timedelta('1h') > cutoff, 'bz_gsm_nT'] += 1000.0
    changed.loc[changed.index + pd.Timedelta('3h') > cutoff, 'gfz_Kp'] += 1000.0
    assert propagate_interval_features(changed, '15min', rules).loc[:cutoff, ['bz_gsm_nT', 'gfz_Kp']].equals(
        propagate_interval_features(frame, '15min', rules).loc[:cutoff, ['bz_gsm_nT', 'gfz_Kp']]
    ), 'rows before the cutoff depended on intervals that had not finished'
