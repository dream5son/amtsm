from app.services.position_math import (
    initial_stop_price,
    ratchet_stop,
    realized_pnl,
    stop_distance_pct,
    unrealized_pnl,
    weighted_avg_cost,
)


def test_weighted_avg_cost_open_and_addon() -> None:
    assert weighted_avg_cost(None, 0, 10.0, 100) == 10.0
    assert weighted_avg_cost(10.0, 100, 12.0, 100) == 11.0


def test_initial_stop_and_ratchet() -> None:
    stop0 = initial_stop_price(100.0, 0.08)
    assert abs(stop0 - 92.0) < 1e-9
    assert ratchet_stop(None, 92.0) == 92.0
    assert ratchet_stop(95.0, 92.0) == 95.0
    assert ratchet_stop(90.0, 92.0) == 92.0


def test_realized_and_unrealized_pnl() -> None:
    assert realized_pnl(110.0, 100.0, 100) == 1000.0
    amount, pct = unrealized_pnl(110.0, 100.0, 200)
    assert amount == 2000.0
    assert abs(pct - 0.1) < 1e-9
    assert unrealized_pnl(None, 100.0, 100) == (None, None)
    assert abs(stop_distance_pct(100.0, 92.0) - 0.08) < 1e-9
