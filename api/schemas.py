from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class Station(BaseModel):
    code: str
    country: str | None = None
    location: str | None = None
    system_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    longitude_180: float | None = None
    data_available: bool = False


class StationListResponse(BaseModel):
    stations: list[Station]


class FeatureResponse(BaseModel):
    station: str
    timestamp: datetime
    features: dict[str, float] = Field(default_factory=dict)


class FeatureSeriesResponse(BaseModel):
    station: str
    start: datetime
    end: datetime
    records: list[FeatureResponse]


class DataAvailabilityResponse(BaseModel):
    station: str
    requested_start: datetime
    requested_end: datetime
    available: bool
    available_from: datetime | None = None
    available_to: datetime | None = None
    feature_file: str | None = None
    update_required: bool = False


class UpdateRequest(BaseModel):
    station: str
    start: datetime
    end: datetime
    sources: str = "all"


class UpdateTask(BaseModel):
    task_id: str
    status: str
    station: str
    start: datetime
    end: datetime
    sources: str = "all"
    created_at: datetime
    message: str
