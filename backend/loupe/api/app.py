import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from loupe import __version__
from loupe.adapters import build_adapter
from loupe.adapters.base import AdapterError, AuthError, RateLimited, RepoNotFound
from loupe.api.routes import router
from loupe.config import Settings, get_settings
from loupe.db import init_engine, session_scope
from loupe.llm import build_provider
from loupe.logging_setup import configure_logging
from loupe.models import Repository
from loupe.sync import record_sync_error, sync_repository

log = logging.getLogger(__name__)


class SyncManager:
    """Owns the background sync loop and de-duplicates concurrent requests per repo."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._inflight: set[int] = set()
        self._task: asyncio.Task | None = None
        self._ticker: asyncio.Task | None = None

    def schedule(self, repository_id: int) -> None:
        if repository_id not in self._inflight:
            self._inflight.add(repository_id)
            self._queue.put_nowait(repository_id)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._worker(), name="loupe-sync-worker")
        if self._settings.background_sync_enabled:
            self._ticker = asyncio.create_task(self._tick(), name="loupe-sync-ticker")

    async def stop(self) -> None:
        for t in (self._task, self._ticker):
            if t:
                t.cancel()
        for t in (self._task, self._ticker):
            if t:
                with suppress(asyncio.CancelledError):
                    await t

    async def _tick(self) -> None:
        interval = self._settings.sync_interval_minutes * 60
        while True:
            await asyncio.sleep(interval)
            with session_scope() as s:
                ids = list(s.scalars(select(Repository.id)))
            for rid in ids:
                self.schedule(rid)

    async def _worker(self) -> None:
        while True:
            rid = await self._queue.get()
            try:
                try:
                    adapter = build_adapter("github", self._settings)
                except AdapterError as exc:
                    record_sync_error(rid, exc)
                    raise
                try:
                    stats = await sync_repository(rid, adapter, self._settings.backfill_days)
                    log.info("sync ok repository_id=%s %s", rid, stats.as_dict())
                finally:
                    await adapter.aclose()
            except RateLimited as exc:
                log.warning("rate limited; will retry repository_id=%s at %s", rid, exc.reset_at)
            except (AuthError, RepoNotFound, AdapterError) as exc:
                log.warning("sync failed repository_id=%s: %s", rid, exc)
            except Exception:  # noqa: BLE001
                log.exception("unexpected sync failure repository_id=%s", rid)
            finally:
                self._inflight.discard(rid)
                self._queue.task_done()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_engine(settings.database_url)
        app.state.settings = settings
        app.state.llm_provider = build_provider(settings)
        app.state.sync_manager = SyncManager(settings)
        await app.state.sync_manager.start()
        log.info("loupe %s up: provider=%s model=%s db=%s", __version__, settings.llm_provider, app.state.llm_provider.model, _redact_db_url(settings.database_url))
        try:
            yield
        finally:
            await app.state.sync_manager.stop()

    app = FastAPI(
        title="Loupe",
        version=__version__,
        description="A small lens on team collaboration signals. Metrics, drift signals, and LLM narratives with verified evidence.",
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"], allow_headers=["*"])
    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app


def _redact_db_url(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url
