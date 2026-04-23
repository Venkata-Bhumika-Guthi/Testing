from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .config import settings
from .routes.health import router as health_router
from .routes.chat import router as chat_router

BASE_DIR = Path(__file__).resolve().parent.parent  


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url="/docs",       
        redoc_url="/redoc",     
    )

    # Health routes
    app.include_router(health_router)

    # Chat routes (under /api)
    app.include_router(chat_router)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def root():
        """
        Serve the main web UI.
        Later, we could move to templates or a separate frontend build,
        but this keeps things simple for the hackathon.
        """
        index_path = BASE_DIR.parent / "frontend" / "index.html"
        return index_path.read_text(encoding="utf-8")

    return app


app = create_app()
