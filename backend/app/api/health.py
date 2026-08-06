from fastapi import APIRouter

from app.config import settings
from app.engine.resilience import get_system_status

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
    }


@router.get("/system/status")
def system_status() -> dict:
    """Resilience flags for frontend (quote delay / consecutive poll failures)."""
    return get_system_status()
