"""FastAPI application factory + serve helper.

Designed to be importable independently from the CLI for tests:
``create_app(data_dir=...)`` returns a fully wired app, no side effects
beyond reading/writing the data dir.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, db


def create_app(*, data_dir: Path) -> FastAPI:
    """Build the FastAPI app bound to ``data_dir``.

    The data dir holds SQLite db + uploaded notes. Idempotent:
    initializes schema if missing, leaves existing data alone.
    """
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    db.initialize(data_dir)

    app = FastAPI(
        title="praxnest",
        version=__version__,
        description="Local-first team AI workspace",
    )
    app.state.data_dir = data_dir

    # Session secret persisted to the data dir so restarts don't log
    # everyone out. Same model as Django's SECRET_KEY: never check in,
    # rotate to invalidate sessions.
    secret_path = data_dir / "session-secret"
    if secret_path.exists():
        secret = secret_path.read_text(encoding="utf-8").strip()
    else:
        secret = secrets.token_urlsafe(48)
        secret_path.write_text(secret + "\n", encoding="utf-8")
        try:
            import os as _os
            _os.chmod(secret_path, 0o600)
        except OSError:
            pass

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="praxnest_session",
        max_age=60 * 60 * 24 * 30,  # 30 days
        same_site="lax",
        https_only=False,  # local-first; users behind https terminate at reverse proxy
    )

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "name": "praxnest",
            "version": __version__,
            "data_dir": str(data_dir),
        })

    # Mount API route modules.
    from .routes import (
        audit_router, auth_router, notes_router, notes_search_router, workspaces_router,
    )

    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(workspaces_router)
    app.include_router(notes_router)
    app.include_router(notes_search_router)

    # Static index page (login + SPA shell). The ``request`` arg is
    # unused but kept for symmetry with other handlers; the ``Request``
    # type annotation tells FastAPI not to treat it as a query param.
    web_dir = Path(__file__).parent / "web"
    index_path = web_dir / "index.html"

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    return app


def serve(*, host: str, port: int, data_dir: Path) -> None:
    """Run uvicorn against `create_app(data_dir=...)`. Blocks until Ctrl+C."""
    import uvicorn

    app = create_app(data_dir=data_dir)
    uvicorn.run(app, host=host, port=port, log_level="info")
