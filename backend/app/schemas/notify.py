from pydantic import BaseModel, Field

from app.services.wechat_notifier import ChannelStatus, ErrorCategory


class WeChatChannelStatusResponse(BaseModel):
    status: ChannelStatus
    configured: bool
    missing_fields: list[str] = Field(default_factory=list)
    last_error: str | None = None
    last_error_category: ErrorCategory | None = None
    last_errcode: int | None = None
    corp_id_masked: str = ""
    agent_id: str = ""
    to_user_masked: str = ""
    available: bool = False


class WeChatSelfCheckResponse(BaseModel):
    ok: bool
    status: ChannelStatus
    message: str = ""
    errcode: int | None = None
    errmsg: str | None = None
    invalid_user: str | None = None
    category: ErrorCategory | None = None
    channel: WeChatChannelStatusResponse


class WeChatSendRequest(BaseModel):
    to_user: str
    content: str = "AMTSM 测试消息"


class WeChatSendResponse(BaseModel):
    ok: bool
    status: ChannelStatus
    message: str = ""
    errcode: int | None = None
    errmsg: str | None = None
    invalid_user: str | None = None
    category: ErrorCategory | None = None
    channel: WeChatChannelStatusResponse
