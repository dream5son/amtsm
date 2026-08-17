from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.wechat_notifier import (
    ChannelSnapshot,
    ChannelStatus,
    ErrorCategory,
    SendResult,
)

client = TestClient(app)


def test_wechat_status_endpoint_default_not_ready() -> None:
    snap = ChannelSnapshot(
        status=ChannelStatus.NOT_READY,
        missing_fields=["WECHAT_SECRET", "WECHAT_TO_USER"],
        last_error="缺少必填配置: WECHAT_SECRET, WECHAT_TO_USER",
        last_error_category=ErrorCategory.MISSING_CONFIG,
        configured=False,
    )
    with patch("app.api.notify.wechat_notifier") as mock_notifier:
        mock_notifier.snapshot.return_value = snap
        mock_notifier.is_available = False
        response = client.get("/api/notify/wechat/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["available"] is False
    assert "WECHAT_SECRET" in data["missing_fields"]


def test_wechat_self_check_endpoint() -> None:
    fake = SendResult(
        ok=True,
        errcode=0,
        errmsg="ok",
        message="发送成功",
    )
    snap = ChannelSnapshot(
        status=ChannelStatus.AVAILABLE,
        configured=True,
        corp_id_masked="wwco***",
        agent_id="1000002",
        to_user_masked="zh***",
    )
    with patch("app.api.notify.wechat_notifier") as mock_notifier:
        mock_notifier.self_check.return_value = fake
        mock_notifier.status = ChannelStatus.AVAILABLE
        mock_notifier.is_available = True
        mock_notifier.snapshot.return_value = snap
        response = client.post("/api/notify/wechat/self-check")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == "available"
    assert data["channel"]["available"] is True


def _available_snap() -> ChannelSnapshot:
    return ChannelSnapshot(
        status=ChannelStatus.AVAILABLE,
        configured=True,
        corp_id_masked="wwco***",
        agent_id="1000002",
        to_user_masked="zh***",
    )


def test_wechat_send_endpoint_success() -> None:
    fake = SendResult(
        ok=True,
        errcode=0,
        errmsg="ok",
        message="发送成功",
    )
    snap = _available_snap()
    with patch("app.api.notify.wechat_notifier") as mock_notifier:
        mock_notifier.send_text.return_value = fake
        mock_notifier.status = ChannelStatus.AVAILABLE
        mock_notifier.is_available = True
        mock_notifier.snapshot.return_value = snap
        response = client.post(
            "/api/notify/wechat/send",
            json={"to_user": "zhangsan", "content": "hello"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["message"] == "发送成功"
    assert data["channel"]["available"] is True
    mock_notifier.send_text.assert_called_once_with("hello", to_user="zhangsan")


def test_wechat_send_endpoint_empty_to_user_returns_400() -> None:
    response = client.post(
        "/api/notify/wechat/send",
        json={"to_user": "   ", "content": "hello"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "to_user and content must not be empty"


def test_wechat_send_endpoint_failure_returns_ok_false() -> None:
    fake = SendResult(
        ok=False,
        errcode=40001,
        errmsg="invalid credential",
        category=ErrorCategory.AUTH,
        message="[auth] errcode=40001 errmsg=invalid credential",
    )
    snap = ChannelSnapshot(
        status=ChannelStatus.FAILED,
        configured=True,
        last_error="[auth] errcode=40001 errmsg=invalid credential",
        last_error_category=ErrorCategory.AUTH,
        last_errcode=40001,
        corp_id_masked="wwco***",
        agent_id="1000002",
        to_user_masked="zh***",
    )
    with patch("app.api.notify.wechat_notifier") as mock_notifier:
        mock_notifier.send_text.return_value = fake
        mock_notifier.status = ChannelStatus.FAILED
        mock_notifier.is_available = False
        mock_notifier.snapshot.return_value = snap
        response = client.post(
            "/api/notify/wechat/send",
            json={"to_user": "zhangsan", "content": "hello"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == "failed"
    assert data["category"] == "auth"
    assert data["errcode"] == 40001
