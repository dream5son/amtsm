from unittest.mock import MagicMock, patch

import pytest
import requests
from wechatpy.exceptions import WeChatClientException

from app.config import Settings
from app.services.wechat_notifier import (
    ChannelStatus,
    ErrorCategory,
    WeChatNotifier,
)


def _settings(**overrides) -> Settings:
    base = {
        "wechat_corp_id": "",
        "wechat_agent_id": "",
        "wechat_secret": "",
        "wechat_to_user": "",
        "wechat_request_timeout_seconds": 5.0,
        "wechat_self_check_on_startup": False,
    }
    base.update(overrides)
    return Settings(**base)


def _full_settings(**overrides) -> Settings:
    return _settings(
        wechat_corp_id="wwcorp1234567890",
        wechat_agent_id="1000002",
        wechat_secret="super-secret-value",
        wechat_to_user="zhangsan|lisi",
        **overrides,
    )


def test_missing_config_marks_not_ready() -> None:
    notifier = WeChatNotifier(_settings(wechat_corp_id="wwonly"))
    assert notifier.status == ChannelStatus.NOT_READY
    assert "WECHAT_SECRET" in notifier.missing_fields
    assert "WECHAT_TO_USER" in notifier.missing_fields
    assert "WECHAT_AGENT_ID" in notifier.missing_fields
    result = notifier.send_text("hello")
    assert result.ok is False
    assert result.category == ErrorCategory.MISSING_CONFIG


def test_self_check_success_marks_available() -> None:
    notifier = WeChatNotifier(_full_settings())
    mock_client = MagicMock()
    mock_client.message.send_text.return_value = {"errcode": 0, "errmsg": "ok"}
    notifier._client = mock_client

    with patch(
        "app.services.wechat_notifier.WeChatClient",
        return_value=mock_client,
    ):
        result = notifier.self_check()

    assert result.ok is True
    assert notifier.status == ChannelStatus.AVAILABLE
    assert notifier.is_available
    mock_client.message.send_text.assert_called()
    args = mock_client.message.send_text.call_args[0]
    assert args[0] == 1000002
    assert args[1] == "zhangsan|lisi"


def test_self_check_auth_error_marks_failed() -> None:
    notifier = WeChatNotifier(_full_settings())
    mock_client = MagicMock()
    mock_client.message.send_text.side_effect = WeChatClientException(
        40001, "invalid credential"
    )
    notifier._client = mock_client

    with patch(
        "app.services.wechat_notifier.WeChatClient",
        return_value=mock_client,
    ):
        result = notifier.self_check()

    assert result.ok is False
    assert result.category == ErrorCategory.AUTH
    assert result.errcode == 40001
    assert notifier.status == ChannelStatus.FAILED
    snap = notifier.snapshot()
    assert snap.last_errcode == 40001
    assert "super-secret-value" not in (snap.last_error or "")


def test_partial_invalid_user_still_available() -> None:
    notifier = WeChatNotifier(_full_settings())
    mock_client = MagicMock()
    mock_client.message.send_text.return_value = {
        "errcode": 0,
        "errmsg": "ok",
        "invaliduser": "lisi",
    }
    notifier._client = mock_client

    result = notifier.send_text("alert")
    assert result.ok is True
    assert result.invalid_user == "lisi"
    assert result.category == ErrorCategory.PARTIAL
    assert notifier.status == ChannelStatus.AVAILABLE


def test_network_timeout_classified() -> None:
    notifier = WeChatNotifier(_full_settings())
    mock_client = MagicMock()
    mock_client.message.send_text.side_effect = requests.Timeout("timed out")
    notifier._client = mock_client

    result = notifier.send_text("alert")
    assert result.ok is False
    assert result.category == ErrorCategory.NETWORK
    assert notifier.status == ChannelStatus.FAILED


def test_snapshot_masks_sensitive_fields() -> None:
    notifier = WeChatNotifier(_full_settings())
    snap = notifier.snapshot()
    assert "super-secret" not in snap.corp_id_masked
    assert snap.corp_id_masked.startswith("wwco")
    assert "***" in snap.to_user_masked
    assert snap.agent_id == "1000002"


def test_send_text_to_user_overrides_default_recipient() -> None:
    notifier = WeChatNotifier(_full_settings())
    mock_client = MagicMock()
    mock_client.message.send_text.return_value = {"errcode": 0, "errmsg": "ok"}
    notifier._client = mock_client

    result = notifier.send_text("ping", to_user="wangwu")

    assert result.ok is True
    mock_client.message.send_text.assert_called_once_with(1000002, "wangwu", "ping")


def test_reuses_client_across_sends() -> None:
    notifier = WeChatNotifier(_full_settings())
    mock_client = MagicMock()
    mock_client.message.send_text.return_value = {"errcode": 0, "errmsg": "ok"}
    notifier._client = mock_client

    assert notifier.send_text("one").ok
    assert notifier.send_text("two").ok
    assert mock_client.message.send_text.call_count == 2
    assert notifier._client is mock_client


def test_invalid_agent_id_treated_as_missing() -> None:
    notifier = WeChatNotifier(
        _settings(
            wechat_corp_id="wwcorp1234567890",
            wechat_agent_id="not-int",
            wechat_secret="super-secret-value",
            wechat_to_user="zhangsan|lisi",
        )
    )
    assert "WECHAT_AGENT_ID" in notifier.missing_fields
    assert notifier.status == ChannelStatus.NOT_READY


@pytest.mark.parametrize(
    "missing_key",
    ["wechat_secret", "wechat_to_user", "wechat_corp_id", "wechat_agent_id"],
)
def test_each_required_field_blocks_ready(missing_key: str) -> None:
    kwargs = {
        "wechat_corp_id": "wwcorp",
        "wechat_agent_id": "1",
        "wechat_secret": "secret",
        "wechat_to_user": "user1",
    }
    kwargs[missing_key] = ""
    notifier = WeChatNotifier(_settings(**kwargs))
    assert notifier.status == ChannelStatus.NOT_READY
    assert notifier.missing_fields
