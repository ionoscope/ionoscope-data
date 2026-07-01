# Data Collection and Preprocessing

This folder contains the first IonoScope module: automated collection and preprocessing of ionospheric and space-weather data from GIRO and NOAA.

## Contents

- `collect_hf_data.py` - main Python script.
- `config.toml` - source and preprocessing settings.
- `update_data.ps1` - Windows helper script for running the update.
- `requirements.txt` - minimal Python dependencies for this module.

## Run

```powershell
git clone https://github.com/ionoscope/ionoscope-data.git
Set-Location .\ionoscope-data
python .\collect_hf_data.py --config .\config.toml
```

Run for a custom UTC interval:

```powershell
python .\collect_hf_data.py --config .\config.toml --start 2012-07-02T21:00:00Z --end 2012-07-03T03:00:00Z
```

## Output

By default, files are written to `data/` inside this module:

- `data/raw/giro`
- `data/raw/noaa`
- `data/processed/giro_scaled.csv`
- `data/processed/giro_scaled.json`
- `data/processed/noaa_observations.csv`
- `data/processed/noaa_observations.json`
- `data/processed/analytical_hf_dataset.csv`
- `data/processed/analytical_hf_dataset.json`
- `data/run_manifest.json`

JSON files use the same envelope:

```json
{
  "schema_version": "1.0",
  "dataset": "dataset_name",
  "record_count": 1,
  "records": []
}
```

GIRO stations may have no data for some time intervals. In that case the script writes diagnostic rows and continues processing NOAA data.

## Tests

```powershell
python -m unittest discover -s tests
```
