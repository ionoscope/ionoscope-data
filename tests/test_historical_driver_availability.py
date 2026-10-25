import pandas as pd

from data_preparation.normalize_time_grid import propagate_interval_features


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
