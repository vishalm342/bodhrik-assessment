# Service Booking & Review API

Full Stack Development Assessment for the Bodhrik AI Fellow Program.

## Overview

This project implements the backend core of a service booking and review platform where providers offer services, customers create bookings, and completed bookings can be reviewed.

The system is being developed using FastAPI, PostgreSQL, Redis, Docker and GitHub Actions.

## Current Status

- [x] FastAPI application foundation
- [x] Health check endpoint
- [ ] PostgreSQL schema
- [ ] Authentication
- [ ] Role-based access control
- [ ] Booking CRUD
- [ ] Reviews
- [ ] Redis-backed summarisation workflow
- [x] Docker Compose
- [ ] Automated tests
- [x] GitHub Actions

## Running Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Docker Compose

Create `.env` from `.env.example` if it does not already exist. Set
`JWT_SECRET_KEY` to a random secret of at least 32 characters, for example with
`python -c "import secrets; print(secrets.token_hex(32))"`.

```bash
docker compose up --build -d
docker compose ps
docker compose logs api --tail=100
```

The API is available at http://localhost:8000, with `/health` and `/docs`.
The image uses Python 3.13 slim. Startup runs `alembic upgrade head` and starts
Uvicorn only if migrations succeed. PostgreSQL and Redis must pass their health
checks before the API starts; the API health check calls `/health`.

Compose explicitly sets the database host to `postgres` and Redis host to `redis`.
Host-side development and tests continue to use `localhost` from `.env`.
The developer `.env` is excluded from the image; Compose passes the JWT secret at
runtime. The PostgreSQL password defaults to `postgres` for this local assessment
and can be overridden using `POSTGRES_PASSWORD` (use URL-safe characters).
This setup is for local assessment use, not production deployment.
Changing that variable does not change the password of an already initialized
database; keep it consistent with the existing volume and host `DATABASE_URL`.

`docker compose down` retains PostgreSQL data in the named `postgres_data` volume.
Redis retains the existing ephemeral storage behavior.

## Lint and tests

With the virtual environment active and requirements installed:

```bash
alembic upgrade head
ruff check .
python -m pytest -q
alembic check
```

Ruff uses its default correctness rules with Python 3.13 as the target; local
virtual-environment folders are excluded. GitHub Actions runs on pushes and pull
requests, installs requirements, starts healthy PostgreSQL 16, applies migrations,
then runs Ruff and pytest. CI credentials are disposable test-only values.
Redis clients are injected in tests, so CI does not start Redis.
