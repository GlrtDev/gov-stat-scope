# backend/app/adapters/schemas.py
from datetime import datetime
from typing import List, Union

from pydantic import BaseModel, Field

from app.models import DataSource


class DataPoint(BaseModel):
    date: Union[str, datetime]
    value: float


class NormalizedSeries(BaseModel):
    source: DataSource
    metric_name: str
    region: str
    time_period: str
    values: List[DataPoint] = Field(default_factory=list)