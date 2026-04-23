from fastapi import APIRouter
from ..config import settings
from ..models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Simple health endpoint to verify the app is running.
    This will be useful for uptime checks and monitoring.
    """
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        version=settings.version,
        env=settings.env,
    )
