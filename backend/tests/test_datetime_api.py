"""Tests for API datetime serialization (Asia/Shanghai)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.engine.market_hours import SH_TZ, to_api_iso


def test_to_api_iso_none() -> None:
    assert to_api_iso(None) is None


def test_to_api_iso_naive_utc_converts_to_shanghai() -> None:
    # 04:00 UTC → 12:00 Asia/Shanghai
    naive = datetime(2026, 8, 25, 4, 0, 0)
    assert to_api_iso(naive) == "2026-08-25T12:00:00+08:00"


def test_to_api_iso_aware_utc() -> None:
    aware = datetime(2026, 8, 25, 4, 0, 0, tzinfo=UTC)
    assert to_api_iso(aware) == "2026-08-25T12:00:00+08:00"


def test_to_api_iso_aware_shanghai() -> None:
    aware = datetime(2026, 8, 25, 12, 0, 0, tzinfo=SH_TZ)
    assert to_api_iso(aware) == "2026-08-25T12:00:00+08:00"


def test_to_api_iso_naive_as_shanghai() -> None:
    naive = datetime(2026, 8, 25, 12, 0, 0)
    assert to_api_iso(naive, naive_as=SH_TZ) == "2026-08-25T12:00:00+08:00"


def test_to_api_iso_other_offset() -> None:
    ny = ZoneInfo("America/New_York")
    aware = datetime(2026, 8, 25, 0, 0, 0, tzinfo=ny)  # EDT UTC-4 → 12:00 SH
    assert to_api_iso(aware) == "2026-08-25T12:00:00+08:00"
