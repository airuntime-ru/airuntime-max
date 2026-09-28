FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The MAX Bot API (platform-api2.max.ru) is signed by the Ministry of Digital Development's
# "Russian Trusted Sub CA", which ships in no default trust store. Without this the bot fails
# every call with CERTIFICATE_VERIFY_FAILED and silently never delivers a message.
# Provenance and fingerprints: infra/certs/README.md
COPY infra/certs/russian-trusted-ca.crt /usr/local/share/ca-certificates/russian-trusted-ca.crt
RUN update-ca-certificates
# ...and again at a path the application itself resolves: httpx verifies against certifi's
# bundle, so the system store update above reaches curl and openssl but never httpx.
COPY infra/certs/russian-trusted-ca.crt /app/certs/russian-trusted-ca.crt

COPY backend/requirements.txt backend/requirements.lock ./
# The lock pins transitive dependencies too, so a rebuild months later installs the tree
# that was tested rather than whatever is newest that day.
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock

COPY backend/alembic.ini ./alembic.ini
COPY backend/entrypoint.sh ./entrypoint.sh
COPY backend/src ./src
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
CMD ["sh", "/app/entrypoint.sh", "api"]
