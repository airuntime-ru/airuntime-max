import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.core.logging_setup import configure_logging

configure_logging()

from src.api.dependencies.rate_limit import enforce_rate_limit  # noqa: E402
from src.api.routers import (  # noqa: E402
    analytics,
    auth,
    billing,
    byok,
    chat,
    deployments,
    files,
    orchestration,
    orchestration_admin,
    project_versions,
    projects,
    providers,
    secrets,
    support,
    support_staff,
    telegram,
)

# Aliased: a bare `max` import would shadow the builtin for the whole module.
from src.api.routers import max as max_router  # noqa: E402
from src.core.config import settings  # noqa: E402
from src.services.orchestration import engine as orchestration_engine  # noqa: E402
from src.services.orchestration.mcp import registry as mcp_registry  # noqa: E402
from src.services.storage import storage_service  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("AIRuntime API starting (environment=%s)", settings.environment)
    storage_service.ensure_bucket()
    # Restart recovery: orchestration runs are durable DB rows, but a run that was mid-flight
    # when this process died has nothing driving it any more. Relaunch those here (runs parked
    # on the user - waiting_for_user - are deliberately excluded; see list_resumable). Runs on
    # this process's event loop, which is why it lives in lifespan rather than at import time.
    orchestration_engine.recover_stranded_runs()
    yield
    # Terminates any cached MCP stdio/http clients (registry.py's get_client cache) - a no-op
    # if no task this process's lifetime ever reached an MCP capability call, otherwise avoids
    # leaking subprocess/connection handles.
    await mcp_registry.close_all_clients()


app = FastAPI(
    title=settings.app_name,
    openapi_url=f"{settings.api_prefix}/openapi.json",
    dependencies=[Depends(enforce_rate_limit)],
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    # X-Max-Init-Data is not a simple header, so a missing allow turns the mini app's
    # preflight into a 400. The webview then reports a network error ("Нет связи с сервером")
    # instead of the real CORS refusal.
    allow_headers=["Authorization", "Content-Type", "X-Max-Init-Data"],
    allow_credentials=True,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)


@app.middleware("http")
async def secure_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none';"
    return response


app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(projects.router, prefix=settings.api_prefix)
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(project_versions.router, prefix=settings.api_prefix)
app.include_router(files.router, prefix=settings.api_prefix)
app.include_router(deployments.router, prefix=settings.api_prefix)
app.include_router(providers.router, prefix=settings.api_prefix)
app.include_router(secrets.router, prefix=settings.api_prefix)
app.include_router(telegram.router, prefix=settings.api_prefix)
app.include_router(max_router.router, prefix=settings.api_prefix)
app.include_router(billing.router, prefix=settings.api_prefix)
app.include_router(byok.router, prefix=settings.api_prefix)
app.include_router(orchestration.router, prefix=settings.api_prefix)
app.include_router(orchestration_admin.router, prefix=settings.api_prefix)
app.include_router(analytics.router, prefix=settings.api_prefix)
app.include_router(support.router, prefix=settings.api_prefix)
app.include_router(support_staff.router, prefix=settings.api_prefix)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
