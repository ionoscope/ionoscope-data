# Ionoscope Data

This repository contains the shared data layer for Ionoscope. It owns data
collection, data preparation and feature engineering. Downstream ML and LLM
projects should consume prepared outputs from this repository instead of
duplicating the same data logic.

## Repository Structure

- `collect_hf_data.py` - collection of GIRO, NOAA/SWPC, GFZ and OMNI data.
- `config.toml` - default collection configuration.
- `config.all_stations.temp.toml` - helper config for broad all-station pulls.
- `update_data.ps1` - Windows helper script for running collection.
- `data_preparation/` - cleaning, quality checks and normalization to a common UTC time grid.
- `feature_engineering/` - leak-aware feature table generation for forecasting experiments.
- `api/` - FastAPI access layer for station metadata and prepared features.
- `tests/` - parser and collection tests.

Generated datasets, raw downloads, logs and cache files are intentionally not
tracked by Git.

## Pipeline

The data pipeline is split into three stages:

1. Collection: download and normalize raw source records from GIRO, NOAA/SWPC,
   GFZ and OMNI.
2. Preparation: clean collected CSV files, evaluate quality and align station
   observations and external indices to a common time grid.
3. Feature engineering: build lag, difference, time-cycle, driver and forecast
   target columns for ML and AutoML experiments.

## Install

Python 3.11+ is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -r data_preparation\requirements.txt
python -m pip install -r feature_engineering\requirements.txt
```

## Run Collection

Use the default interval from `config.toml`:

```powershell
python .\collect_hf_data.py --config .\config.toml
```

Run for a custom UTC interval:

```powershell
python .\collect_hf_data.py --config .\config.toml --start 2024-01-01T00:00:00Z --end 2024-02-01T00:00:00Z
```

Collect only selected sources:

```powershell
python .\collect_hf_data.py --config .\config.toml --sources giro,gfz
```

By default, collection writes files to `data/`:

- `data/raw/giro`
- `data/raw/noaa`
- `data/raw/gfz`
- `data/raw/omni`
- `data/processed/giro_scaled.csv`
- `data/processed/noaa_observations.csv`
- `data/processed/geophysical_indices.csv`
- `data/processed/omni_solar_wind.csv`
- `data/processed/analytical_hf_dataset.csv`
- `data/metadata/stations.json`
- `data/logs/collection_events.jsonl`
- `data/run_manifest.json`

## Run Data Preparation

```powershell
python .\data_preparation\clean_collected_data.py --input-dir .\data --output-dir .\cleaned
python .\data_preparation\normalize_time_grid.py --processed-dir .\cleaned\processed --output-dir .\normalized --time-config .\data_preparation\configs\time_normalization.json
```

For large station sets, use the station selection configs in
`data_preparation/configs/`.

## Run Feature Engineering

```powershell
python .\feature_engineering\build_features.py --input-dir .\normalized_by_station --config .\feature_engineering\configs\feature_engineering.json --output-dir .\features
```

The feature builder creates target columns separately from feature columns. Lag
features use past values, and forecast targets use future values with
`shift(-horizon)`.

## Update Prepared API Data

API requests should read already prepared feature tables. Run the update command
ahead of user traffic, manually or from a scheduler:

```powershell
python .\update_prepared_data.py --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z --station-set .\data_preparation\configs\station_sets\exploration_v0.1.json
```

The update writes feature tables to `features/stations/`, where the Data API can
serve them immediately.

## Run Data API

```powershell
python -m uvicorn api.main:app --reload --port 8001
```

Initial endpoints:

- `GET /health`
- `GET /api/v1/stations`
- `GET /api/v1/stations/{station}`
- `GET /api/v1/features/{station}/latest`
- `GET /api/v1/features/{station}?start=...&end=...`
- `GET /api/v1/availability/{station}?start=...&end=...`
- `POST /api/v1/updates`
- `GET /api/v1/updates/{task_id}`

The API is designed to serve already prepared features quickly. If the requested
station and period are not available yet, create an update task and let a
scheduler or worker run `update_prepared_data.py` before retrying the request.

## JSON Envelope

Processed JSON files use the same envelope:

```json
{
  "schema_version": "1.0",
  "dataset": "dataset_name",
  "record_count": 1,
  "records": []
}
```

## Notes

GIRO stations may have no data for some time intervals. In that case the
collector writes diagnostic rows and continues processing the available external
indices when `continue_on_error` is enabled.

Current GIRO station values are used as state features for forecasting from an
available observation. If a downstream model must forecast without current GIRO
measurements, those state columns should be excluded and only lagged values
should be used.

## Tests

```powershell
python -m unittest discover -s tests
```
