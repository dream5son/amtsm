"""Compatibility re-export for the WeChat notification channel.

New code should import from ``app.services.notifier``.
"""

from app.services.notifier.base import ChannelStatus, ErrorCategory, SendResult
from app.services.notifier.wechat import (
    REQUIRED_FIELDS,
    SELF_CHECK_CONTENT,
    WeChatChannelSnapshot,
    WeChatNotifier,
    wechat_notifier,
)

ChannelSnapshot = WeChatChannelSnapshot

__all__ = [
    "REQUIRED_FIELDS",
    "SELF_CHECK_CONTENT",
    "ChannelSnapshot",
    "ChannelStatus",
    "ErrorCategory",
    "SendResult",
    "WeChatNotifier",
    "wechat_notifier",
]
