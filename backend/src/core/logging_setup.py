"""Console logging for the API and worker processes.

Both processes run under Docker with the default json-file log driver, so anything sent to
stdout/stderr is already captured by `docker compose logs` - there was just nothing calling
`logging` anywhere in the app to put a line there (confirmed by grep before this was added: the
only two callers of `logging.getLogger` in the whole backend were an Alembic migration script and
cloudflare_dns.py). Call configure_logging() once, as early as possible, from each process
entrypoint (src/main.py for the API, src/workers/deployment_worker.py for the worker).
"""

from __future__ import annotations

import logging
import re

from src.core.config import settings

_CONFIGURED = False

# MAX cannot send a custom header with its webhook deliveries, so the shared secret has to
# live in the path - and uvicorn's access logger would then print it on every single
# delivery. Rewrite it out of the record before any handler sees it.
# A lookbehind keeps the readable prefix in place without a backreference in the
# replacement, which is easy to get subtly wrong.
_SECRET_IN_PATH = re.compile(r"(?<=/max/webhook/)[^/\s\"']+")


class RedactWebhookSecret(logging.Filter):
    """Replaces the MAX webhook secret with a placeholder in any log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                _SECRET_IN_PATH.sub("<secret>", arg) if isinstance(arg, str) else arg
                for arg in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        if isinstance(record.msg, str):
            record.msg = _SECRET_IN_PATH.sub("<secret>", record.msg)
        return True


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # uvicorn installs its own handlers on these loggers before app code runs; only bump the
    # level so LOG_LEVEL=DEBUG actually shows uvicorn's own request-cycle detail too.
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)

    redact = RedactWebhookSecret()
    # Attached to the loggers that carry request paths, plus the root, so the secret cannot
    # reach a handler from an application log line either.
    for name in ("uvicorn.access", "uvicorn.error", ""):
        logging.getLogger(name).addFilter(redact)
    _CONFIGURED = True
