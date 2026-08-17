"""Shared types and abstract base for text notification channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChannelStatus(str, Enum):
    """Channel readiness for alert delivery."""

    NOT_READY = "not_ready"
    AVAILABLE = "available"
    FAILED = "failed"


class ErrorCategory(str, Enum):
    MISSING_CONFIG = "missing_config"
    AUTH = "auth"
    NETWORK = "network"
    API = "api"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass
class SendResult:
    ok: bool
    errcode: int | None = None
    errmsg: str | None = None
    invalid_user: str | None = None
    category: ErrorCategory | None = None
    message: str = ""
    raw: dict[str, Any] | None = None


@dataclass
class ChannelSnapshot:
    status: ChannelStatus
    missing_fields: list[str] = field(default_factory=list)
    last_error: str | None = None
    last_error_category: ErrorCategory | None = None
    last_errcode: int | None = None
    configured: bool = False
    recipient_masked: str = ""


def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}***"


class TextNotifier(ABC):
    """Abstract sender for plain-text notifications."""

    channel_id: str

    @abstractmethod
    def send_text(self, content: str, *, to_user: str | None = None) -> SendResult:
        """Send a text message. Must not pretend success when misconfigured."""

    @abstractmethod
    def self_check(self) -> SendResult:
        """Connectivity check; must not raise to the caller."""

    @abstractmethod
    def snapshot(self) -> ChannelSnapshot:
        """Non-secret status snapshot for APIs and diagnostics."""

    @property
    @abstractmethod
    def status(self) -> ChannelStatus: ...

    @property
    def is_available(self) -> bool:
        return self.status == ChannelStatus.AVAILABLE

    @property
    def self_check_on_startup(self) -> bool:
        return False
