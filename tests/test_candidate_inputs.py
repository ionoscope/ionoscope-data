import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from data_preparation.candidate_inputs import read_drivers, station_grid, PROFILE


class CandidateInputTests(unittest.TestCase):
    def test_strict_confidence_physical_bounds_and_missing_interval(self):
        grid = pd.date_range('2025-10-01', periods=8, freq='15min', tz='UTC')
        raw = pd.DataFrame({'time_utc': grid, 'station': 'TR169', 'CS': [90, -1, np.nan, 49, 50, 90, 90, 90],
                            'foF2': [5, 6, 7, 8, .4, 25.1, 9, 10], 'hmF2': 300., 'TEC': 30.})
        result = station_grid(raw, grid, pd.DataFrame(index=grid))
        self.assertEqual(result.foF2.notna().tolist(), [True, False, False, False, False, False, True, True])
        self.assertTrue(pd.isna(result.foF2_source_timestamp.iloc[1]))

    def test_external_indices_delayed_and_expire_without_fill_across_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grid = pd.date_range('2025-10-01', periods=40, freq='15min', tz='UTC')
            pd.DataFrame({'time_utc': [grid[0], grid[12], grid[24]], 'index': 'Kp', 'value': [3, np.nan, 4]}).to_csv(root/'geophysical_indices.csv', index=False)
            drivers = read_drivers(root, grid, require_observations=False)
            self.assertTrue(drivers.gfz_Kp.iloc[:12].isna().all())
            self.assertEqual(drivers.gfz_Kp.iloc[12], 3)
            self.assertTrue(drivers.gfz_Kp.iloc[24:36].isna().all())
            self.assertEqual(drivers.gfz_Kp.iloc[36], 4)
            self.assertTrue(drivers.dst_nT.isna().all())
            with self.assertRaises(ValueError):
                read_drivers(root, grid)
