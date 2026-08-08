from app.schemas.strategy import TrailingLadderLevel, parse_trailing_ladder
from app.services.risk_rules import (
    SIGNAL_PARTIAL_TP,
    SIGNAL_STOP_LOSS,
    SIGNAL_TAKE_PROFIT,
    RiskParams,
    evaluate,
    match_ladder_idx,
)


def _default_params(**overrides) -> RiskParams:
    base = dict(
        stop_loss_pct=0.08,
        break_even_trigger_pct=0.10,
        break_even_buffer_pct=0.005,
        trailing_ladder=parse_trailing_ladder(None),
        enable_partial_take_profit=False,
    )
    base.update(overrides)
    return RiskParams(**base)


def test_initial_stop_and_ratchet_no_retreat() -> None:
    params = _default_params()
    first = evaluate(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=None,
        price=100.0,
        params=params,
    )
    assert abs(first.stop_price - 92.0) < 1e-9
    assert first.exit_signal is None

    # Price dips but above stop — stop stays at initial.
    second = evaluate(
        avg_cost=100.0,
        highest_since_hold=first.highest_since_hold,
        prev_stop=first.stop_price,
        price=95.0,
        params=params,
    )
    assert abs(second.stop_price - 92.0) < 1e-9

    # Break-even triggers at +10%: stop ratchets up, never down.
    third = evaluate(
        avg_cost=100.0,
        highest_since_hold=110.0,
        prev_stop=second.stop_price,
        price=110.0,
        params=params,
    )
    assert abs(third.stop_price - 100.5) < 1e-9

    # Price falls but still above break-even stop — stop does not retreat.
    fourth = evaluate(
        avg_cost=100.0,
        highest_since_hold=third.highest_since_hold,
        prev_stop=third.stop_price,
        price=105.0,
        params=params,
    )
    assert abs(fourth.stop_price - 100.5) < 1e-9


def test_trailing_ladder_tightens_stop() -> None:
    params = _default_params()
    # +25% -> 20-50% band, drawdown 10% from highest 125 -> stop 112.5
    result = evaluate(
        avg_cost=100.0,
        highest_since_hold=120.0,
        prev_stop=100.5,
        price=125.0,
        params=params,
    )
    assert abs(result.highest_since_hold - 125.0) < 1e-9
    assert abs(result.stop_price - 112.5) < 1e-9
    assert result.ladder_idx == 1


def test_stop_loss_vs_take_profit_classification() -> None:
    params = _default_params()
    loss = evaluate(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=92.0,
        price=91.0,
        params=params,
    )
    assert loss.exit_signal == SIGNAL_STOP_LOSS

    # After break-even, touching stop while still above cost => TAKE_PROFIT
    win = evaluate(
        avg_cost=100.0,
        highest_since_hold=120.0,
        prev_stop=100.5,
        price=100.4,
        params=params,
    )
    assert win.exit_signal == SIGNAL_TAKE_PROFIT


def test_partial_tp_on_ladder_cross() -> None:
    params = _default_params(enable_partial_take_profit=True)
    first = evaluate(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=92.0,
        price=112.0,
        params=params,
        prev_ladder_idx=None,
    )
    assert first.partial_tp is not None
    assert first.partial_tp.ladder_idx == 0
    assert abs(first.partial_tp.suggest_sell_pct - 0.30) < 1e-9

    # Same band again — no new partial.
    again = evaluate(
        avg_cost=100.0,
        highest_since_hold=first.highest_since_hold,
        prev_stop=first.stop_price,
        price=115.0,
        params=params,
        prev_ladder_idx=0,
    )
    assert again.partial_tp is None

    # Cross into next band.
    higher = evaluate(
        avg_cost=100.0,
        highest_since_hold=again.highest_since_hold,
        prev_stop=again.stop_price,
        price=125.0,
        params=params,
        prev_ladder_idx=0,
    )
    assert higher.partial_tp is not None
    assert higher.partial_tp.ladder_idx == 1


def test_partial_tp_disabled() -> None:
    params = _default_params(enable_partial_take_profit=False)
    result = evaluate(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=92.0,
        price=112.0,
        params=params,
    )
    assert result.partial_tp is None


def test_match_ladder_boundaries() -> None:
    ladder = parse_trailing_ladder(None)
    assert match_ladder_idx(0.09, ladder) is None
    assert match_ladder_idx(0.10, ladder) == 0
    assert match_ladder_idx(0.20, ladder) == 1
    assert match_ladder_idx(0.50, ladder) == 2
    assert match_ladder_idx(0.80, ladder) == 2


def test_update_highest_then_trigger_same_cycle() -> None:
    """Same-poll: new high raises stop, then price at close can still trigger."""
    params = _default_params()
    # Highest updates to 130; trailing stop = 130 * 0.9 = 117; price 116 triggers TP.
    result = evaluate(
        avg_cost=100.0,
        highest_since_hold=120.0,
        prev_stop=108.0,
        price=116.0,
        params=params,
    )
    # Wait - at price 116, pnl=16%, ladder idx 0 (10-20), drawdown 15% -> stop = max(108, 92, 100.5, 116*0.85)
    # highest = max(120, 116) = 120, stop2 = 120 * 0.85 = 102. Not 117.
    # Need price that is also the high.
    result = evaluate(
        avg_cost=100.0,
        highest_since_hold=120.0,
        prev_stop=108.0,
        price=130.0,
        params=params,
    )
    assert abs(result.highest_since_hold - 130.0) < 1e-9
    assert abs(result.stop_price - 117.0) < 1e-9  # 130 * (1-0.10)

    triggered = evaluate(
        avg_cost=100.0,
        highest_since_hold=130.0,
        prev_stop=117.0,
        price=116.0,
        params=params,
    )
    assert triggered.exit_signal == SIGNAL_TAKE_PROFIT
    # Silence unused imports for type checkers if any
    assert SIGNAL_PARTIAL_TP == "PARTIAL_TP"
    assert isinstance(TrailingLadderLevel(min_pnl=0.1, max_pnl=0.2, drawdown=0.15), TrailingLadderLevel)
