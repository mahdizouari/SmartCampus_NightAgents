#!/bin/sh
# =============================================================================
# SmartCampus AI — Backend Entrypoint (SA-18)
#
# Automatically applies Alembic migrations before starting the server.
# This ensures every developer gets a fully migrated DB on first `docker compose up`.
# =============================================================================

set -e

echo "[entrypoint] Waiting for PostgreSQL to be ready..."
# The 'depends_on: postgres: condition: service_healthy' in docker-compose.yml
# already guarantees postgres is healthy before this script runs.

echo "[entrypoint] Running Alembic migrations..."
python -m alembic upgrade head

echo "[entrypoint] Migrations applied. Starting FastAPI server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
