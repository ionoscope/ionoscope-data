import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from api import services
from api.schemas import Station
from data_preparation.normalize_time_grid import normalize_giro


class ObservationTests(unittest.TestCase):
    def test_disabled_cs_filter_retains_low_scores_without_filling_absent_values(self):
        frame = pd.DataFrame({
            "station": ["TR169"] * 6,
            "time_utc": pd.to_datetime(["2026-10-23T17:00:00Z", "2026-10-23T17:15:00Z",
                "2026-10-23T17:30:00Z", "2026-10-23T17:45:00Z",
                "2026-10-23T18:00:00Z", "2026-10-23T18:15:00Z"]),
            "CS": [999, -1, float("nan"), 25, 0, 75],
            "foF2": [5, 4.5, 4.3, 4.1, 3.1, float("nan")],
        })
        unfiltered = normalize_giro(frame, "15min", None, "30min")
        filtered = normalize_giro(frame, "15min", 50, "30min")
        self.assertEqual(unfiltered.foF2.notna().sum(), 5)
        self.assertEqual(filtered.foF2.notna().sum(), 3)
        self.assertEqual(unfiltered.iloc[-2].CS, 0)
        self.assertEqual(unfiltered.iloc[-2].foF2_source_timestamp, pd.Timestamp("2026-10-23T18:00:00Z"))
        self.assertTrue(pd.isna(unfiltered.iloc[-1].foF2))
        self.assertTrue(pd.isna(unfiltered.iloc[-1].foF2_source_timestamp))

    def test_normalization_keeps_real_time_and_does_not_fill_missing_fof2(self):
        frame = pd.DataFrame({
            "station": ["TR169"] * 4,
            "time_utc": pd.to_datetime(["2026-10-23T17:45:00Z", "2026-10-23T17:49:29Z",
                                        "2026-10-23T18:04:29Z", "2026-10-23T18:19:30Z"]),
            "CS": [55, 60, 0, 75], "foF2": [4.05, 4.15, 3.1, float("nan")],
        })
        result = normalize_giro(frame, "15min", 50, "30min")
        self.assertAlmostEqual(result.iloc[0].foF2, 4.1)
        self.assertEqual(result.iloc[0].foF2_source_timestamp, pd.Timestamp("2026-10-23T17:49:29Z"))
        self.assertTrue(pd.isna(result.iloc[-1].foF2))
        self.assertTrue(pd.isna(result.iloc[-1].foF2_source_timestamp))

    def test_latest_distinguishes_grid_and_actual_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "TR169_features.csv"
            path.write_text("time_utc,foF2,foF2_source_timestamp,foF2_state\n"
                "2026-10-23T17:45:00Z,4.05,2026-10-23T17:49:29Z,4.05\n"
                "2026-10-23T18:15:00Z,,,\n")
            with patch.object(services, "_feature_file", return_value=path), patch.object(
                services, "get_station", return_value=Station(code="TR169")
            ):
                result = services.get_latest_features("TR169")
            self.assertFalse(result.current_fof2_available)
            self.assertIsNone(result.observation_timestamp)
            self.assertEqual(result.latest_fof2_timestamp.isoformat(), "2026-10-23T17:49:29+00:00")
            self.assertEqual(result.timestamp.isoformat(), "2026-10-23T18:15:00+00:00")
            self.assertNotIn("foF2", result.features)

    def test_legacy_table_does_not_invent_observation_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "TR169_features.csv"
            path.write_text("time_utc,foF2\n2026-10-23T18:15:00Z,4.05\n")
            with patch.object(services, "_feature_file", return_value=path), patch.object(
                services, "get_station", return_value=Station(code="TR169")
            ):
                result = services.get_latest_features("TR169")
            self.assertTrue(result.current_fof2_available)
            self.assertIsNone(result.observation_timestamp)
            self.assertIsNone(result.latest_fof2_timestamp)


if __name__ == "__main__":
    unittest.main()
