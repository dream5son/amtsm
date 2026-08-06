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
