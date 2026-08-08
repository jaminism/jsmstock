import pandas as pd

from rich_stock.config import SRConfig
from rich_stock.strategies.qualitative import compute_qualitative_score


def make_df(closes, highs=None):
    n = len(closes)
    dates = pd.bdate_range("2024-01-02", periods=n)
    highs = highs or list(closes)
    df = pd.DataFrame({"High": highs, "Close": closes}, index=dates)
    return df


def test_new_high_120_true_when_breaking_out():
    # 최근 130일간 완만하게 오르다가 마지막날 신고가 경신
    closes = [1000 + i * 2 for i in range(130)] + [2000]
    df = make_df(closes)
    config = SRConfig()
    q = compute_qualitative_score(df, ul_index=len(df) - 1, prior_ul_index=None, config=config)
    assert q.new_high_120 is True


def test_no_resistance_true_when_price_jumps_into_uncharted_territory():
    # 120일 신고가 override에 걸리지 않도록: 80일 전(120일 lookback 안, 60일 lookback 밖)에
    # 상한가 당일 고가(3000)보다 높은 스파이크(3500)를 하나 심어 new_high_120=False로 만들고,
    # 최근 60일 안에는 3000의 85%(2550)에도 못 미치는 저항만 있게 구성한다.
    closes = [1000] * 50 + [3500] + [1000] * 79 + [3000]  # index50=스파이크, index130=UL
    df = make_df(closes)
    config = SRConfig()
    q = compute_qualitative_score(df, ul_index=130, prior_ul_index=None, config=config)
    assert q.new_high_120 is False
    assert q.no_resistance is True
    assert q.breakdown.get("no_resistance") == config.no_resistance_penalty


def test_no_resistance_false_when_prior_high_nearby():
    # 최근 60일 안에 이미 2900원까지 가본 적 있음 (상한가 3000원의 85% 이상)
    closes = [1000] * 55 + [2900] + [1500] * 4 + [3000]
    df = make_df(closes)
    config = SRConfig()
    q = compute_qualitative_score(df, ul_index=60, prior_ul_index=None, config=config)
    assert q.no_resistance is False


def test_long_n_shape_true_for_gradual_multiday_climb():
    # 판정 대상 5일 구간(index1~5) 자체가 완만하게(하루 최대 상승폭이 크지 않게) 누적 15%+ 상승
    base = [1000, 1050, 1100, 1150, 1200, 1250]
    df = make_df(base + [1600])
    config = SRConfig()
    q = compute_qualitative_score(df, ul_index=6, prior_ul_index=None, config=config)
    assert q.long_n_shape is True


def test_long_n_shape_false_for_flat_then_single_spike():
    # 4일간 거의 안 움직이다가 상한가 직전 하루에만 급등 -> "짧은N자"(긴N자 아님)
    base = [1000, 1001, 1002, 1003, 1004, 1300]
    df = make_df(base + [1690])
    config = SRConfig()
    q = compute_qualitative_score(df, ul_index=6, prior_ul_index=None, config=config)
    assert q.long_n_shape is False


def test_new_high_120_overrides_long_n_and_no_resistance_penalties():
    # 완만한 5일 상승(긴N자 조건 충족) + 최근 60일 고점과 크게 괴리(무공방 조건 충족)이지만
    # 동시에 130일 신고가이기도 하면 두 감점 모두 취소되어야 한다.
    closes = [500] * 60 + [1000, 1050, 1100, 1150, 1200, 1250] + [1600]
    df = make_df(closes)
    config = SRConfig()
    ul_index = len(df) - 1
    q = compute_qualitative_score(df, ul_index=ul_index, prior_ul_index=None, config=config)
    assert q.new_high_120 is True
    assert q.long_n_shape is True  # 조건 자체는 감지되지만
    assert q.no_resistance is True
    assert "long_n_shape" not in q.breakdown  # 신고가 override로 감점에는 반영 안 됨
    assert "no_resistance" not in q.breakdown
    assert q.score == 0


def test_pre_rebound_true_when_recent_prior_ul_within_window():
    df = make_df([1000] * 20 + [1500])
    config = SRConfig(pre_rebound_lookback_days=15)
    q = compute_qualitative_score(df, ul_index=20, prior_ul_index=10, config=config)
    assert q.pre_rebound is True
    assert q.breakdown.get("pre_rebound") == config.pre_rebound_penalty


def test_pre_rebound_false_when_prior_ul_too_old():
    df = make_df([1000] * 30 + [1500])
    config = SRConfig(pre_rebound_lookback_days=15)
    q = compute_qualitative_score(df, ul_index=30, prior_ul_index=10, config=config)
    assert q.pre_rebound is False


def test_score_zero_when_no_penalties_triggered():
    closes = [1000] * 130 + [1050]  # 완만한 신고가 아님(변화 거의 없음), 저항 충분, 선반등 없음
    df = make_df(closes)
    config = SRConfig()
    q = compute_qualitative_score(df, ul_index=len(df) - 1, prior_ul_index=None, config=config)
    assert q.score == 0
    assert q.breakdown == {}
