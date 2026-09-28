#!/bin/sh
set -e

python - <<'PY'
import os
import time
import psycopg

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is required")
database_url = database_url.replace("+psycopg", "")

deadline = time.time() + 90
last_error = None
while time.time() < deadline:
    try:
        conn = psycopg.connect(database_url)
        conn.close()
        print("Database is ready")
        raise SystemExit(0)
    except Exception as exc:  # pragma: no cover
        last_error = exc
        time.sleep(2)

raise RuntimeError(f"Database is not ready: {last_error}")
PY

if [ "$1" = "worker" ]; then
  exec python -m src.workers.deployment_worker
fi

alembic -c alembic.ini upgrade head
exec uvicorn src.main:app --host 0.0.0.0 --port 8000
