import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.bookings import get_booking, scoped_bookings
from app.api.dependencies import DatabaseSession, require_roles
from app.core.redis import REVIEW_SUMMARY_QUEUE, get_redis
from app.models.booking import Booking, BookingStatus
from app.models.review import Review
from app.models.user import User, UserRole
from app.schemas.review import ReviewCreate, ReviewPublic, SummaryJobResponse

router = APIRouter(prefix="/reviews", tags=["Reviews"])
ReviewCreator = Annotated[User, Depends(require_roles(UserRole.CUSTOMER))]
ReviewReader = Annotated[
    User, Depends(require_roles(UserRole.ADMIN, UserRole.CUSTOMER, UserRole.PROVIDER))
]
ReviewId = Annotated[int, Path(gt=0, le=2_147_483_647)]
RedisClient = Annotated[Redis, Depends(get_redis)]


def get_review(db: Session, user: User, review_id: int) -> Review:
    visible_booking_ids = scoped_bookings(user).with_only_columns(Booking.id)
    review = db.scalar(select(Review).where(
        Review.id == review_id, Review.booking_id.in_(visible_booking_ids)
    ))
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


@router.post("", response_model=ReviewPublic, status_code=201)
def create_review(payload: ReviewCreate, db: DatabaseSession, user: ReviewCreator) -> Review:
    try:
        booking = get_booking(db, user, payload.booking_id, lock=True)
        if booking.status != BookingStatus.COMPLETED:
            raise HTTPException(status_code=409, detail="Only completed bookings can be reviewed")
        if db.scalar(select(Review.id).where(Review.booking_id == booking.id)) is not None:
            raise HTTPException(status_code=409, detail="Booking already has a review")
        review = Review(booking_id=booking.id, rating=payload.rating, comment=payload.comment)
        db.add(review)
        db.commit()
        db.refresh(review)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Review conflicts with database constraints") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to save review") from None
    return review


@router.get("/{review_id}", response_model=ReviewPublic)
def read_review(review_id: ReviewId, db: DatabaseSession, user: ReviewReader) -> Review:
    return get_review(db, user, review_id)


@router.post("/{review_id}/summarize", response_model=SummaryJobResponse, status_code=202)
def summarize_review(
    review_id: ReviewId, db: DatabaseSession, user: ReviewReader, redis: RedisClient
) -> SummaryJobResponse:
    review = get_review(db, user, review_id)
    job_id = uuid4()
    job = {"job_id": str(job_id), "type": "summarize_review", "review_id": review.id}
    try:
        redis.rpush(REVIEW_SUMMARY_QUEUE, json.dumps(job))
    except RedisError:
        raise HTTPException(status_code=503, detail="Review summary queue is unavailable") from None
    return SummaryJobResponse(job_id=job_id)
