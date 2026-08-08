import pandas as pd

from rich_stock.limits import is_limit_up_day, tick_size


def test_is_limit_up_day_true_case():
    assert is_limit_up_day(high=13000, close=13000, prev_close=10000, threshold=0.295) is True


def test_is_limit_up_day_false_when_high_gt_close():
    # 고가에서 마감하지 못함 (장중 상한가 찍고 밀림) -> "진짜" 상한가 아님
    assert is_limit_up_day(high=13000, close=12500, prev_close=10000, threshold=0.295) is False


def test_is_limit_up_day_false_when_return_below_threshold():
    assert is_limit_up_day(high=12000, close=12000, prev_close=10000, threshold=0.295) is False


def test_tick_size_legacy_vs_2023_regime():
    old_date = pd.Timestamp("2021-01-01")
    new_date = pd.Timestamp("2024-01-01")
    # 15,000원대: 개편 전에는 50원 단위(1만~5만 구간), 개편 후에는 10원 단위(5천~2만 구간)
    assert tick_size(15000, old_date) == 50
    assert tick_size(15000, new_date) == 10
