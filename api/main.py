from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException, Query

from .catalog import DataUnavailableError, get_feature_catalog_entry, read_feature_range
from .schemas import (
    DataAvailabilityResponse,
    FeatureResponse,
    FeatureSeriesResponse,
    HealthResponse,
    Station,
    StationListResponse,
    UpdateRequest,
    UpdateTask,
)
from .services import FeatureNotFoundError, StationNotFoundError, get_latest_features, get_station, list_stations
from .update_tasks import UpdateTaskNotFoundError, create_update_task, get_update_task


app = FastAPI(
    title="IonoScope Data API",
    version="0.1.0",
    description="Prepared station metadata and feature access for IonoScope services.",
)


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="ionoscope-data")


@app.get("/api/v1/stations", response_model=StationListResponse, tags=["stations"])
def stations() -> StationListResponse:
    return StationListResponse(stations=list_stations())


@app.get("/api/v1/stations/{station}", response_model=Station, tags=["stations"])
def station_detail(station: str) -> Station:
    try:
        return get_station(station)
    except StationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/features/{station}/latest", response_model=FeatureResponse, tags=["features"])
def latest_features(station: str) -> FeatureResponse:
    try:
        return get_latest_features(station)
    except StationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FeatureNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/features/{station}", response_model=FeatureSeriesResponse, tags=["features"])
def feature_series(
    station: str,
    start: datetime = Query(..., description="UTC start timestamp."),
    end: datetime = Query(..., description="UTC end timestamp."),
) -> FeatureSeriesResponse:
    if end < start:
        raise HTTPException(status_code=400, detail="end must be greater than or equal to start.")
    try:
        get_station(station)
        records = read_feature_range(station, start, end)
    except StationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FeatureSeriesResponse(station=station.upper(), start=start, end=end, records=records)


@app.get("/api/v1/availability/{station}", response_model=DataAvailabilityResponse, tags=["features"])
def data_availability(
    station: str,
    start: datetime = Query(..., description="UTC start timestamp."),
    end: datetime = Query(..., description="UTC end timestamp."),
) -> DataAvailabilityResponse:
    if end < start:
        raise HTTPException(status_code=400, detail="end must be greater than or equal to start.")
    try:
        station_info = get_station(station)
    except StationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    entry = get_feature_catalog_entry(station_info.code)
    available = entry is not None and entry.available_from <= start and entry.available_to >= end
    return DataAvailabilityResponse(
        station=station_info.code,
        requested_start=start,
        requested_end=end,
        available=available,
        available_from=entry.available_from if entry else None,
        available_to=entry.available_to if entry else None,
        feature_file=str(entry.feature_file) if entry else None,
        update_required=not available,
    )


@app.post("/api/v1/updates", response_model=UpdateTask, status_code=202, tags=["updates"])
def request_update(request: UpdateRequest) -> UpdateTask:
    try:
        get_station(request.station)
    except StationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if request.end < request.start:
        raise HTTPException(status_code=400, detail="end must be greater than or equal to start.")
    return create_update_task(request)


@app.get("/api/v1/updates/{task_id}", response_model=UpdateTask, tags=["updates"])
def update_status(task_id: str) -> UpdateTask:
    try:
        return get_update_task(task_id)
    except UpdateTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
