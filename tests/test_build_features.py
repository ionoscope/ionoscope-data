import numpy as np
import pandas as pd

from feature_engineering.build_features import add_lag_features, add_targets, build_station_features, feature_columns


def test_lag_features_dont_change_when_later_values_change() -> None:
    rng = np.random.default_rng()
    frame = pd.DataFrame({"foF2": rng.uniform(2.0, 14.0, size=120), "gfz_Kp": rng.uniform(0.0, 9.0, size=120)})
    config = {
        "dataset_step": "15min",
        "targets": ["foF2"],
        "state_columns": ["foF2"],
        "lag_columns": ["foF2", "gfz_Kp"],
        "lags": ["15min", "3h"],
        "diff_lags": ["1h"],
    }
    changed = frame.copy()
    changed.loc[71:, ["foF2", "gfz_Kp"]] += 500.0
    assert add_lag_features(changed, config).loc[:70].equals(add_lag_features(frame, config).loc[:70]), "lag features depended on later values"


def test_target_holds_the_value_one_horizon_ahead() -> None:
    frame = pd.DataFrame({"foF2": np.random.default_rng().uniform(2.0, 14.0, size=40)})
    targets = add_targets(frame, {"dataset_step": "15min", "targets": ["foF2"], "forecast_horizons": ["3h"]})
    assert targets["foF2_target_3h"].iloc[:28].tolist() == frame["foF2"].iloc[12:].tolist(), "target was not shifted by exactly one horizon"


def test_feature_columns_dont_include_any_target() -> None:
    frame = pd.DataFrame({"foF2": np.random.default_rng().uniform(2.0, 14.0, size=200)})
    config = {"dataset_step": "15min", "targets": ["foF2"], "forecast_horizons": ["15min", "1h", "24h"]}
    assert not [column for column in feature_columns(add_targets(frame, config), config) if "_target_" in column], "target column was offered as a feature"


def test_station_features_dont_change_when_later_rows_change(tmp_path) -> None:
    rng = np.random.default_rng()
    frame = pd.DataFrame(
        {
            "time_utc": pd.date_range("2025-05-03T00:00:00Z", periods=300, freq="15min").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "station": ["XX901"] * 300,
            "foF2": rng.uniform(2.0, 14.0, size=300),
            "hmF2": rng.uniform(200.0, 400.0, size=300),
            "dst_nT": rng.normal(-20.0, 30.0, size=300),
        }
    )
    config = {
        "dataset_step": "15min",
        "targets": ["foF2"],
        "forecast_horizons": ["1h", "24h"],
        "state_columns": ["foF2", "hmF2"],
        "lag_columns": ["foF2", "hmF2", "dst_nT"],
        "lags": ["15min", "6h", "24h"],
        "diff_lags": ["15min", "3h"],
    }
    changed = frame.copy()
    changed.loc[181:, ["foF2", "hmF2", "dst_nT"]] *= -7.0
    frame.to_csv(tmp_path / "original.csv", index=False)
    changed.to_csv(tmp_path / "changed.csv", index=False)
    original = build_station_features(tmp_path / "original.csv", config, None)
    assert build_station_features(tmp_path / "changed.csv", config, None).loc[:180, feature_columns(original, config)].equals(
        original.loc[:180, feature_columns(original, config)]
    ), "features of earlier rows depended on later rows"
