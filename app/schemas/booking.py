from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.models.booking import BookingStatus

ServiceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
UserId = Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]


class BookingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: UserId
    # Only admins may supply this; customers use their authenticated identity.
    customer_id: UserId | None = None
    service_name: ServiceName
    scheduled_start: AwareDatetime
    scheduled_end: AwareDatetime

    @model_validator(mode="after")
    def valid_schedule(self) -> Self:
        if self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled_end must be after scheduled_start")
        return self


class BookingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: ServiceName | None = None
    scheduled_start: AwareDatetime | None = None
    scheduled_end: AwareDatetime | None = None
    status: BookingStatus | None = None

    @model_validator(mode="after")
    def valid_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Booking fields cannot be null")
        if self.scheduled_start is not None and self.scheduled_end is not None:
            if self.scheduled_end <= self.scheduled_start:
                raise ValueError("scheduled_end must be after scheduled_start")
        return self


class BookingPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    customer_id: int
    service_name: str
    scheduled_start: datetime
    scheduled_end: datetime
    status: BookingStatus
    created_at: datetime
    updated_at: datetime
