from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import DatabaseSession, require_roles
from app.models.booking import Booking, BookingStatus
from app.models.user import User, UserRole
from app.schemas.booking import BookingCreate, BookingPublic, BookingUpdate

router = APIRouter(prefix="/bookings", tags=["Bookings"])
# Central role-level authorization runs before resource lookup.
BookingUser = Annotated[
    User, Depends(require_roles(UserRole.ADMIN, UserRole.CUSTOMER, UserRole.PROVIDER))
]
BookingCreator = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.CUSTOMER))]
BookingAdmin = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
BookingId = Annotated[int, Path(gt=0, le=2_147_483_647)]


def scoped_bookings(user: User) -> Select:
    query = select(Booking)
    if user.role == UserRole.ADMIN:
        return query
    if user.role == UserRole.PROVIDER:
        return query.where(Booking.provider_id == user.id)
    if user.role == UserRole.CUSTOMER:
        return query.where(Booking.customer_id == user.id)
    raise HTTPException(status_code=403, detail="Booking access is not allowed")


def get_booking(db: Session, user: User, booking_id: int, *, lock: bool = False) -> Booking:
    query = scoped_bookings(user).where(Booking.id == booking_id)
    if lock:
        # Serialize mutations so lifecycle checks use the current stored status.
        query = query.with_for_update()
    booking = db.scalar(query)
    if booking is None:
        # Missing and out-of-scope resources deliberately have the same response.
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


def require_participant(db: Session, user_id: int, role: UserRole) -> None:
    participant = db.get(User, user_id)
    if participant is None or participant.role != role:
        raise HTTPException(status_code=422, detail=f"A valid {role.value}_id is required")


def save_booking(db: Session, booking: Booking) -> Booking:
    try:
        db.add(booking)
        db.commit()
        db.refresh(booking)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Booking conflicts with database constraints") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to save booking") from None
    return booking


def validate_update(booking: Booking, payload: BookingUpdate, user: User) -> None:
    if user.role != UserRole.ADMIN:
        if booking.status in (BookingStatus.COMPLETED, BookingStatus.CANCELLED):
            raise HTTPException(status_code=409, detail="Completed or cancelled bookings cannot be edited")
        if payload.status is not None:
            if user.role == UserRole.CUSTOMER:
                if payload.status != BookingStatus.CANCELLED:
                    raise HTTPException(status_code=403, detail="Customers may only cancel bookings")
            else:
                transitions = {
                    BookingStatus.PENDING: {BookingStatus.CONFIRMED, BookingStatus.CANCELLED},
                    BookingStatus.CONFIRMED: {BookingStatus.COMPLETED, BookingStatus.CANCELLED},
                }
                if payload.status not in transitions[booking.status]:
                    raise HTTPException(status_code=409, detail="Invalid booking status transition")
    start = payload.scheduled_start if payload.scheduled_start is not None else booking.scheduled_start
    end = payload.scheduled_end if payload.scheduled_end is not None else booking.scheduled_end
    if end <= start:
        raise HTTPException(status_code=422, detail="scheduled_end must be after scheduled_start")


@router.post("", response_model=BookingPublic, status_code=201)
def create_booking(payload: BookingCreate, db: DatabaseSession, user: BookingCreator) -> Booking:
    if user.role == UserRole.CUSTOMER:
        if "customer_id" in payload.model_fields_set:
            raise HTTPException(status_code=403, detail="Customers cannot supply customer_id")
        customer_id = user.id
    else:  # The role dependency permits only customer or admin.
        if payload.customer_id is None:
            raise HTTPException(status_code=422, detail="Admin bookings require customer_id")
        customer_id = payload.customer_id
        require_participant(db, customer_id, UserRole.CUSTOMER)
    require_participant(db, payload.provider_id, UserRole.PROVIDER)
    booking = Booking(
        customer_id=customer_id,
        provider_id=payload.provider_id,
        service_name=payload.service_name,
        scheduled_start=payload.scheduled_start,
        scheduled_end=payload.scheduled_end,
        status=BookingStatus.PENDING,
    )
    return save_booking(db, booking)


@router.get("", response_model=list[BookingPublic])
def list_bookings(db: DatabaseSession, user: BookingUser) -> list[Booking]:
    return list(db.scalars(scoped_bookings(user).order_by(Booking.id)).all())


@router.get("/{booking_id}", response_model=BookingPublic)
def read_booking(booking_id: BookingId, db: DatabaseSession, user: BookingUser) -> Booking:
    return get_booking(db, user, booking_id)


@router.patch("/{booking_id}", response_model=BookingPublic)
def update_booking(
    booking_id: BookingId, payload: BookingUpdate, db: DatabaseSession, user: BookingUser
) -> Booking:
    booking = get_booking(db, user, booking_id, lock=True)
    validate_update(booking, payload, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(booking, field, value)
    return save_booking(db, booking)


@router.delete("/{booking_id}", status_code=204)
def delete_booking(booking_id: BookingId, db: DatabaseSession, user: BookingAdmin) -> Response:
    # The role dependency returns 403 before looking up any resource.
    booking = get_booking(db, user, booking_id, lock=True)
    try:
        db.delete(booking)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Booking conflicts with database constraints") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to delete booking") from None
    return Response(status_code=204)
