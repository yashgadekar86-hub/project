-- Initial Postgres setup for AI Forex Command Center
-- Schema is managed via Alembic migrations; this file creates extensions.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
