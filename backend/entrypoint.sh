#!/bin/sh
# Run Alembic migrations before starting the server.
# All migrations are idempotent (check-before-apply), so this is safe to run
# against databases that were bootstrapped via SQLAlchemy's create_all as well
# as completely fresh databases.
set -e

echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

exec uvicorn main:app --host 0.0.0.0 --port 8000 --log-config logging.yaml
