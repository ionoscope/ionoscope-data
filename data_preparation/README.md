# Data Preparation

This module contains data cleaning and time-grid normalization copied from the
AutoML project.

## Contents

- `clean_collected_data.py` - creates soft-cleaned copies of collected CSV files and a cleaning report.
- `normalize_time_grid.py` - normalizes GIRO and index data to one UTC time grid.
- `configs/time_normalization.json` - rules for aligning driver variables and GIRO observations.
- `configs/station_selection.json` - station filtering settings.
- `configs/stations_metadata.*` - station metadata used by downstream steps.
- `configs/station_sets/` - reusable station subsets.

## Run

From this folder:

```powershell
python .\clean_collected_data.py --input-dir ..\data_collection_preprocessing\data --output-dir .\cleaned
python .\normalize_time_grid.py --processed-dir .\cleaned\processed --output-dir .\normalized --time-config .\configs\time_normalization.json
```

Large historical runs can pass explicit input and output folders from the
collection module.
