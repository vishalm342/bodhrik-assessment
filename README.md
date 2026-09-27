# Bodhrik Service Booking & Review API

[![CI](https://github.com/vishalm342/bodhrik-assessment/actions/workflows/ci.yml/badge.svg)](https://github.com/vishalm342/bodhrik-assessment/actions/workflows/ci.yml)

FastAPI implementation of a small service booking and review platform for the **Bodhrik Full Stack Development Assessment**.

The service supports three user roles—`admin`, `provider`, and `customer`—with authenticated booking workflows, scoped access control, completed-booking reviews, Redis-backed review-summary jobs, PostgreSQL persistence, Docker Compose, migrations, tests, linting, and GitHub Actions CI.

> **Scope choice:** the assessment version stores the booked interval directly on `Booking` through `scheduled_start` and `scheduled_end`. A separate provider availability/slot entity is intentionally left as a production extension and is discussed in [DESIGN_NOTE.md](DESIGN_NOTE.md).

## Stack

| Area | Choice |
|---|---|
| API | FastAPI |
| Validation | Pydantic |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| Authentication | JWT + Argon2 password hashing |
| Queue | Redis 7 |
| Containers | Docker + Docker Compose |
| Tests | pytest + httpx |
| Lint | Ruff |
| CI | GitHub Actions |

## Architecture

```text
Client
  |
  v
FastAPI
  |-- JWT authentication
  |-- role-level authorization
  |-- resource ownership scoping
  |
  +------> PostgreSQL
  |          users
  |          bookings
  |          reviews
  |
  +------> Redis
             review_summary_jobs
```

Authorization is deliberately split into two layers:

1. **Role-level authorization** uses reusable FastAPI dependencies such as `require_roles(...)`.
2. **Resource-level authorization** scopes database queries by the authenticated user's identity. Providers only see bookings where they are the provider; customers only see bookings where they are the customer. Out-of-scope individual resources return the same `404` as missing resources.

## Core Domain

### Users

One `users` table stores shared identity and authentication data. The `role` column distinguishes:

- `admin`
- `provider`
- `customer`

Admins cannot self-register through the public registration endpoint.

### Bookings

A booking links one customer to one provider and stores:

- service name
- scheduled start/end
- lifecycle status: `pending`, `confirmed`, `completed`, `cancelled`

Database constraints ensure the provider and customer differ and that the end time is after the start time.

### Reviews

Only the customer who owns a **completed** booking may create its review. Each booking can have at most one review, and ratings are constrained to `1..5`.

### Review summarisation queue

`POST /reviews/{review_id}/summarize` does not call an LLM. It creates a job ID and pushes a JSON job into the Redis list `review_summary_jobs`, then returns `202 Accepted`.

Example queued job:

```json
{
  "job_id": "e43a052e-f738-4a51-b659-c5e4842b1810",
  "type": "summarize_review",
  "review_id": 42
}
```

A production worker would consume this queue and persist or return the resulting summary. The worker is intentionally outside the assessment scope because the brief permits an enqueued stub.

## API Endpoints

Interactive Swagger documentation is available at **http://localhost:8000/docs**.

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| GET | `/health` | Public | Health check |
| POST | `/auth/register` | Public | Register customer/provider |
| POST | `/auth/login` | Public | Login and receive bearer token |
| GET | `/auth/me` | Authenticated | Current user |
| POST | `/bookings` | Customer/Admin | Create booking |
| GET | `/bookings` | Authenticated | List visible bookings |
| GET | `/bookings/{booking_id}` | Authenticated | Read visible booking |
| PATCH | `/bookings/{booking_id}` | Authenticated | Update permitted fields/status |
| DELETE | `/bookings/{booking_id}` | Admin | Hard-delete booking |
| POST | `/reviews` | Customer | Review own completed booking |
| GET | `/reviews/{review_id}` | Authenticated | Read visible review |
| POST | `/reviews/{review_id}/summarize` | Authorized reader | Queue summary job |

### Booking visibility

| Role | Visible bookings |
|---|---|
| Admin | All |
| Provider | Bookings assigned to that provider |
| Customer | Their own bookings |

### Booking lifecycle

- Customers may cancel their active bookings.
- Providers may transition `pending -> confirmed/cancelled`.
- Providers may transition `confirmed -> completed/cancelled`.
- Completed or cancelled bookings are immutable for non-admin users.
- Admins have broader operational control.

## Run with Docker Compose

### 1. Clone

```bash
git clone https://github.com/vishalm342/bodhrik-assessment.git
cd bodhrik-assessment
```

### 2. Create environment file

```bash
cp .env.example .env
```

Generate a development JWT secret of at least 32 characters:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Place the generated value in `.env` as `JWT_SECRET_KEY`.

The committed `.env.example` contains local-only defaults. The real `.env` is ignored by Git and excluded from the Docker image.

### 3. Start the stack

```bash
docker compose up --build -d
docker compose ps
```

Compose starts:

- `api` — FastAPI on port `8000`
- `postgres` — PostgreSQL 16
- `redis` — Redis 7

The API waits for PostgreSQL and Redis health checks. On startup it runs:

```text
alembic upgrade head
```

and only then starts Uvicorn.

### 4. Verify

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok"}
```

Open:

```text
http://localhost:8000/docs
```

### 5. Stop

```bash
docker compose down
```

PostgreSQL data is retained in the named `postgres_data` volume. Use `docker compose down -v` only if you intentionally want to remove it.

## Local Development

With PostgreSQL and Redis available locally:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

For host-side development, the example database and Redis URLs use `localhost`. Inside Docker Compose, the API instead connects to the service hostnames `postgres` and `redis`.

## Tests and Lint

```bash
ruff check .
python -m pytest -q
alembic check
```

Current validated suite:

```text
174 passed
```

Tests cover authentication, JWT validation, booking CRUD, role restrictions, ownership isolation, lifecycle rules, reviews, duplicate review protection, Redis queue payloads, and queue failure handling.

## Continuous Integration

`.github/workflows/ci.yml` runs on pushes and pull requests.

The workflow:

1. checks out the repository,
2. configures Python 3.13,
3. starts a healthy PostgreSQL 16 service,
4. installs dependencies,
5. applies Alembic migrations,
6. runs `ruff check .`,
7. runs `python -m pytest -q`.

The CI database credentials are disposable test-only values. Redis is not required as a CI service because queue interactions are dependency-injected and mocked in the review tests.

## Project Structure

```text
app/
├── api/
│   ├── auth.py
│   ├── bookings.py
│   ├── dependencies.py
│   └── reviews.py
├── core/
│   ├── config.py
│   ├── redis.py
│   └── security.py
├── db/
├── models/
│   ├── booking.py
│   ├── review.py
│   └── user.py
└── schemas/

alembic/                  database migrations
tests/                    integration/API tests
.github/workflows/ci.yml  CI pipeline
Dockerfile                API image
docker-compose.yml        API + PostgreSQL + Redis
DESIGN_NOTE.md            architecture decisions and production gaps
```

## Design Decisions

The required 300–500 word assessment note is in **[DESIGN_NOTE.md](DESIGN_NOTE.md)**. It explains:

- schema shape and normalisation tradeoffs,
- how RBAC would evolve for a fourth role or nested organisations,
- production gaps around migrations, secrets, scheduling concurrency, jobs, authentication, and operations.
