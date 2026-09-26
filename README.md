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
- [ ] Docker Compose
- [ ] Automated tests
- [ ] GitHub Actions

## Running Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload