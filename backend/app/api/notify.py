from fastapi import APIRouter, HTTPException

from app.schemas.notify import (
    EmailChannelStatusResponse,
    EmailSelfCheckResponse,
    EmailSendRequest,
    EmailSendResponse,
    WeChatChannelStatusResponse,
    WeChatSelfCheckResponse,
    WeChatSendRequest,
    WeChatSendResponse,
)
from app.services.notifier.email_notifier import EmailChannelSnapshot, email_notifier
from app.services.wechat_notifier import wechat_notifier

router = APIRouter(prefix="/api/notify", tags=["notify"])
wechat_router = APIRouter(prefix="/wechat")
email_router = APIRouter(prefix="/email")


def _wechat_status_response() -> WeChatChannelStatusResponse:
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


@wechat_router.get("/status", response_model=WeChatChannelStatusResponse)
def get_wechat_channel_status() -> WeChatChannelStatusResponse:
    """Return WeChat channel readiness without exposing secrets."""
    return _wechat_status_response()


@wechat_router.post("/self-check", response_model=WeChatSelfCheckResponse)
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
        channel=_wechat_status_response(),
    )


@wechat_router.post("/send", response_model=WeChatSendResponse)
def send_wechat_test(payload: WeChatSendRequest) -> WeChatSendResponse:
    """Send a test text message to an explicit enterprise WeChat UserID."""
    to_user = payload.to_user.strip()
    content = payload.content.strip()
    if not to_user or not content:
        raise HTTPException(
            status_code=400, detail="to_user and content must not be empty"
        )

    result = wechat_notifier.send_text(content, to_user=to_user)
    return WeChatSendResponse(
        ok=result.ok,
        status=wechat_notifier.status,
        message=result.message,
        errcode=result.errcode,
        errmsg=result.errmsg,
        invalid_user=result.invalid_user,
        category=result.category,
        channel=_wechat_status_response(),
    )


def _email_status_response() -> EmailChannelStatusResponse:
    snap: EmailChannelSnapshot = email_notifier.snapshot()
    return EmailChannelStatusResponse(
        status=snap.status,
        configured=snap.configured,
        missing_fields=snap.missing_fields,
        last_error=snap.last_error,
        last_error_category=snap.last_error_category,
        last_errcode=snap.last_errcode,
        smtp_host=snap.smtp_host,
        smtp_port=snap.smtp_port,
        from_masked=snap.from_masked,
        to_masked=snap.to_masked,
        use_tls=snap.use_tls,
        use_ssl=snap.use_ssl,
        available=email_notifier.is_available,
    )


@email_router.get("/status", response_model=EmailChannelStatusResponse)
def get_email_channel_status() -> EmailChannelStatusResponse:
    """Return email channel readiness without exposing secrets."""
    return _email_status_response()


@email_router.post("/self-check", response_model=EmailSelfCheckResponse)
def run_email_self_check() -> EmailSelfCheckResponse:
    """Run connectivity self-check (sends a test email when configured)."""
    result = email_notifier.self_check()
    return EmailSelfCheckResponse(
        ok=result.ok,
        status=email_notifier.status,
        message=result.message,
        errcode=result.errcode,
        errmsg=result.errmsg,
        invalid_user=result.invalid_user,
        category=result.category,
        channel=_email_status_response(),
    )


@email_router.post("/send", response_model=EmailSendResponse)
def send_email_test(payload: EmailSendRequest) -> EmailSendResponse:
    """Send a test text email to an explicit recipient address."""
    to_user = payload.to_user.strip()
    content = payload.content.strip()
    if not to_user or not content:
        raise HTTPException(
            status_code=400, detail="to_user and content must not be empty"
        )

    result = email_notifier.send_text(content, to_user=to_user)
    return EmailSendResponse(
        ok=result.ok,
        status=email_notifier.status,
        message=result.message,
        errcode=result.errcode,
        errmsg=result.errmsg,
        invalid_user=result.invalid_user,
        category=result.category,
        channel=_email_status_response(),
    )


router.include_router(wechat_router)
router.include_router(email_router)
