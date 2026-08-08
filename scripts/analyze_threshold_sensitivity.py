"""정성적 필터 주요 임계값 민감도 분석.

한 번에 하나의 파라미터만 바꿔가며(one-at-a-time) 나머지는 기본값(전체 필터 켠 상태)으로 고정하고
성과 변화를 관찰한다. 전수 그리드서치가 아니라 "이 값을 조금 바꾸면 결과가 뒤집히는가?"를 보는
빠른 진단용이다 — 결과가 특정 값 근처에서 크게 출렁이면 그 파라미터는 근거가 약하다는 신호다.

사용 예:
    python scripts/analyze_threshold_sensitivity.py --start 2021-01-01 --end 2024-12-31 --cache-dir .cache/ohlcv
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich_stock.backtest.engine import run_sr_backtest
from rich_stock.config import SRConfig
from rich_stock.data.loader import load_universe_ohlcv
from rich_stock.data.universe import get_universe


def _print_sweep(title: str, ohlcv, base: SRConfig, field: str, values: list) -> None:
    header = f"{field}={'값':<8} {'신호수':>7} {'신호승률':>8} {'PF(신호)':>9} {'실행건':>6} {'승률':>7} {'CAGR':>8} {'MDD':>8}"
    print(f"\n### {title} ###")
    print(header)
    print("-" * len(header))
    for v in values:
        config = dataclasses.replace(base, **{field: v})
        result = run_sr_backtest(ohlcv, config)
        m = result.metrics
        s = m.signal_level
        print(
            f"{field}={v!s:<8} {s.n_trades:>7} {s.win_rate:>7.1%} {s.profit_factor:>9.2f} "
            f"{m.n_trades:>6} {m.win_rate:>6.1%} {m.cagr_pct:>7.2f}% {m.max_drawdown_pct:>7.2f}%"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="정성적 필터 임계값 민감도 분석")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--min-market-cap", type=float, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cache-dir", default=".cache/ohlcv")
    args = parser.parse_args()

    universe = get_universe(min_market_cap=args.min_market_cap)
    tickers = universe["Code"].tolist()
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"[sensitivity] 유니버스 {len(tickers)}종목 로딩 중 (캐시 활용)...")
    ohlcv = load_universe_ohlcv(tickers, args.start, args.end, cache_dir=args.cache_dir)
    print(f"[sensitivity] {len(ohlcv)}종목 확보. 임계값 스윕 시작...")

    base = SRConfig(qualitative_filter_enabled=True)

    _print_sweep(
        "무공방 판정 비율(no_resistance_price_ratio) — 낮을수록 엄격(더 많이 배제)",
        ohlcv, base, "no_resistance_price_ratio", [0.70, 0.80, 0.85, 0.90, 0.95],
    )
    _print_sweep(
        "선반등 lookback(pre_rebound_lookback_days) — 클수록 엄격",
        ohlcv, base, "pre_rebound_lookback_days", [5, 10, 15, 30, 45],
    )
    _print_sweep(
        "필터 통과 점수 기준(qualitative_score_threshold) — 낮을수록 관대(더 많이 통과)",
        ohlcv, base, "qualitative_score_threshold", [-40, -30, -20, -10, 0, 10],
    )
    _print_sweep(
        "상한가 판정 등락률 임계값(limit_up_return_threshold) — step_0_공통자료.md §1: 강사 최종본=0.299",
        ohlcv, base, "limit_up_return_threshold", [0.290, 0.295, 0.299, 0.300],
    )


if __name__ == "__main__":
    main()
