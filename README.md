# Ionoscope Data

## Automatic live updates

The infrastructure Compose stack runs `scripts/run_live_worker.py`: every 5
minutes it rechecks selected GIRO stations and collects their observations, accumulating history through
`incremental_update.py`, atomically publishes prepared features, then refreshes
the main API forecast/cache. Failures retry after 60 seconds. State and history
persist in `live_data/`; Data API uses `IONOSCOPE_DATA_WORKSPACE=/app/live_data`.
A separate `station-discovery` worker scans the full catalog on an hourly
schedule, publishing candidates incrementally in `live_data/discovery/`.
Candidates do not authorize forecasts: the live worker still checks recent
physical foF2, CS and completed intervals. A shared rate gate enforces six-second
spacing and HTTP 429 cooldown across processes; background discovery yields to
live updates. A slow catalog scan does not block the foreground cycle.
`GET /api/v1/live/status` reports progress and last success. See
[ionoscope-infrastructure](https://github.com/ionoscope/ionoscope-infrastructure)
and its `.env.example` for the
versioned Compose files, collection sources, windows and restart settings.
On Windows, run `./start.ps1 -RestartDocker -NoBuild` from that sibling checkout
to restart Docker Desktop and explicitly restore the Compose stack.

## Station audit workbook

[Station audit, 24 October 2026](docs/reports/station_audit_2026-10-24.xlsx)
contains a summary and all 105 stations from the original catalog. It preserves
the reported snapshot at 10:14 UTC: 30 stations were not verified because GIRO
limited requests. These are not classified as permanently inactive. Blank
observations are distinct from zero counts. Later live checks may differ.

## Deferred update queue

Queue an update without running collection (run from this repository):

```powershell
python enqueue_update.py --station TR169 --start 2026-01-01T00:00:00Z --end 2026-01-02T00:00:00Z
python run_update_tasks.py --limit 10
python scripts/run_update_worker.py --interval-seconds 60 --limit 10
```

Alternatively, submit the same station, start, end and optional sources with
`POST /api/v1/updates` (HTTP 202), then poll `GET /api/v1/updates/{task_id}`.
The API only queues work; the worker runs the preparation pipeline separately.
Statuses are `queued`, `running`, `completed`, `failed`.

The local JSON queue is ordered by creation time and written atomically.
An OS file lock permits one active worker per queue directory, including while
the pipeline runs. Use a local filesystem shared by the API and worker.
Do not run the direct preparation command concurrently against the same outputs.
Failures retain their error text and do not block subsequent tasks. On restart,
interrupted running tasks become failed; submit a new task to retry them.
`--limit 0` processes all queued tasks. `--dry-run` previews commands without
changing statuses or launching collection. Completed means the pipeline exited
successfully; requested data coverage can be checked through the availability API.

Run automated tests with:

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

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

Queued update tasks can be processed by a worker:

```powershell
python .\run_update_tasks.py
```

For a scheduler, run this worker periodically. It moves tasks from `queued` to
`running`, then to `completed` or `failed`.

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

## Observation provenance

The latest-features endpoint keeps `timestamp` as the 15-minute grid label.
`observation_timestamp` is the actual latest accepted foF2 measurement time
within that interval, and is null when current foF2 is absent.
`latest_fof2_timestamp` reports the last accepted foF2 time across the table,
for diagnostics only. `current_fof2_available` reports whether the current row
contains finite foF2. Previous measurements are never carried forward.
Normalization persists `foF2_source_timestamp`; existing tables must be rebuilt
from raw data to obtain verified provenance. Legacy tables do not invent it.

## Incremental local updates

Use a separate persistent workspace. For an initial import of an existing
collector run, execute from ionoscope-data:

```powershell
python incremental_update.py --stations TR169 --workspace runs/tr169_incremental --seed-dir runs/live_20261023_182336/data --sources giro,gfz
```

Subsequent calls omit `--seed-dir`:

```powershell
python incremental_update.py --stations TR169 --workspace runs/tr169_incremental --sources giro,gfz
```

Without a seed, the first call downloads eight days. Later calls download from
the previous query end minus `--overlap-hours` (default 48) through now, to
recheck delayed/revised observations. This is download incrementality: cleaning
and feature generation still rebuild over the accumulated local history.
An empty source result does not delete previous records. Coverage gaps remain
visible; an advanced query watermark does not prove source completeness.
Use a larger overlap to recover older delayed observations.

Batches are immutable; newer overlapping observations replace older ones without
duplicating timestamps. NOAA energy channels and GFZ indices remain distinct.
Each rebuild gets a new generation. `state.json` switches atomically only after
successful preparation; a failed attempt leaves the active generation unchanged.
The command shares the existing update-worker lock. Stations and source selection
are fixed per workspace. `--prepare-only` rebuilds stored batches without network.
No scheduler or forecast refresh is installed by this command.

To serve the completed generation, start Data API with an absolute workspace path:

```powershell
$env:IONOSCOPE_DATA_WORKSPACE = (Resolve-Path runs/tr169_incremental).Path
python -m uvicorn api.main:app --port 8001
```

The latest-features reader follows the committed generation on every request;
its diagnostic output distinguishes grid time, actual foF2 time, and missing
current foF2. Range and availability endpoints use the same committed generation.
The example selects GIRO/GFZ explicitly because the earlier NOAA/OMNI problems
remain unresolved; this selection does not establish full model-input coverage.

## Experimental CS mode

`--cs-filter off` disables row rejection by autoscaling confidence, retaining
CS=0, low scores, unknown scores and manual scaling. It does not fill absent
foF2, change the time grid, disable forecast freshness checks or permit forecasts
without current foF2/provenance. Raw per-observation scores remain in immutable
batches; the CS field of the normalized interval is the mean of included rows,
not a probability or an individual foF2 quality guarantee.

Rebuild the current incremental workspace without network calls:

```powershell
python incremental_update.py --stations TR169 --workspace runs/tr169_incremental --sources giro,gfz --prepare-only --cs-filter off
```

The incremental workspace remembers this mode for subsequent runs. Return to
threshold filtering with `--cs-filter threshold --min-cs 50` (add `--prepare-only`
when no download is needed). Previous generations remain available for comparison.
The same switches are accepted by update_prepared_data.py and
 data_preparation/normalize_time_grid.py; those commands default to threshold 50.
Each normalization report records the applied filter. This experiment changes
input coverage; it does not validate model quality with low-confidence inputs.


## API interval and validation behavior

Latest features use the most recent COMPLETED 15-minute interval (UTC), selected
by time rather than CSV row order. `timestamp` labels interval start and
`source_interval_end` labels interval end. A still-open interval is not used.
Missing/nonfinite feature values and future target columns are not serialized
as model inputs. Range responses also expose interval end and observation time.
Timezone-less range boundaries are interpreted as UTC; explicit offsets are
normalized before comparisons. Availability refers to prepared time coverage,
not a guarantee that every observation is present.

Station listings recheck prepared-file availability. An uninitialized incremental
workspace is treated as missing data instead of crashing. Unknown stations and
missing feature tables both return 404 with distinct detail codes
`station_not_found` and `features_unavailable`.
`IONOSCOPE_FEATURES_DIR` optionally selects a prepared feature root containing
`stations/`; an incremental workspace takes precedence when configured.


`IONOSCOPE_UPDATE_TASK_DIR` optionally selects the queue directory (default:
update_tasks/). This permits isolated API checks without using an operator queue.
Corrupt task files and storage failures return HTTP 503 with
`detail.code = "data_store_unavailable"`. Error responses are documented in OpenAPI.

## Candidate input profile

Compose enables `IONOSCOPE_INPUT_PROFILE=candidate-cs50-period-end-v1`.
The live updater shares the holdout implementation in
`data_preparation/candidate_inputs.py`: strict known CS >= 50, physical bounds,
missing values retained, delayed GFZ/OMNI drivers, and eight days of lag history.
The committed generation includes `features/input_contract.json` and the state
records its profile and preparation time. Source additions trigger history
backfill. Missing external drivers stay missing; failed generations never
replace the active pointer. Standalone legacy preparation remains available.
