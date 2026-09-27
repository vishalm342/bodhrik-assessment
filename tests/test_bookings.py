"""PostgreSQL integration tests; all records roll back, sequences may advance.

Only get_db is overridden to bind a rollback transaction. JWT decoding, current
user lookup, ownership queries, role checks and database constraints run normally.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.db.session import engine, get_db
from app.main import app
from app.models.booking import Booking, BookingStatus
from app.models.review import Review
from app.models.user import User, UserRole

START = datetime(2030, 1, 1, 10, tzinfo=timezone.utc)
PUBLIC_FIELDS = {
    "id", "provider_id", "customer_id", "service_name", "scheduled_start",
    "scheduled_end", "status", "created_at", "updated_at",
}


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
def client(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="module")
def password_hash():
    return hash_password("booking-test-password")


@pytest.fixture
def actors(db, password_hash):
    users = {}
    for name, role in [
        ("admin", UserRole.ADMIN), ("c1", UserRole.CUSTOMER),
        ("c2", UserRole.CUSTOMER), ("p1", UserRole.PROVIDER), ("p2", UserRole.PROVIDER),
    ]:
        user = User(name=name, role=role, email=f"booking-{uuid4().hex}@example.com",
                    password_hash=password_hash)
        db.add(user)
        users[name] = user
    db.commit()
    return users


def headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.fixture
def payload(actors):
    return {
        "provider_id": actors["p1"].id,
        "service_name": "  Consultation  ",
        "scheduled_start": START.isoformat(),
        "scheduled_end": (START + timedelta(hours=1)).isoformat(),
    }


@pytest.fixture
def bookings(client, actors, payload):
    result = []
    for customer, provider in [("c1", "p1"), ("c1", "p2"), ("c2", "p1"), ("c2", "p2")]:
        response = client.post("/bookings", headers=headers(actors[customer]),
                               json={**payload, "provider_id": actors[provider].id})
        assert response.status_code == 201
        result.append(response.json())
    return result


@pytest.mark.parametrize("method,path", [
    ("POST", "/bookings"), ("GET", "/bookings"), ("GET", "/bookings/1"),
    ("PATCH", "/bookings/1"), ("DELETE", "/bookings/1"),
])
@pytest.mark.parametrize("auth", [{}, {"Authorization": "Bearer invalid"}])
def test_authentication_required(client, method, path, auth):
    response = client.request(method, path, headers=auth, json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_customer_create_uses_authenticated_identity(client, db, actors, payload):
    auth = {**headers(actors["c1"]), "X-User-Id": str(actors["c2"].id), "X-Role": "admin"}
    response = client.post("/bookings", headers=auth, json=payload)
    assert response.status_code == 201
    result = response.json()
    assert set(result) == PUBLIC_FIELDS
    assert result["customer_id"] == actors["c1"].id
    assert result["provider_id"] == actors["p1"].id
    assert result["status"] == "pending"
    assert result["service_name"] == "Consultation"
    stored = db.get(Booking, result["id"])
    assert stored.customer_id == actors["c1"].id
    assert stored.scheduled_start.tzinfo is not None


@pytest.mark.parametrize("customer", ["c1", "c2", None])
def test_customer_cannot_supply_customer_id(client, actors, payload, customer):
    payload["customer_id"] = actors[customer].id if customer else None
    response = client.post("/bookings", headers=headers(actors["c1"]), json=payload)
    assert response.status_code == 403


@pytest.mark.parametrize("actor", ["customer", "admin"])
def test_create_cannot_choose_initial_status(client, actors, payload, actor):
    if actor == "admin":
        payload["customer_id"] = actors["c1"].id
    payload["status"] = "completed"
    user = actors["c1" if actor == "customer" else "admin"]
    assert client.post("/bookings", headers=headers(user), json=payload).status_code == 422


@pytest.mark.parametrize("target", ["c1", "admin", None])
def test_invalid_provider(client, actors, payload, target):
    payload["provider_id"] = actors[target].id if target else 2_147_483_647
    assert client.post("/bookings", headers=headers(actors["c1"]), json=payload).status_code == 422


def test_provider_cannot_create(client, actors, payload):
    assert client.post("/bookings", headers=headers(actors["p1"]), json=payload).status_code == 403


def test_admin_create(client, actors, payload):
    payload["customer_id"] = actors["c2"].id
    response = client.post("/bookings", headers=headers(actors["admin"]), json=payload)
    assert response.status_code == 201
    assert response.json()["customer_id"] == actors["c2"].id
    assert response.json()["status"] == "pending"


@pytest.mark.parametrize("target", ["p1", "admin", "missing", None])
def test_admin_requires_valid_customer(client, actors, payload, target):
    if target:
        payload["customer_id"] = actors[target].id if target != "missing" else 2_147_483_647
    assert client.post("/bookings", headers=headers(actors["admin"]), json=payload).status_code == 422


@pytest.mark.parametrize("change", [
    {"service_name": "  "}, {"service_name": "x" * 201},
    {"scheduled_end": START.isoformat()},
    {"scheduled_end": (START - timedelta(hours=1)).isoformat()},
    {"scheduled_start": "2030-01-01T10:00:00"},
    {"scheduled_end": "2030-01-01T11:00:00"},
    {"provider_id": 0}, {"provider_id": 2_147_483_648},
])
def test_create_validation(client, actors, payload, change):
    assert client.post("/bookings", headers=headers(actors["c1"]),
                       json={**payload, **change}).status_code == 422


@pytest.mark.parametrize("actor", ["admin", "c1", "c2", "p1", "p2"])
def test_list_scopes_and_order(client, db, actors, bookings, actor):
    user = actors[actor]
    response = client.get("/bookings", headers=headers(user))
    assert response.status_code == 200
    rows = response.json()
    ids = [row["id"] for row in rows]
    assert ids == sorted(ids)
    if actor == "admin":
        assert ids == list(db.scalars(select(Booking.id).order_by(Booking.id)))
    else:
        field = "customer_id" if actor.startswith("c") else "provider_id"
        assert all(row[field] == user.id for row in rows)
        assert set(ids) == {row["id"] for row in bookings if row[field] == user.id}
    assert all(set(row) == PUBLIC_FIELDS for row in rows)


@pytest.mark.parametrize("actor", ["admin", "c1", "c2", "p1", "p2"])
def test_get_scope_and_missing_are_indistinguishable(client, actors, bookings, actor):
    user = actors[actor]
    missing = client.get("/bookings/2147483647", headers=headers(user))
    assert missing.status_code == 404
    for booking in bookings:
        response = client.get(f'/bookings/{booking["id"]}', headers=headers(user))
        allowed = actor == "admin" or user.id in (booking["customer_id"], booking["provider_id"])
        if allowed:
            assert response.status_code == 200
            assert response.json() == booking
        else:
            assert response.status_code == 404
            assert response.json() == missing.json() == {"detail": "Booking not found"}


@pytest.mark.parametrize("actor", ["c1", "p1", "admin"])
def test_active_fields_update(client, actors, bookings, actor):
    response = client.patch(f'/bookings/{bookings[0]["id"]}', headers=headers(actors[actor]), json={
        "service_name": "  Updated service  ",
        "scheduled_start": (START - timedelta(minutes=15)).isoformat(),
    })
    assert response.status_code == 200
    assert response.json()["service_name"] == "Updated service"
    assert response.json()["scheduled_end"] == bookings[0]["scheduled_end"]
    assert response.json()["customer_id"] == bookings[0]["customer_id"]


@pytest.mark.parametrize("actor", ["c2", "p2"])
def test_cannot_patch_another_users_booking(client, actors, bookings, actor):
    response = client.patch(f'/bookings/{bookings[0]["id"]}', headers=headers(actors[actor]),
                            json={"service_name": "Unauthorized"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Booking not found"}
    assert client.get(f'/bookings/{bookings[0]["id"]}', headers=headers(actors["admin"])).json() == bookings[0]


@pytest.mark.parametrize("status", ["pending", "confirmed", "completed"])
def test_customer_cannot_set_other_states(client, actors, bookings, status):
    response = client.patch(f'/bookings/{bookings[0]["id"]}', headers=headers(actors["c1"]),
                            json={"status": status})
    assert response.status_code == 403


def test_customer_cancel(client, actors, bookings):
    response = client.patch(f'/bookings/{bookings[0]["id"]}', headers=headers(actors["c1"]),
                            json={"status": "cancelled"})
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.parametrize("initial,target,expected", [
    ("pending", "confirmed", 200), ("pending", "cancelled", 200),
    ("pending", "completed", 409), ("pending", "pending", 409),
    ("confirmed", "completed", 200), ("confirmed", "cancelled", 200),
    ("confirmed", "pending", 409), ("confirmed", "confirmed", 409),
])
def test_provider_lifecycle(client, db, actors, bookings, initial, target, expected):
    booking = db.get(Booking, bookings[0]["id"])
    booking.status = BookingStatus(initial)
    db.commit()
    response = client.patch(f"/bookings/{booking.id}", headers=headers(actors["p1"]), json={"status": target})
    assert response.status_code == expected
    db.refresh(booking)
    assert booking.status.value == (target if expected == 200 else initial)


@pytest.mark.parametrize("actor", ["c1", "p1"])
@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
@pytest.mark.parametrize("change", [{"service_name": "Edit"}, {"status": "cancelled"}, {"status": "confirmed"}])
def test_terminal_bookings_locked(client, db, actors, bookings, actor, terminal, change):
    booking = db.get(Booking, bookings[0]["id"])
    booking.status = BookingStatus(terminal)
    db.commit()
    assert client.patch(f"/bookings/{booking.id}", headers=headers(actors[actor]), json=change).status_code == 409
    db.refresh(booking)
    assert booking.status.value == terminal
    assert booking.service_name == "Consultation"


@pytest.mark.parametrize("target", [status.value for status in BookingStatus])
def test_admin_can_edit_terminal_and_set_any_status(client, db, actors, bookings, target):
    booking = db.get(Booking, bookings[3]["id"])
    booking.status = BookingStatus.COMPLETED
    db.commit()
    response = client.patch(f"/bookings/{booking.id}", headers=headers(actors["admin"]),
                            json={"status": target, "service_name": "Admin correction"})
    assert response.status_code == 200
    assert response.json()["status"] == target
    assert response.json()["service_name"] == "Admin correction"


@pytest.mark.parametrize("change", [
    {"scheduled_end": START.isoformat()},
    {"scheduled_start": (START + timedelta(hours=2)).isoformat()},
    {"scheduled_start": "2030-01-01T10:00:00"},
    {"scheduled_end": "2030-01-01T11:00:00"},
    {"scheduled_start": "2030-01-01T12:00:00Z", "scheduled_end": "2030-01-01T11:00:00Z"},
    {"service_name": " "}, {"service_name": "x" * 201},
    {"service_name": None}, {"scheduled_start": None}, {"scheduled_end": None},
    {"status": None}, {"status": "invalid"}, {},
])
def test_invalid_patch_preserves_booking(client, actors, bookings, change):
    path = f'/bookings/{bookings[0]["id"]}'
    assert client.patch(path, headers=headers(actors["admin"]), json=change).status_code == 422
    assert client.get(path, headers=headers(actors["admin"])).json() == bookings[0]


@pytest.mark.parametrize("field", ["id", "customer_id", "provider_id", "created_at", "updated_at"])
def test_immutable_fields_rejected_even_for_admin(client, actors, bookings, field):
    path = f'/bookings/{bookings[0]["id"]}'
    assert client.patch(path, headers=headers(actors["admin"]), json={field: 1}).status_code == 422
    assert client.get(path, headers=headers(actors["admin"])).json() == bookings[0]


def test_timezone_offsets_and_combined_schedule(client, actors, payload):
    payload.update(scheduled_start="2030-01-01T10:00:00+05:30", scheduled_end="2030-01-01T05:30:00Z")
    response = client.post("/bookings", headers=headers(actors["c1"]), json=payload)
    assert response.status_code == 201
    path = f'/bookings/{response.json()["id"]}'
    response = client.patch(path, headers=headers(actors["c1"]), json={
        "scheduled_start": "2030-01-01T12:00:00Z", "scheduled_end": "2030-01-01T13:00:00Z"})
    assert response.status_code == 200


@pytest.mark.parametrize("actor", ["c1", "c2", "p1", "p2"])
def test_non_admin_delete_always_forbidden(client, actors, bookings, actor):
    for booking_id in [bookings[0]["id"], 2_147_483_647]:
        assert client.delete(f"/bookings/{booking_id}", headers=headers(actors[actor])).status_code == 403


def test_admin_delete_preserves_existing_review_cascade(client, db, actors, bookings):
    booking_id = bookings[0]["id"]
    review = Review(booking_id=booking_id, rating=5)
    db.add(review)
    db.commit()
    review_id = review.id
    response = client.delete(f"/bookings/{booking_id}", headers=headers(actors["admin"]))
    assert response.status_code == 204
    assert response.content == b""
    assert db.get(Booking, booking_id) is None
    assert db.get(Review, review_id) is None
    assert client.delete(f"/bookings/{booking_id}", headers=headers(actors["admin"])).status_code == 404


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_write_failure_rolls_back(client, db, actors, payload, bookings, monkeypatch, method):
    path = "/bookings" if method == "POST" else f'/bookings/{bookings[0]["id"]}'
    body = {**payload, "customer_id": actors["c1"].id} if method == "POST" else {"service_name": "Changed"}
    before = client.get("/bookings", headers=headers(actors["admin"])).json()
    def fail():
        db.flush()
        raise SQLAlchemyError("simulated write failure")
    with monkeypatch.context() as patch:
        patch.setattr(db, "commit", fail)
        response = client.request(method, path, headers=headers(actors["admin"]), json=body)
    assert response.status_code == 500
    assert "simulated" not in response.text
    assert client.get("/bookings", headers=headers(actors["admin"])).json() == before
    assert client.request(method, path, headers=headers(actors["admin"]), json=body).status_code in (200, 201, 204)


def test_docs_expose_protected_booking_operations(client):
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    for path, methods in [("/bookings", ["get", "post"]), ("/bookings/{booking_id}", ["get", "patch", "delete"])]:
        for method in methods:
            assert paths[path][method]["security"] == [{"OAuth2PasswordBearer": []}]
