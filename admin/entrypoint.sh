#!/bin/sh
set -e

python <<'PY'
import os
import time
import psycopg

for _ in range(90):
    try:
        with psycopg.connect(
            dbname=os.environ.get("POSTGRES_DB", "airuntime"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
        ):
            break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("Database not ready")
PY

python manage.py migrate --noinput
python manage.py ensure_superuser

exec gunicorn airuntime_admin.wsgi:application --bind 0.0.0.0:8001 --workers 2 --timeout 120
