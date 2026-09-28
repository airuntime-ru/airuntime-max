# Image for the per-turn browser-preview runner container.
#
# The `worker` service (the only backend process with Docker socket access - see
# backend/src/services/docker_control_queue.py) launches a fresh container from this image for
# every preview_project tool call (see backend/src/services/preview_runner.py), the same
# "dedicated image, one job per container, always removed after" shape deployment/codex/Dockerfile
# already uses - kept out of backend/Dockerfile (shared by `backend` and `worker`) so neither of
# those carries Chromium's weight.
#
# Base image ships Python + Playwright + Chromium + all OS-level browser deps preinstalled and
# version-matched - avoids hand-rolling apt installs for headless Chromium's large dependency list.
# Bump the tag deliberately (not ":latest") when upgrading; this is a build-only/opt-in image
# (`docker compose build preview`), so a stale pin only affects preview quality, never other
# services.
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app
RUN pip install --no-cache-dir "playwright==1.48.0"
COPY deployment/preview/run_preview.py ./run_preview.py

# No fixed CMD args - preview_runner.py always passes explicit env vars (PREVIEW_TARGET_HOST/
# PREVIEW_PATHS/PREVIEW_OUTPUT_DIR/...) per run; this container never idles waiting for input.
ENTRYPOINT ["python3", "/app/run_preview.py"]
