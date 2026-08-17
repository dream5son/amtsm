from unittest.mock import MagicMock

from app.services.alert_service import _combine_channel_results, _send_with_retry
from app.services.notifier.base import ErrorCategory, SendResult, TextNotifier
from app.services.notifier.email_notifier import email_notifier
from app.services.notifier.registry import (
    get_enabled_notifiers,
    get_notifier,
    get_unknown_channel_ids,
    parse_notify_channels,
    resolve_alert_channels,
)
from app.services.notifier.wechat import wechat_notifier


def test_parse_notify_channels_default_wechat() -> None:
    assert parse_notify_channels("wechat") == ["wechat"]


def test_parse_notify_channels_multi_and_dedupe() -> None:
    assert parse_notify_channels(" wechat, email, WECHAT ") == ["wechat", "email"]


def test_parse_notify_channels_ignores_empty() -> None:
    assert parse_notify_channels("wechat,, ,email") == ["wechat", "email"]


def test_unknown_channel_is_reported() -> None:
    assert parse_notify_channels("wechat,sms") == ["wechat", "sms"]
    assert get_notifier("sms") is None


def test_get_notifier_known_ids() -> None:
    assert get_notifier("wechat") is wechat_notifier
    assert get_notifier("email") is email_notifier


def test_get_enabled_notifiers_skips_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.notifier.registry.parse_notify_channels",
        lambda raw=None: ["wechat", "sms"],
    )
    enabled = get_enabled_notifiers()
    assert enabled == [wechat_notifier]
    assert get_unknown_channel_ids() == ["sms"]


def test_resolve_alert_channels_keeps_unknown_slot(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.notifier.registry.parse_notify_channels",
        lambda raw=None: ["email", "pager"],
    )
    resolved = resolve_alert_channels()
    assert resolved == [("email", email_notifier), ("pager", None)]


def test_combine_success_if_any_channel_ok() -> None:
    combined = _combine_channel_results(
        [
            SendResult(ok=True, message="微信成功"),
            SendResult(ok=False, category=ErrorCategory.NETWORK, message="邮件失败"),
        ]
    )
    assert combined.ok is True
    assert combined.category == ErrorCategory.PARTIAL
    assert "邮件失败" in combined.message


def test_combine_all_failures() -> None:
    combined = _combine_channel_results(
        [
            SendResult(ok=False, message="微信失败"),
            SendResult(ok=False, message="邮件失败"),
        ]
    )
    assert combined.ok is False
    assert "微信失败" in combined.message
    assert "邮件失败" in combined.message


def test_send_with_retry_fans_out_and_unknown_fails(monkeypatch) -> None:
    wechat = MagicMock(spec=TextNotifier)
    wechat.channel_id = "wechat"
    wechat.send_text.return_value = SendResult(ok=True, message="ok")
    monkeypatch.setattr(
        "app.services.alert_service.resolve_alert_channels",
        lambda: [("wechat", wechat), ("sms", None)],
    )
    monkeypatch.setattr("app.services.alert_service.settings.alert_send_max_retries", 0)

    result, _latency = _send_with_retry("hello", log_prefix="test")
    assert result.ok is True
    wechat.send_text.assert_called_once_with("hello")
    assert "未知通知通道: sms" in (result.message or "")
