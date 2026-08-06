"""WeChat enterprise notification channel (user story 07).

Wraps wechatpy ``WeChatClient`` for config validation, startup self-check,
and reusable text message sending for later buy/sell alert stories.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import requests
from wechatpy.enterprise import WeChatClient
from wechatpy.exceptions import WeChatClientException, WeChatException

from app.config import Settings, settings

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = (
    "WECHAT_CORP_ID",
    "WECHAT_AGENT_ID",
    "WECHAT_SECRET",
    "WECHAT_TO_USER",
)

SELF_CHECK_CONTENT = "AMTSM 微信通道自检：连接正常"


class ChannelStatus(str, Enum):
    """WeChat channel readiness for alert delivery."""

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


# Enterprise WeChat errcodes commonly related to credentials / token.
_AUTH_ERRCODES = frozenset(
    {
        40001,  # invalid credential
        40014,  # invalid access_token
        41001,  # access_token missing
        42001,  # access_token expired
        40013,  # invalid corp id
        301002,  # no privilege / secret
    }
)


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
    to_user_masked: str = ""
    agent_id: str = ""
    corp_id_masked: str = ""


def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}***"


def _classify_wechat_error(exc: BaseException) -> ErrorCategory:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return ErrorCategory.NETWORK
    if isinstance(exc, requests.RequestException):
        return ErrorCategory.NETWORK
    if isinstance(exc, WeChatClientException):
        if getattr(exc, "errcode", None) in _AUTH_ERRCODES:
            return ErrorCategory.AUTH
        return ErrorCategory.API
    if isinstance(exc, WeChatException):
        return ErrorCategory.API
    return ErrorCategory.UNKNOWN


class WeChatNotifier:
    """Enterprise WeChat application message sender."""

    def __init__(self, app_settings: Settings | None = None) -> None:
        self._settings = app_settings or settings
        self._client: WeChatClient | None = None
        self._status = ChannelStatus.NOT_READY
        self._missing_fields: list[str] = []
        self._last_error: str | None = None
        self._last_error_category: ErrorCategory | None = None
        self._last_errcode: int | None = None
        self._reload_config()

    def _reload_config(self) -> None:
        corp_id = (self._settings.wechat_corp_id or "").strip()
        agent_id = (self._settings.wechat_agent_id or "").strip()
        secret = (self._settings.wechat_secret or "").strip()
        to_user = (self._settings.wechat_to_user or "").strip()

        missing: list[str] = []
        if not corp_id:
            missing.append("WECHAT_CORP_ID")
        if not agent_id:
            missing.append("WECHAT_AGENT_ID")
        else:
            try:
                int(agent_id)
            except ValueError:
                missing.append("WECHAT_AGENT_ID")
        if not secret:
            missing.append("WECHAT_SECRET")
        if not to_user:
            missing.append("WECHAT_TO_USER")

        self._missing_fields = missing
        self._client = None
        if missing:
            self._status = ChannelStatus.NOT_READY
            self._last_error = f"缺少必填配置: {', '.join(missing)}"
            self._last_error_category = ErrorCategory.MISSING_CONFIG
            self._last_errcode = None
            logger.warning(
                "WeChat channel not ready; missing config: %s",
                ", ".join(missing),
            )
            return

        timeout = float(self._settings.wechat_request_timeout_seconds)
        self._client = WeChatClient(corp_id, secret, timeout=timeout)
        # Config present but not yet verified.
        if self._status != ChannelStatus.AVAILABLE:
            self._status = ChannelStatus.NOT_READY
            self._last_error = "配置已加载，等待连通性自检"
            self._last_error_category = None
            self._last_errcode = None

    @property
    def status(self) -> ChannelStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == ChannelStatus.AVAILABLE

    @property
    def missing_fields(self) -> list[str]:
        return list(self._missing_fields)

    def snapshot(self) -> ChannelSnapshot:
        s = self._settings
        return ChannelSnapshot(
            status=self._status,
            missing_fields=self.missing_fields,
            last_error=self._last_error,
            last_error_category=self._last_error_category,
            last_errcode=self._last_errcode,
            configured=not self._missing_fields,
            to_user_masked=_mask_secret(s.wechat_to_user.strip(), keep=2),
            agent_id=(s.wechat_agent_id or "").strip(),
            corp_id_masked=_mask_secret(s.wechat_corp_id.strip(), keep=4),
        )

    def _agent_id_int(self) -> int:
        return int((self._settings.wechat_agent_id or "").strip())

    def _to_user(self) -> str:
        return (self._settings.wechat_to_user or "").strip()

    def send_text(self, content: str, *, to_user: str | None = None) -> SendResult:
        """Send a text message via the enterprise WeChat application.

        Reuses the process-local ``WeChatClient`` (AccessToken managed by wechatpy).
        Refuses to pretend success when the channel is not configured.
        """
        if self._missing_fields or self._client is None:
            missing = self._missing_fields or list(REQUIRED_FIELDS)
            msg = f"微信通道未就绪，缺少配置: {', '.join(missing)}"
            logger.error(msg)
            return SendResult(
                ok=False,
                category=ErrorCategory.MISSING_CONFIG,
                message=msg,
            )

        if self._status == ChannelStatus.FAILED:
            # Allow retry of send even after prior failure (token may recover),
            # but callers of alerts should gate on is_available for real alerts.
            pass

        user_ids = (to_user or self._to_user()).strip()
        if not user_ids:
            msg = "收件人为空，无法发送"
            return SendResult(
                ok=False,
                category=ErrorCategory.MISSING_CONFIG,
                message=msg,
            )

        try:
            result = self._client.message.send_text(
                self._agent_id_int(),
                user_ids,
                content,
            )
        except Exception as exc:  # noqa: BLE001 — classify and degrade
            category = _classify_wechat_error(exc)
            errcode = getattr(exc, "errcode", None)
            errmsg = getattr(exc, "errmsg", None) or str(exc)
            # Never log secret / full credential material.
            logger.error(
                "WeChat send_text failed category=%s errcode=%s errmsg=%s",
                category.value,
                errcode,
                errmsg,
            )
            self._status = ChannelStatus.FAILED
            self._last_error = f"[{category.value}] errcode={errcode} errmsg={errmsg}"
            self._last_error_category = category
            self._last_errcode = int(errcode) if errcode is not None else None
            return SendResult(
                ok=False,
                errcode=int(errcode) if errcode is not None else None,
                errmsg=str(errmsg) if errmsg is not None else None,
                category=category,
                message=self._last_error,
            )

        invalid_user = None
        if isinstance(result, dict):
            invalid_user = result.get("invaliduser") or result.get("invalid_user")
            if invalid_user:
                invalid_user = str(invalid_user).strip() or None

        if invalid_user:
            # Partial delivery: some recipients rejected.
            note = f"部分收件人无效: invaliduser={invalid_user}"
            logger.warning("WeChat send_text partial success: %s", note)
            self._status = ChannelStatus.AVAILABLE
            self._last_error = note
            self._last_error_category = ErrorCategory.PARTIAL
            self._last_errcode = 0
            return SendResult(
                ok=True,
                errcode=0,
                errmsg="ok",
                invalid_user=invalid_user,
                category=ErrorCategory.PARTIAL,
                message=note,
                raw=result if isinstance(result, dict) else None,
            )

        self._status = ChannelStatus.AVAILABLE
        self._last_error = None
        self._last_error_category = None
        self._last_errcode = 0
        return SendResult(
            ok=True,
            errcode=0,
            errmsg="ok",
            message="发送成功",
            raw=result if isinstance(result, dict) else None,
        )

    def self_check(self) -> SendResult:
        """Connectivity self-check: send one test text message.

        Safe to call during process startup; failures mark the channel unavailable
        without raising to the caller.
        """
        self._reload_config()
        if self._missing_fields:
            result = SendResult(
                ok=False,
                category=ErrorCategory.MISSING_CONFIG,
                message=self._last_error or "配置缺失",
            )
            return result

        logger.info(
            "WeChat channel self-check starting (agent_id=%s)", self._agent_id_int()
        )
        result = self.send_text(SELF_CHECK_CONTENT)
        if result.ok:
            logger.info("WeChat channel self-check succeeded; status=available")
        else:
            logger.error(
                "WeChat channel self-check failed; status=%s detail=%s",
                self._status.value,
                result.message,
            )
        return result


# Process-local singleton used by API lifespan and later alert flows.
wechat_notifier = WeChatNotifier()
