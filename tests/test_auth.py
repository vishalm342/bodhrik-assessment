"""Integration tests against configured PostgreSQL; each test rolls back its writes.

Run after starting Compose and applying the existing migration:
    python -m pytest -q
No tables are created/dropped. PostgreSQL sequences may advance.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, verify_password
from app.db.session import engine, get_db
from app.main import app
from app.models.user import User

PASSWORD = "assessment-test-password"
PUBLIC_FIELDS = {"id", "name", "email", "role", "created_at"}


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
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


@pytest.fixture
def payload():
    return {"name": "Test User", "email": f"auth-{uuid4().hex}@example.com",
            "password": PASSWORD, "role": "customer"}


def register(client, payload):
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    assert set(response.json()) == PUBLIC_FIELDS
    return response.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def assert_unauthorized(response):
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def signed(claims, algorithm="HS256"):
    return jwt.encode(claims, settings.jwt_secret_key.get_secret_value(), algorithm=algorithm)


def test_health_docs_and_oauth(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert {"/auth/register", "/auth/login", "/auth/me"} <= schema["paths"].keys()
    flow = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]["flows"]
    assert flow["password"]["tokenUrl"] == "/auth/login"
    assert schema["paths"]["/auth/me"]["get"]["security"]


@pytest.mark.parametrize("role", ["customer", "provider"])
def test_register_and_storage(client, db, payload, role):
    payload["role"] = role
    payload["email"] = "  " + payload["email"].upper() + "  "
    user = register(client, payload)
    assert user["role"] == role
    assert user["email"] == payload["email"].strip().lower()
    stored = db.get(User, user["id"])
    assert stored.password_hash.startswith("$argon2id$")
    assert stored.password_hash != PASSWORD
    assert verify_password(PASSWORD, stored.password_hash)
    assert not verify_password("wrong-password", stored.password_hash)


def test_duplicate_email(client, payload):
    register(client, payload)
    payload["email"] = " " + payload["email"].upper() + " "
    assert client.post("/auth/register", json=payload).status_code == 409
    assert client.get("/health").status_code == 200


def test_admin_registration_rejected(client, db, payload):
    payload["role"] = "admin"
    assert client.post("/auth/register", json=payload).status_code == 403
    assert db.scalar(select(User).where(User.email == payload["email"])) is None


@pytest.mark.parametrize("field,value", [
    ("password", "short"), ("password", ""), ("email", "invalid"),
    ("email", "a@b"), ("name", "   "), ("role", "owner"),
])
def test_invalid_registration(client, payload, field, value):
    payload[field] = value
    assert client.post("/auth/register", json=payload).status_code == 422


def test_login_and_me(client, payload):
    user = register(client, payload)
    response = client.post("/auth/login", data={
        "username": " " + payload["email"].upper() + " ", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type"}
    assert body["token_type"] == "bearer"
    claims = decode_access_token(body["access_token"])
    assert set(claims) == {"sub", "exp"}
    assert claims["sub"] == str(user["id"])
    assert 0 < claims["exp"] - datetime.now(timezone.utc).timestamp() <= settings.access_token_expire_minutes * 60
    response = client.get("/auth/me", headers=bearer(body["access_token"]))
    assert response.status_code == 200
    assert response.json() == user


def test_credentials_are_generic(client, payload):
    register(client, payload)
    wrong = client.post("/auth/login", data={"username": payload["email"], "password": "wrong"})
    unknown = client.post("/auth/login", data={"username": f"missing-{uuid4().hex}@example.com", "password": PASSWORD})
    assert_unauthorized(wrong)
    assert_unauthorized(unknown)
    assert wrong.json() == unknown.json()


@pytest.mark.parametrize("headers", [{}, bearer("garbage"), {"X-User-Id": "1", "X-Role": "admin"}, {"Authorization": "Basic abc"}])
def test_missing_or_malformed_auth(client, headers):
    assert_unauthorized(client.get("/auth/me", headers=headers))


def test_tampered_and_wrong_signature(client, payload):
    user = register(client, payload)
    token = create_access_token(str(user["id"]))
    head, body, signature = token.split(".")
    signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    assert_unauthorized(client.get("/auth/me", headers=bearer(f"{head}.{body}.{signature}")))
    wrong_key = jwt.encode(decode_access_token(token), "different-test-key-of-at-least-32-characters", algorithm="HS256")
    assert_unauthorized(client.get("/auth/me", headers=bearer(wrong_key)))


@pytest.mark.parametrize("subject", [None, "", "abc", "0", "-1", "1.2", "2147483648", "9" * 100, 1, True])
def test_invalid_subject(client, subject):
    token = signed({"sub": subject, "exp": datetime.now(timezone.utc) + timedelta(minutes=1)})
    assert_unauthorized(client.get("/auth/me", headers=bearer(token)))


def test_missing_claims_expiration_and_algorithm(client, payload):
    user = register(client, payload)
    subject = str(user["id"])
    expired = signed({"sub": subject, "exp": datetime.now(timezone.utc) - timedelta(seconds=1)})
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    for token in [expired, signed({"sub": subject}), signed({"exp": future}),
                  signed({"sub": subject, "exp": future}, algorithm="HS384")]:
        assert_unauthorized(client.get("/auth/me", headers=bearer(token)))


def test_deleted_user(client, db, payload):
    user = register(client, payload)
    token = create_access_token(str(user["id"]))
    db.delete(db.get(User, user["id"]))
    db.commit()
    assert_unauthorized(client.get("/auth/me", headers=bearer(token)))


def test_failed_commit_rolls_back(client, db, payload, monkeypatch):
    original_commit = db.commit
    def fail():
        db.flush()
        raise SQLAlchemyError("simulated database failure")
    monkeypatch.setattr(db, "commit", fail)
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to register user"}
    monkeypatch.setattr(db, "commit", original_commit)
    assert db.scalar(select(User).where(User.email == payload["email"])) is None
    register(client, payload)



def test_duplicate_constraint_rolls_back(client, db, payload, monkeypatch):
    register(client, payload)
    # Simulate another request winning after the initial duplicate lookup.
    with monkeypatch.context() as patch:
        patch.setattr(db, "scalar", lambda statement: None)
        response = client.post("/auth/register", json=payload)
    assert response.status_code == 409
    payload["email"] = f"auth-{uuid4().hex}@example.com"
    register(client, payload)
