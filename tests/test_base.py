from rich_stock.strategies.base import Bracket, evaluate_bracket


def _bracket(stop=90, target=110, max_days=4, safety=False) -> Bracket:
    return Bracket(
        stop_price=stop, target_price=target, max_hold_trading_days=max_days,
        stop_reason="test-stop", target_reason="test-target", is_safety_override=safety,
    )


def test_evaluate_bracket_holds_when_price_between_stop_and_target():
    assert evaluate_bracket(_bracket(), current_price=100, trading_days_held=1) == "hold"


def test_evaluate_bracket_exits_stop_when_price_at_or_below_stop():
    assert evaluate_bracket(_bracket(stop=90), current_price=90, trading_days_held=1) == "exit_stop"
    assert evaluate_bracket(_bracket(stop=90), current_price=85, trading_days_held=1) == "exit_stop"


def test_evaluate_bracket_exits_target_when_price_at_or_above_target():
    assert evaluate_bracket(_bracket(target=110), current_price=110, trading_days_held=1) == "exit_target"
    assert evaluate_bracket(_bracket(target=110), current_price=115, trading_days_held=1) == "exit_target"


def test_evaluate_bracket_prefers_stop_when_both_triggered_same_poll():
    # 손절가(90)보다 낮으면서 동시에 극단적으로 낮은 target을 설정해 둘 다 만족하는 경우를 재현
    bracket = _bracket(stop=90, target=80)
    assert evaluate_bracket(bracket, current_price=85, trading_days_held=1) == "exit_stop"


def test_evaluate_bracket_exits_forced_hold_when_days_reached():
    bracket = _bracket(stop=90, target=110, max_days=4)
    assert evaluate_bracket(bracket, current_price=100, trading_days_held=3) == "hold"
    assert evaluate_bracket(bracket, current_price=100, trading_days_held=4) == "exit_forced_hold"
    assert evaluate_bracket(bracket, current_price=100, trading_days_held=5) == "exit_forced_hold"


def test_evaluate_bracket_stop_or_target_takes_priority_over_forced_hold():
    bracket = _bracket(stop=90, target=110, max_days=4)
    assert evaluate_bracket(bracket, current_price=89, trading_days_held=4) == "exit_stop"
    assert evaluate_bracket(bracket, current_price=111, trading_days_held=4) == "exit_target"


def test_evaluate_bracket_none_stop_never_triggers_stop_exit():
    # S6처럼 손절 자체가 없는 경우(안전장치 없이 순수하게) — 아무리 가격이 내려가도 exit_stop 안 남
    bracket = Bracket(stop_price=None, target_price=None, max_hold_trading_days=None,
                       stop_reason="없음", target_reason="단계별", is_safety_override=False)
    assert evaluate_bracket(bracket, current_price=1, trading_days_held=100) == "hold"


def test_evaluate_bracket_safety_override_flag_is_just_metadata():
    bracket = _bracket(stop=90, safety=True)
    assert bracket.is_safety_override is True
    assert evaluate_bracket(bracket, current_price=85, trading_days_held=1) == "exit_stop"
