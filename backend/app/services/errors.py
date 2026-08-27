"""Domain errors mapped to HTTP by a single FastAPI exception handler."""

from __future__ import annotations


class AppError(Exception):
    status_code: int = 400


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409
