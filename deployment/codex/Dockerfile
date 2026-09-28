# Image for the per-turn OpenAI Codex CLI runner container.
#
# The `worker` service (the only backend process with Docker socket access - see
# backend/src/services/docker_control_queue.py) launches a fresh container from this image for
# every coding-agent turn (`docker run`, see codex_worker.py), instead of the backend making raw
# HTTP calls to a model provider. Each run's container gets only that one project's own
# subdirectory bind-mounted at /workspace (not the whole shared projects tree) and either the raw
# Docker socket or a scoped docker-socket-proxy, so Codex can build/run Docker images through its
# own shell access without being able to reach other projects' files or containers.
#
# Auth: no key is baked into the image - the worker passes OPENAI_API_KEY per run (resolved the
# same way the old HTTP path did, via system_settings/config); the container logs in on startup
# (see codex_worker.py's _login_and_exec_command).
FROM node:20-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        docker.io \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @openai/codex

WORKDIR /workspace

# No fixed CMD - codex_worker.py always passes an explicit `sh -c '<login> && exec codex ...'`
# command per run; this container never idles waiting for a `docker exec`.
