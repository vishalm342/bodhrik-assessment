from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]
    rating: Annotated[int, Field(strict=True, ge=1, le=5)]
    comment: str | None = None


class ReviewPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    rating: int
    comment: str | None
    created_at: datetime


class SummaryJobResponse(BaseModel):
    job_id: UUID
    status: Literal["queued"] = "queued"
