from app.schemas.strategy import (
    DEFAULT_BREAK_EVEN_BUFFER_PCT,
    DEFAULT_BREAK_EVEN_TRIGGER_PCT,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TRAILING_LADDER,
    LEGACY_TRAILING_LADDER,
    TrailingLadderLevel,
    parse_trailing_ladder,
)
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
        trailing_ladder=parse_trailing_ladder(LEGACY_TRAILING_LADDER),
        enable_partial_take_profit=False,
    )
    base.update(overrides)
    return RiskParams(**base)


def _growth_params(**overrides) -> RiskParams:
    base = dict(
        stop_loss_pct=DEFAULT_STOP_LOSS_PCT,
        break_even_trigger_pct=DEFAULT_BREAK_EVEN_TRIGGER_PCT,
        break_even_buffer_pct=DEFAULT_BREAK_EVEN_BUFFER_PCT,
        trailing_ladder=parse_trailing_ladder(DEFAULT_TRAILING_LADDER),
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

    # Break-even (+10%) also enters the first trailing band: lock 85% of the
    # 10 gain → 108.5 (price-based used to sit at the 100.5 break-even floor).
    third = evaluate(
        avg_cost=100.0,
        highest_since_hold=110.0,
        prev_stop=second.stop_price,
        price=110.0,
        params=params,
    )
    assert abs(third.stop_price - 108.5) < 1e-9

    # Price falls but still above the trailing stop — stop does not retreat.
    fourth = evaluate(
        avg_cost=100.0,
        highest_since_hold=third.highest_since_hold,
        prev_stop=third.stop_price,
        price=109.0,
        params=params,
    )
    assert abs(fourth.stop_price - 108.5) < 1e-9
    assert fourth.exit_signal is None


def test_trailing_ladder_tightens_stop() -> None:
    params = _default_params()
    # +25% -> 20-50% band, drawdown 10% of the 25 gain -> stop 122.5
    result = evaluate(
        avg_cost=100.0,
        highest_since_hold=120.0,
        prev_stop=100.5,
        price=125.0,
        params=params,
    )
    assert abs(result.highest_since_hold - 125.0) < 1e-9
    assert abs(result.stop_price - 122.5) < 1e-9
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
    assert match_ladder_idx(0.19, ladder) is None
    assert match_ladder_idx(0.20, ladder) == 0
    assert match_ladder_idx(0.50, ladder) == 1
    assert match_ladder_idx(0.80, ladder) == 2
    assert match_ladder_idx(0.90, ladder) == 2


def test_update_highest_then_trigger_same_cycle() -> None:
    """Same-poll: new high raises stop, then price at close can still trigger."""
    params = _default_params()
    result = evaluate(
        avg_cost=100.0,
        highest_since_hold=120.0,
        prev_stop=108.0,
        price=130.0,
        params=params,
    )
    assert abs(result.highest_since_hold - 130.0) < 1e-9
    # +30% in 20-50% band, dd 10% of the 30 gain -> stop 127
    assert abs(result.stop_price - 127.0) < 1e-9

    triggered = evaluate(
        avg_cost=100.0,
        highest_since_hold=130.0,
        prev_stop=127.0,
        price=116.0,
        params=params,
    )
    assert triggered.exit_signal == SIGNAL_TAKE_PROFIT
    # Silence unused imports for type checkers if any
    assert SIGNAL_PARTIAL_TP == "PARTIAL_TP"
    assert isinstance(TrailingLadderLevel(min_pnl=0.1, max_pnl=0.2, drawdown=0.15), TrailingLadderLevel)


def test_trailing_stop_locks_most_of_the_gain() -> None:
    """Default first band (+22% / dd 20%) locks 80% of the 22 gain → 117.6.

    Price-based trailing used to put the stop at 97.6 (below cost). Profit-based
    trailing never falls below cost once highest is above it.
    """
    params = _growth_params()
    result = evaluate(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=85.0,
        price=122.0,
        params=params,
    )
    assert abs(result.stop_price - 117.6) < 1e-9
    assert result.ladder_idx == 0
    assert result.exit_signal is None


def test_trailing_stop_above_cost_still_tightens() -> None:
    """Once trailing actually locks profit, the ladder still ratchets up."""
    params = _growth_params()
    result = evaluate(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=85.0,
        price=160.0,
        params=params,
    )
    # +60% in 50-80% band, dd 15% of the 60 gain -> stop 151
    assert abs(result.stop_price - 151.0) < 1e-9
    assert result.ladder_idx == 1
    assert result.exit_signal is None


def test_sh600660_peak_locks_most_of_the_gain() -> None:
    """Regression: 7.3424 in, high 10.068 (+37%) must lock ~+29.7%, not +9.7%."""
    params = _growth_params()
    result = evaluate(
        avg_cost=7.3424,
        highest_since_hold=7.3424,
        prev_stop=7.3424 * (1.0 - DEFAULT_STOP_LOSS_PCT),
        price=10.0680,
        params=params,
    )
    expected = 7.3424 + (10.0680 - 7.3424) * 0.80
    assert abs(result.stop_price - expected) < 1e-9
    assert abs(result.stop_price / 7.3424 - 1.0 - 0.297) < 0.002
    assert result.ladder_idx == 0
    assert result.exit_signal is None
