"""Resolve enabled text-notification channels from NOTIFY_CHANNELS."""

from __future__ import annotations

import logging

from app.config import settings
from app.services.notifier.base import TextNotifier
from app.services.notifier.email_notifier import email_notifier
from app.services.notifier.wechat import wechat_notifier

logger = logging.getLogger(__name__)

KNOWN_CHANNEL_IDS = ("wechat", "email")


def parse_notify_channels(raw: str | None = None) -> list[str]:
    text = settings.notify_channels if raw is None else raw
    seen: set[str] = set()
    ordered: list[str] = []
    for part in (text or "").split(","):
        channel_id = part.strip().lower()
        if not channel_id or channel_id in seen:
            continue
        seen.add(channel_id)
        ordered.append(channel_id)
    return ordered


def get_notifier(channel_id: str) -> TextNotifier | None:
    if channel_id == "wechat":
        return wechat_notifier
    if channel_id == "email":
        return email_notifier
    return None


def get_enabled_notifiers() -> list[TextNotifier]:
    """Return process-local singletons for known ids in NOTIFY_CHANNELS."""
    notifiers: list[TextNotifier] = []
    for channel_id in parse_notify_channels():
        notifier = get_notifier(channel_id)
        if notifier is None:
            logger.error("Unknown notify channel in NOTIFY_CHANNELS: %s", channel_id)
            continue
        notifiers.append(notifier)
    return notifiers


def get_unknown_channel_ids() -> list[str]:
    return [
        channel_id
        for channel_id in parse_notify_channels()
        if get_notifier(channel_id) is None
    ]


def resolve_alert_channels() -> list[tuple[str, TextNotifier | None]]:
    """Return (channel_id, notifier) for each NOTIFY_CHANNELS entry.

    Unknown ids keep ``notifier is None`` so callers can fail that entry
    instead of silently skipping it.
    """
    resolved: list[tuple[str, TextNotifier | None]] = []
    for channel_id in parse_notify_channels():
        resolved.append((channel_id, get_notifier(channel_id)))
    return resolved
