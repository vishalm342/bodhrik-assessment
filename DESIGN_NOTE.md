# Design Note

## Schema and normalisation decisions

I kept the database around the three main entities required in the assessment: `users`, `bookings`, and `reviews`. I used one `users` table for admins, providers, and customers because most of their identity and authentication data is the same. A `role` field decides how the user can interact with the system instead of maintaining three separate tables with duplicated fields.

A booking connects two users in different roles, so `provider_id` and `customer_id` both reference the users table. Reviews are kept separately because they only exist after a booking is completed and have their own data and lifecycle. I also made `booking_id` unique on reviews so a booking cannot receive multiple reviews.

I considered adding a separate availability or time-slot table because providers offering slots is part of the domain. For this assessment, I kept `scheduled_start` and `scheduled_end` directly on the booking. The required schema focused on Users, Bookings, and Reviews, and I wanted to keep the implementation small rather than introduce another scheduling subsystem. In a production version, I would separate provider availability/time slots and add stronger conflict checks to prevent overlapping or double bookings.

## RBAC and future roles

I kept authorization in two parts. Reusable FastAPI dependencies decide whether a user's role can perform an action, while the booking and review queries decide which records that specific user can access. This is why a provider cannot access another provider's bookings and a customer only sees their own.

Adding one simple fourth role would mainly mean extending the role enum and the centralized authorization rules. For nested organisations, however, one global role would no longer be enough. I would introduce `organizations` and a membership table connecting users to organisations with organisation-specific roles or permissions.

## What I would change for production

For this assessment, Alembic migrations run before the API starts because it makes the Docker setup easy to reproduce. With multiple production replicas, I would instead run migrations once as a controlled deployment step.

Production secrets would come from a secret manager or protected runtime environment rather than committed configuration. I would also add HTTPS, rate limiting, structured logging and monitoring, database backups, stronger JWT lifecycle/revocation handling, and connection-pool tuning.

The Redis summarisation flow currently stops after queueing the job, as allowed by the assessment. A production version would add a worker with retries, idempotent processing, failure/dead-letter handling, monitoring, and appropriate Redis persistence. 