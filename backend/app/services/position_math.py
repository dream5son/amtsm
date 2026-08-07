from __future__ import annotations


def weighted_avg_cost(
    old_cost: float | None, old_qty: int, buy_price: float, buy_qty: int
) -> float:
    if buy_qty <= 0:
        raise ValueError("buy qty must be positive")
    if buy_price <= 0:
        raise ValueError("buy price must be positive")
    if old_qty < 0:
        raise ValueError("old qty must be non-negative")
    if old_qty == 0:
        return buy_price
    if old_cost is None or old_cost <= 0:
        raise ValueError("old cost must be positive when holding")
    return (old_cost * old_qty + buy_price * buy_qty) / (old_qty + buy_qty)


def initial_stop_price(avg_cost: float, stop_loss_pct: float) -> float:
    if avg_cost <= 0:
        raise ValueError("avg cost must be positive")
    if stop_loss_pct < 0 or stop_loss_pct >= 1:
        raise ValueError("stop_loss_pct out of range")
    return avg_cost * (1 - stop_loss_pct)


def ratchet_stop(prev_stop: float | None, new_stop0: float) -> float:
    prev = prev_stop or 0.0
    return max(prev, new_stop0)


def realized_pnl(sell_price: float, avg_cost: float, sell_qty: int) -> float:
    return (sell_price - avg_cost) * sell_qty


def unrealized_pnl(
    price: float | None, avg_cost: float | None, qty: int
) -> tuple[float | None, float | None]:
    if price is None or avg_cost is None or qty <= 0 or avg_cost <= 0:
        return None, None
    amount = (price - avg_cost) * qty
    pct = (price - avg_cost) / avg_cost
    return amount, pct


def stop_distance_pct(price: float | None, stop_price: float | None) -> float | None:
    if price is None or stop_price is None or price <= 0:
        return None
    return (price - stop_price) / price
