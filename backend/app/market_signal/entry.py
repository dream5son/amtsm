"""Buy / tech-sell / addon threshold rules shared by live engine and backtest."""

from __future__ import annotations


def buy_threshold(low_min: float, x: float) -> float:
    return float(low_min) * float(x)


def sell_threshold(high_max: float, y: float) -> float:
    return float(high_max) * float(y)


def is_buy_signal(
    price: float,
    low_min: float,
    x: float,
    *,
    actual_n: int | None = None,
    n: int | None = None,
    require_full_n: bool = False,
) -> bool:
    """True when ``price <= low_min * x``.

    When ``require_full_n`` is True (backtest), also requires ``actual_n == n``.
    Live monitoring passes ``require_full_n=False`` so degraded windows are allowed.
    """
    if require_full_n:
        if actual_n is None or n is None:
            raise ValueError("actual_n and n are required when require_full_n=True")
        if actual_n != n:
            return False
    if price <= 0 or low_min <= 0 or x <= 0:
        return False
    # 1e-12：浮点运算误差容差，避免 price 与阈值在数学上相等却因精度差导致比较失败
    return float(price) <= buy_threshold(low_min, x) + 1e-12


def is_tech_sell_signal(price: float, high_max: float, y: float) -> bool:
    """True when ``price >= high_max * y`` (technical sell / optional holding sell)."""
    if price <= 0 or high_max <= 0 or y <= 0:
        return False
    # 1e-12：同上，卖出侧从阈值减去容差，保证边界相等时仍能触发
    return float(price) >= sell_threshold(high_max, y) - 1e-12


def is_addon_signal(price: float, low_min: float, x: float) -> bool:
    """Addon reuses the buy threshold with a different signal_type at the call site."""
    return is_buy_signal(price, low_min, x, require_full_n=False)
