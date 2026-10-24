# Docker deployment

Local layout:

```text
ionoscope-data/
  deployment/
ionoscope-api/
ionoscope-ml-service/
```

From this directory, run `./start.ps1` on Windows. It starts Docker Desktop if
needed and starts the existing `ionoscope-api-check` Compose project. Use
`./start.ps1 -NoBuild` when no rebuild is needed. Or run directly:

```powershell
docker compose -f docker-compose.dev.yml -p ionoscope-api-check up -d --build --wait
```

API docs: http://localhost:8000/docs . Data API: http://localhost:8001/docs .
Model API: http://localhost:8002/docs . Live status:
http://localhost:8001/api/v1/live/status . Stop without deleting data:

```powershell
docker compose -f docker-compose.dev.yml -p ionoscope-api-check stop
```

The default worker selects operational stations automatically. FoF2 must be
finite, within 0.5–25 MHz, measured in a completed 15-minute interval, no more
than 60 minutes old, with CS >= 50. Current forecast selection additionally
requires prepared features and a forecast target that has not passed. An
inactive station cannot bypass the check by using an old cached forecast.

The audit in `../live_data/station_registry.json` records exclusion reasons and
`diagnosis`: no measurements in the three-hour window, missing foF2, old foF2,
low CS, unknown CS, invalid physical values, incomplete interval, source failure
or a station missing from the current GIRO catalog. A source failure does not
establish that a station has stopped operating.

Each cycle collects GIRO, GFZ and current NOAA RTSW observations, prepares a new
immutable generation, publishes its pointer atomically and refreshes forecast
cache. The normal period is 900 seconds from cycle start; failures retry after
60 seconds. Initial history is 24 hours, subsequent pulls use a three-hour
overlap. History and cache persist on host mounts. History is retained; monitor
disk usage. Changing collection sources requires a separate workspace.

GIRO requests are spaced at least six seconds apart. HTTP 429 opens a cooldown
of at least 60 seconds or Retry-After, without burst retries. Confirmed checks
can be reused for up to ten minutes without extending observation lifetime.
Activity checks expire after 30 minutes. A failed station does not prevent
other stations from being refreshed. Timeouts terminate Linux child process
groups and release the preparation lock.

All services restart with Docker Engine unless explicitly stopped. Docker
Desktop must be running; the launcher does not change Windows login autostart.

For production, copy `.env.example` to `.env`, set real published image tags,
provide the trusted model artifact in `ml_models`, then run:

```powershell
docker compose --env-file .env -f docker-compose.prod.yml up -d
```

Production uses private services and a public nginx gateway. The data worker has
outbound access for collection. Configure TLS separately and back up persistent
volumes. The production configuration was validated; this work did not deploy a
production server. The development stack was tested with real observations.
