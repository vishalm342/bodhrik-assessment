import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.review import Review
    from app.models.user import User


class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Booking(Base):
    __tablename__ = "bookings"

    __table_args__ = (
        CheckConstraint(
            "provider_id <> customer_id",
            name="ck_booking_distinct_users",
        ),
        CheckConstraint(
            "scheduled_end > scheduled_start",
            name="ck_booking_valid_schedule",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    provider_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    customer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    service_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    scheduled_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    scheduled_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus),
        default=BookingStatus.PENDING,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    provider: Mapped["User"] = relationship(
        back_populates="provider_bookings",
        foreign_keys=[provider_id],
    )

    customer: Mapped["User"] = relationship(
        back_populates="customer_bookings",
        foreign_keys=[customer_id],
    )

    review: Mapped["Review | None"] = relationship(
        back_populates="booking",
        uselist=False,
        cascade="all, delete-orphan",
    )