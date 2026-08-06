from fastapi import APIRouter

from app.schemas.notify import WeChatChannelStatusResponse, WeChatSelfCheckResponse
from app.services.wechat_notifier import wechat_notifier

router = APIRouter(prefix="/api/notify/wechat", tags=["notify"])


def _status_response() -> WeChatChannelStatusResponse:
    snap = wechat_notifier.snapshot()
    return WeChatChannelStatusResponse(
        status=snap.status,
        configured=snap.configured,
        missing_fields=snap.missing_fields,
        last_error=snap.last_error,
        last_error_category=snap.last_error_category,
        last_errcode=snap.last_errcode,
        corp_id_masked=snap.corp_id_masked,
        agent_id=snap.agent_id,
        to_user_masked=snap.to_user_masked,
        available=wechat_notifier.is_available,
    )


@router.get("/status", response_model=WeChatChannelStatusResponse)
def get_wechat_channel_status() -> WeChatChannelStatusResponse:
    """Return WeChat channel readiness without exposing secrets."""
    return _status_response()


@router.post("/self-check", response_model=WeChatSelfCheckResponse)
def run_wechat_self_check() -> WeChatSelfCheckResponse:
    """Run connectivity self-check (sends a test text message when configured)."""
    result = wechat_notifier.self_check()
    return WeChatSelfCheckResponse(
        ok=result.ok,
        status=wechat_notifier.status,
        message=result.message,
        errcode=result.errcode,
        errmsg=result.errmsg,
        invalid_user=result.invalid_user,
        category=result.category,
        channel=_status_response(),
    )
