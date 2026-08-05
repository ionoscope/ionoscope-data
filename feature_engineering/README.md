# Feature Engineering

This module builds forecasting feature tables from normalized station time-grid
CSV files.

## Contents

- `build_features.py` - builds lag, difference, time-cycle, driver and forecast-target features.
- `configs/feature_engineering.json` - feature generation settings.
- `configs/stations_metadata.csv` - optional station metadata joined into feature tables.

## Run

From this folder:

```powershell
python .\build_features.py --input-dir ..\data_preparation\normalized_by_station --config .\configs\feature_engineering.json --output-dir .\features
```

The generated feature tables are intended for downstream ML and AutoML
experiments.
