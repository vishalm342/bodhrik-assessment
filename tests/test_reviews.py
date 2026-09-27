"""PostgreSQL integration tests with an injected Redis client; writes roll back."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import ConnectionError, TimeoutError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.core.security import create_access_token, hash_password
from app.db.session import engine, get_db
from app.main import app
from app.models.booking import Booking, BookingStatus
from app.models.review import Review
from app.models.user import User, UserRole


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                yield session
        finally:
            transaction.rollback()


@pytest.fixture
def queue():
    return Mock(spec=Redis, rpush=Mock(return_value=1))


@pytest.fixture
def client(db, queue):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: queue
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_redis, None)


@pytest.fixture(scope="module")
def password_hash():
    return hash_password("review-test-password")


@pytest.fixture
def actors(db, password_hash):
    users = {}
    for name, role in [
        ("admin", UserRole.ADMIN), ("c1", UserRole.CUSTOMER),
        ("c2", UserRole.CUSTOMER), ("p1", UserRole.PROVIDER), ("p2", UserRole.PROVIDER),
    ]:
        user = User(name=name, role=role, email=f"review-{uuid4().hex}@example.com",
                    password_hash=password_hash)
        db.add(user)
        users[name] = user
    db.commit()
    return users


def headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.fixture
def booking(db, actors):
    start = datetime(2030, 1, 1, 10, tzinfo=timezone.utc)
    booking = Booking(customer_id=actors["c1"].id, provider_id=actors["p1"].id,
                      service_name="Consultation", scheduled_start=start,
                      scheduled_end=start + timedelta(hours=1), status=BookingStatus.COMPLETED)
    db.add(booking)
    db.commit()
    return booking


@pytest.fixture
def review(client, actors, booking):
    response = client.post("/reviews", headers=headers(actors["c1"]),
                           json={"booking_id": booking.id, "rating": 5, "comment": "Excellent"})
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("method,path", [
    ("POST", "/reviews"), ("GET", "/reviews/1"), ("POST", "/reviews/1/summarize"),
])
@pytest.mark.parametrize("auth", [{}, {"Authorization": "Bearer invalid"}])
def test_authentication_required(client, queue, method, path, auth):
    response = client.request(method, path, headers=auth, json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    queue.rpush.assert_not_called()


@pytest.mark.parametrize("actor", ["p1", "admin"])
def test_only_customer_can_create(client, actors, booking, actor):
    response = client.post("/reviews", headers=headers(actors[actor]),
                           json={"booking_id": booking.id, "rating": 5})
    assert response.status_code == 403


@pytest.mark.parametrize("comment", [{}, {"comment": None}, {"comment": "Excellent service"}])
def test_create_completed_review(client, db, actors, booking, comment):
    response = client.post("/reviews", headers=headers(actors["c1"]),
                           json={"booking_id": booking.id, "rating": 1, **comment})
    assert response.status_code == 201
    result = response.json()
    assert set(result) == {"id", "booking_id", "rating", "comment", "created_at"}
    assert result["booking_id"] == booking.id
    assert result["rating"] == 1
    assert result["comment"] == comment.get("comment")
    stored = db.get(Review, result["id"])
    assert stored.booking_id == booking.id
    assert stored.rating == 1
    assert stored.comment == comment.get("comment")
    assert stored.created_at.tzinfo is not None


def test_foreign_and_missing_booking_indistinguishable(client, actors, booking):
    for booking_id in [booking.id, 2_147_483_647]:
        response = client.post("/reviews", headers=headers(actors["c2"]),
                               json={"booking_id": booking_id, "rating": 5})
        assert response.status_code == 404
        assert response.json() == {"detail": "Booking not found"}


@pytest.mark.parametrize("state", [BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.CANCELLED])
def test_incomplete_booking_rejected(client, db, actors, booking, state):
    booking.status = state
    db.commit()
    response = client.post("/reviews", headers=headers(actors["c1"]),
                           json={"booking_id": booking.id, "rating": 5})
    assert response.status_code == 409
    assert response.json() == {"detail": "Only completed bookings can be reviewed"}
    assert db.scalar(select(Review).where(Review.booking_id == booking.id)) is None


def test_duplicate_rejected(client, db, actors, review):
    response = client.post("/reviews", headers=headers(actors["c1"]),
                           json={"booking_id": review["booking_id"], "rating": 1})
    assert response.status_code == 409
    assert response.json() == {"detail": "Booking already has a review"}
    assert db.get(Review, review["id"]).rating == 5


@pytest.mark.parametrize("rating", [0, 6, -1, 1.5, True, "5", None])
def test_invalid_rating(client, db, actors, booking, rating):
    response = client.post("/reviews", headers=headers(actors["c1"]),
                           json={"booking_id": booking.id, "rating": rating})
    assert response.status_code == 422
    assert db.scalar(select(Review).where(Review.booking_id == booking.id)) is None


def test_customer_id_payload_rejected(client, actors, booking):
    response = client.post("/reviews", headers=headers(actors["c1"]),
                           json={"booking_id": booking.id, "rating": 5, "customer_id": actors["c2"].id})
    assert response.status_code == 422


@pytest.mark.parametrize("actor", ["c1", "p1", "admin"])
def test_authorized_read(client, actors, review, actor):
    response = client.get(f'/reviews/{review["id"]}', headers=headers(actors[actor]))
    assert response.status_code == 200
    assert response.json() == review


@pytest.mark.parametrize("actor", ["c2", "p2"])
@pytest.mark.parametrize("summarize", [False, True])
def test_inaccessible_and_missing_review_not_found(client, queue, actors, review, actor, summarize):
    for review_id in [review["id"], 2_147_483_647]:
        path = f"/reviews/{review_id}" + ("/summarize" if summarize else "")
        response = client.request("POST" if summarize else "GET", path, headers=headers(actors[actor]))
        assert response.status_code == 404
        assert response.json() == {"detail": "Review not found"}
    queue.rpush.assert_not_called()


@pytest.mark.parametrize("actor", ["c1", "p1", "admin"])
def test_summary_enqueued(client, queue, actors, review, actor):
    response = client.post(f'/reviews/{review["id"]}/summarize', headers=headers(actors[actor]))
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"job_id", "status"}
    assert body["status"] == "queued"
    assert UUID(body["job_id"]).version == 4
    queue.rpush.assert_called_once()
    key, payload = queue.rpush.call_args.args
    assert key == "review_summary_jobs"
    assert isinstance(payload, str)
    assert json.loads(payload) == {
        "job_id": body["job_id"], "type": "summarize_review", "review_id": review["id"],
    }


@pytest.mark.parametrize("error", [ConnectionError, TimeoutError])
def test_queue_failure(client, queue, actors, review, error):
    queue.rpush.side_effect = error("private connection details")
    response = client.post(f'/reviews/{review["id"]}/summarize', headers=headers(actors["c1"]))
    assert response.status_code == 503
    assert response.json() == {"detail": "Review summary queue is unavailable"}
    queue.rpush.assert_called_once()


def test_database_failure_rolls_back(client, db, actors, booking, monkeypatch):
    body = {"booking_id": booking.id, "rating": 5}
    auth = headers(actors["c1"])

    def fail():
        db.flush()
        raise SQLAlchemyError("private database details")

    with monkeypatch.context() as patch:
        patch.setattr(db, "commit", fail)
        response = client.post("/reviews", headers=auth, json=body)
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to save review"}
    assert db.scalar(select(Review).where(Review.booking_id == body["booking_id"])) is None
    assert client.post("/reviews", headers=auth, json=body).status_code == 201


def test_unique_constraint_rolls_back(client, db, actors, review, monkeypatch):
    original_scalar = db.scalar

    def hide_duplicate(statement, *args, **kwargs):
        # Bypass only the precheck to exercise the real PostgreSQL unique constraint.
        if statement.column_descriptions[0]["expr"] is Review.id:
            return None
        return original_scalar(statement, *args, **kwargs)

    auth = headers(actors["c1"])
    with monkeypatch.context() as patch:
        patch.setattr(db, "scalar", hide_duplicate)
        response = client.post("/reviews", headers=auth,
                               json={"booking_id": review["booking_id"], "rating": 1})
    assert response.status_code == 409
    assert response.json() == {"detail": "Review conflicts with database constraints"}
    assert client.get(f'/reviews/{review["id"]}', headers=auth).json() == review
