from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.services.notifier.base import ChannelStatus, ErrorCategory
from app.services.notifier.email_notifier import EmailNotifier, parse_recipients


def _settings(**overrides) -> Settings:
    base = {
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_from": "",
        "smtp_to": "",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "smtp_timeout_seconds": 5.0,
        "smtp_subject": "AMTSM 通知",
        "smtp_self_check_on_startup": False,
    }
    base.update(overrides)
    return Settings(**base)


def _full_settings(**overrides) -> Settings:
    kwargs = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "user",
        "smtp_password": "super-secret-smtp",
        "smtp_from": "alerts@example.com",
        "smtp_to": "owner@example.com, other@example.com",
    }
    kwargs.update(overrides)
    return _settings(**kwargs)


def _smtp_client(refused: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.send_message.return_value = refused or {}
    return client


def test_parse_recipients_splits_comma_and_semicolon() -> None:
    assert parse_recipients("a@x.com, b@x.com;c@x.com") == [
        "a@x.com",
        "b@x.com",
        "c@x.com",
    ]


def test_missing_config_marks_not_ready() -> None:
    notifier = EmailNotifier(_settings(smtp_host="smtp.example.com"))
    assert notifier.status == ChannelStatus.NOT_READY
    assert "SMTP_FROM" in notifier.missing_fields
    assert "SMTP_TO" in notifier.missing_fields
    result = notifier.send_text("hello")
    assert result.ok is False
    assert result.category == ErrorCategory.MISSING_CONFIG


def test_send_text_success() -> None:
    notifier = EmailNotifier(_full_settings())
    client = _smtp_client()
    with patch.object(notifier, "_open_smtp", return_value=client):
        result = notifier.send_text("hello")

    assert result.ok is True
    assert notifier.status == ChannelStatus.AVAILABLE
    client.send_message.assert_called_once()
    mail = client.send_message.call_args[0][0]
    assert mail["To"] == "owner@example.com, other@example.com"
    assert mail["From"] == "alerts@example.com"
    assert mail["Subject"] == "AMTSM 通知"
    assert "hello" in mail.get_content()


def test_send_text_to_user_overrides_default_recipient() -> None:
    notifier = EmailNotifier(_full_settings())
    client = _smtp_client()
    with patch.object(notifier, "_open_smtp", return_value=client):
        result = notifier.send_text("ping", to_user="override@example.com")

    assert result.ok is True
    mail = client.send_message.call_args[0][0]
    assert mail["To"] == "override@example.com"


def test_auth_error_classified() -> None:
    import smtplib

    notifier = EmailNotifier(_full_settings())
    with patch.object(
        notifier,
        "_open_smtp",
        side_effect=smtplib.SMTPAuthenticationError(535, b"auth failed"),
    ):
        result = notifier.send_text("alert")

    assert result.ok is False
    assert result.category == ErrorCategory.AUTH
    assert notifier.status == ChannelStatus.FAILED
    snap = notifier.snapshot()
    assert "super-secret-smtp" not in (snap.last_error or "")


def test_network_timeout_classified() -> None:
    notifier = EmailNotifier(_full_settings())
    with patch.object(notifier, "_open_smtp", side_effect=TimeoutError("timed out")):
        result = notifier.send_text("alert")

    assert result.ok is False
    assert result.category == ErrorCategory.NETWORK
    assert notifier.status == ChannelStatus.FAILED


def test_snapshot_masks_sensitive_fields() -> None:
    notifier = EmailNotifier(_full_settings())
    snap = notifier.snapshot()
    assert snap.smtp_host == "smtp.example.com"
    assert snap.smtp_port == 587
    assert "***" in snap.from_masked
    assert "***" in snap.to_masked
    assert "super-secret" not in snap.from_masked
    assert snap.use_tls is True
    assert snap.use_ssl is False


def test_partial_refused_recipients_still_available() -> None:
    notifier = EmailNotifier(_full_settings())
    client = _smtp_client(refused={"other@example.com": (550, b"user unknown")})
    with patch.object(notifier, "_open_smtp", return_value=client):
        result = notifier.send_text("alert")

    assert result.ok is True
    assert result.category == ErrorCategory.PARTIAL
    assert result.invalid_user == "other@example.com"
    assert notifier.status == ChannelStatus.AVAILABLE


def test_port_465_uses_implicit_ssl_even_when_flag_false() -> None:
    notifier = EmailNotifier(
        _full_settings(smtp_port=465, smtp_use_ssl=False, smtp_use_tls=True)
    )
    mock_ssl = MagicMock()
    mock_ssl.__enter__.return_value = mock_ssl
    mock_ssl.__exit__.return_value = False
    mock_ssl.send_message.return_value = {}
    with (
        patch(
            "app.services.notifier.email_notifier.smtplib.SMTP_SSL",
            return_value=mock_ssl,
        ) as ssl_ctor,
        patch("app.services.notifier.email_notifier.smtplib.SMTP") as plain_ctor,
    ):
        result = notifier.send_text("hello")

    assert result.ok is True
    ssl_ctor.assert_called_once()
    plain_ctor.assert_not_called()
    assert notifier.snapshot().use_ssl is True
    assert notifier.snapshot().use_tls is False


def test_smtp_user_alias_and_from_fallback_login() -> None:
    notifier = EmailNotifier(
        _settings(
            smtp_host="smtp.example.com",
            smtp_username="",
            smtp_user="alias@example.com",
            smtp_password="secret",
            smtp_from="from@example.com",
            smtp_to="to@example.com",
        )
    )
    client = _smtp_client()
    with patch(
        "app.services.notifier.email_notifier.smtplib.SMTP",
        return_value=client,
    ):
        result = notifier.send_text("hello")

    assert result.ok is True
    client.login.assert_called_once_with("alias@example.com", "secret")


def test_login_falls_back_to_from_when_username_missing() -> None:
    notifier = EmailNotifier(_full_settings(smtp_username="", smtp_user=""))
    client = _smtp_client()
    with patch(
        "app.services.notifier.email_notifier.smtplib.SMTP",
        return_value=client,
    ):
        result = notifier.send_text("hello")

    assert result.ok is True
    client.login.assert_called_once_with("alerts@example.com", "super-secret-smtp")
    notifier = EmailNotifier(_full_settings())
    client = _smtp_client()
    with patch.object(notifier, "_open_smtp", return_value=client):
        result = notifier.self_check()

    assert result.ok is True
    assert notifier.is_available
    client.send_message.assert_called_once()


@pytest.mark.parametrize("missing_key", ["smtp_host", "smtp_from", "smtp_to"])
def test_each_required_field_blocks_ready(missing_key: str) -> None:
    kwargs = {
        "smtp_host": "smtp.example.com",
        "smtp_from": "a@x.com",
        "smtp_to": "b@x.com",
    }
    kwargs[missing_key] = ""
    notifier = EmailNotifier(_settings(**kwargs))
    assert notifier.status == ChannelStatus.NOT_READY
    assert notifier.missing_fields


def test_ssl_context_uses_certifi_ca_bundle() -> None:
    import ssl as ssl_mod

    from app.services.notifier.email_notifier import _ssl_context

    ctx = _ssl_context()
    assert ctx.verify_mode == ssl_mod.CERT_REQUIRED
    assert len(ctx.get_ca_certs()) > 0
