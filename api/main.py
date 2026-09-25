from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .schemas import FeatureResponse, HealthResponse, Station, StationListResponse
from .services import FeatureNotFoundError, StationNotFoundError, get_latest_features, get_station, list_stations


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
