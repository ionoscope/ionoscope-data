from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    observation_timestamp: datetime | None = None
    latest_fof2_timestamp: datetime | None = None
    current_fof2_available: bool = False
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
    station: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    start: datetime
    end: datetime
    sources: str = "all"

    @field_validator("start", "end")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, value: str) -> str:
        sources = [item.strip().lower() for item in value.split(",")]
        if not sources or any(item not in {"all", "giro", "gfz", "omni", "noaa"} for item in sources):
            raise ValueError("Unsupported collection source.")
        return ",".join(dict.fromkeys(sources))


class UpdateTask(BaseModel):
    task_id: str
    status: Literal["queued", "running", "completed", "failed"]
    station: str
    start: datetime
    end: datetime
    sources: str = "all"
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str
    error: str | None = None
