"""SMTP email notification channel."""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage

import certifi

from app.config import Settings, settings
from app.services.notifier.base import (
    ChannelSnapshot,
    ChannelStatus,
    ErrorCategory,
    SendResult,
    TextNotifier,
    mask_secret,
)

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = (
    "SMTP_HOST",
    "SMTP_FROM",
    "SMTP_TO",
)

SELF_CHECK_CONTENT = "AMTSM 邮件通道自检：连接正常"


@dataclass
class EmailChannelSnapshot(ChannelSnapshot):
    smtp_host: str = ""
    smtp_port: int = 587
    from_masked: str = ""
    to_masked: str = ""
    use_tls: bool = True
    use_ssl: bool = False


def parse_recipients(raw: str) -> list[str]:
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def _ssl_context() -> ssl.SSLContext:
    """Build a verifying TLS context using certifi's CA bundle.

    macOS Python often lacks a usable default trust store, which surfaces as
    ``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``.
    """
    return ssl.create_default_context(cafile=certifi.where())


def _classify_smtp_error(exc: BaseException) -> ErrorCategory:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ErrorCategory.AUTH
    if isinstance(
        exc, (TimeoutError, ConnectionError, OSError, smtplib.SMTPConnectError)
    ):
        return ErrorCategory.NETWORK
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return ErrorCategory.NETWORK
    if isinstance(exc, smtplib.SMTPException):
        return ErrorCategory.API
    return ErrorCategory.UNKNOWN


class EmailNotifier(TextNotifier):
    """Plain-text sender over SMTP."""

    channel_id = "email"

    def __init__(self, app_settings: Settings | None = None) -> None:
        self._settings = app_settings or settings
        self._status = ChannelStatus.NOT_READY
        self._missing_fields: list[str] = []
        self._last_error: str | None = None
        self._last_error_category: ErrorCategory | None = None
        self._last_errcode: int | None = None
        self._reload_config()

    def _reload_config(self) -> None:
        host = (self._settings.smtp_host or "").strip()
        from_addr = (self._settings.smtp_from or "").strip()
        to_addr = (self._settings.smtp_to or "").strip()

        missing: list[str] = []
        if not host:
            missing.append("SMTP_HOST")
        if not from_addr:
            missing.append("SMTP_FROM")
        if not to_addr or not parse_recipients(to_addr):
            missing.append("SMTP_TO")

        self._missing_fields = missing
        if missing:
            self._status = ChannelStatus.NOT_READY
            self._last_error = f"缺少必填配置: {', '.join(missing)}"
            self._last_error_category = ErrorCategory.MISSING_CONFIG
            self._last_errcode = None
            logger.warning(
                "Email channel not ready; missing config: %s",
                ", ".join(missing),
            )
            return

        if self._status != ChannelStatus.AVAILABLE:
            self._status = ChannelStatus.NOT_READY
            self._last_error = "配置已加载，等待连通性自检"
            self._last_error_category = None
            self._last_errcode = None

    @property
    def status(self) -> ChannelStatus:
        return self._status

    @property
    def missing_fields(self) -> list[str]:
        return list(self._missing_fields)

    @property
    def self_check_on_startup(self) -> bool:
        return bool(self._settings.smtp_self_check_on_startup)

    def snapshot(self) -> EmailChannelSnapshot:
        s = self._settings
        to_masked = mask_secret((s.smtp_to or "").strip(), keep=2)
        from_masked = mask_secret((s.smtp_from or "").strip(), keep=2)
        return EmailChannelSnapshot(
            status=self._status,
            missing_fields=self.missing_fields,
            last_error=self._last_error,
            last_error_category=self._last_error_category,
            last_errcode=self._last_errcode,
            configured=not self._missing_fields,
            recipient_masked=to_masked,
            smtp_host=(s.smtp_host or "").strip(),
            smtp_port=int(s.smtp_port),
            from_masked=from_masked,
            to_masked=to_masked,
            use_tls=self._use_tls(),
            use_ssl=self._use_ssl(),
        )

    def _from_addr(self) -> str:
        return (self._settings.smtp_from or "").strip()

    def _default_recipients(self) -> list[str]:
        return parse_recipients(self._settings.smtp_to or "")

    def _use_ssl(self) -> bool:
        # Port 465 is SMTPS (implicit SSL). STARTTLS on this port times out
        # waiting for a plaintext 220 banner.
        if int(self._settings.smtp_port) == 465:
            return True
        return bool(self._settings.smtp_use_ssl)

    def _use_tls(self) -> bool:
        if self._use_ssl():
            return False
        return bool(self._settings.smtp_use_tls)

    def _username(self) -> str:
        return (
            (self._settings.smtp_username or "").strip()
            or (self._settings.smtp_user or "").strip()
            or self._from_addr()
        )

    def _open_smtp(self) -> smtplib.SMTP:
        host = (self._settings.smtp_host or "").strip()
        port = int(self._settings.smtp_port)
        timeout = float(self._settings.smtp_timeout_seconds)
        use_ssl = self._use_ssl()
        use_tls = self._use_tls()

        client: smtplib.SMTP
        if use_ssl:
            client = smtplib.SMTP_SSL(
                host, port, timeout=timeout, context=_ssl_context()
            )
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)
            if use_tls:
                client.starttls(context=_ssl_context())

        username = self._username()
        password = self._settings.smtp_password or ""
        if username and password:
            client.login(username, password)
        return client

    def _build_message(self, content: str, recipients: Sequence[str]) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = (
            self._settings.smtp_subject or "AMTSM 通知"
        ).strip() or "AMTSM 通知"
        msg["From"] = self._from_addr()
        msg["To"] = ", ".join(recipients)
        msg.set_content(content)
        return msg

    def send_text(self, content: str, *, to_user: str | None = None) -> SendResult:
        """Send a plain-text email via SMTP.

        Refuses to pretend success when the channel is not configured.
        ``to_user`` overrides ``SMTP_TO`` (comma-separated addresses).
        """
        if self._missing_fields:
            missing = self._missing_fields or list(REQUIRED_FIELDS)
            msg = f"邮件通道未就绪，缺少配置: {', '.join(missing)}"
            logger.error(msg)
            return SendResult(
                ok=False,
                category=ErrorCategory.MISSING_CONFIG,
                message=msg,
            )

        recipients = (
            parse_recipients(to_user)
            if to_user is not None
            else self._default_recipients()
        )
        if not recipients:
            msg = "收件人为空，无法发送"
            return SendResult(
                ok=False,
                category=ErrorCategory.MISSING_CONFIG,
                message=msg,
            )

        from_addr = self._from_addr()
        if not from_addr:
            msg = "发件人为空，无法发送"
            return SendResult(
                ok=False,
                category=ErrorCategory.MISSING_CONFIG,
                message=msg,
            )

        try:
            mail = self._build_message(content, recipients)
            with self._open_smtp() as client:
                refused = client.send_message(mail)
        except Exception as exc:  # noqa: BLE001 — classify and degrade
            category = _classify_smtp_error(exc)
            errcode = getattr(exc, "smtp_code", None)
            errmsg = str(exc)
            logger.error(
                "Email send_text failed category=%s errcode=%s errmsg=%s",
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
                errmsg=errmsg,
                category=category,
                message=self._last_error,
            )

        if refused:
            invalid = ",".join(str(addr) for addr in refused)
            note = f"部分收件人被拒绝: {invalid}"
            logger.warning("Email send_text partial success: %s", note)
            self._status = ChannelStatus.AVAILABLE
            self._last_error = note
            self._last_error_category = ErrorCategory.PARTIAL
            self._last_errcode = 0
            return SendResult(
                ok=True,
                errcode=0,
                errmsg="ok",
                invalid_user=invalid,
                category=ErrorCategory.PARTIAL,
                message=note,
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
        )

    def self_check(self) -> SendResult:
        """Connectivity self-check: send one test email.

        Safe to call during process startup; failures mark the channel unavailable
        without raising to the caller.
        """
        self._reload_config()
        if self._missing_fields:
            return SendResult(
                ok=False,
                category=ErrorCategory.MISSING_CONFIG,
                message=self._last_error or "配置缺失",
            )

        logger.info(
            "Email channel self-check starting (host=%s port=%s)",
            (self._settings.smtp_host or "").strip(),
            self._settings.smtp_port,
        )
        result = self.send_text(SELF_CHECK_CONTENT)
        if result.ok:
            logger.info("Email channel self-check succeeded; status=available")
        else:
            logger.error(
                "Email channel self-check failed; status=%s detail=%s",
                self._status.value,
                result.message,
            )
        return result


email_notifier = EmailNotifier()
