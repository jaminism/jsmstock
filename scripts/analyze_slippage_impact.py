"""전 기법(S1/S2/S3/S2+/S3+) 슬리피지 민감도 일괄 분석.

[[project-kplus-backtest-engine]]에서 S3+의 CAGR+68%가 "장중 터치=정확히 그 가격 체결"이라는
낙관적 가정과 고빈도 거래(평균 보유 1.8일, 연 1000건+)의 복리효과로 부풀려졌음을 확인했다.
이 스크립트는 backtest/engine.py에 새로 추가한 PortfolioConfig.slippage_pct를 0%부터 스윕해
5개 기법 모두에서 CAGR/승률/샤프가 얼마나 깨지는지 한 번에 비교한다 — 데이터는 한 번만 로드하고
config만 바꿔가며 재사용하는 기존 analyze_*.py 스크립트들과 동일한 패턴.

사용 예:
    python scripts/analyze_slippage_impact.py --start 2021-01-01 --end 2024-12-31 --cache-dir .cache/ohlcv
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich_stock.backtest.engine import (
    run_s1_backtest,
    run_s2_backtest,
    run_s3_backtest,
    run_s4_backtest,
    run_s5_backtest,
)
from rich_stock.config import S1Config, S2Config, S3Config, S4Config, S5Config
from rich_stock.data.loader import load_universe_ohlcv
from rich_stock.data.universe import get_universe

SLIPPAGE_LEVELS = [0.0, 0.001, 0.003, 0.005, 0.01, 0.02]

TECHNIQUES = [
    ("S1", run_s1_backtest, S1Config()),
    ("S2", run_s2_backtest, S2Config()),
    ("S3", run_s3_backtest, S3Config()),
    ("S4", run_s4_backtest, S4Config()),
    ("S5", run_s5_backtest, S5Config()),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="전 기법 슬리피지 민감도 분석")
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

    print(f"[slippage-impact] 유니버스 {len(tickers)}종목 로딩 중 (캐시 활용)...")
    ohlcv = load_universe_ohlcv(tickers, args.start, args.end, cache_dir=args.cache_dir)
    print(f"[slippage-impact] {len(ohlcv)}종목 확보. 기법 x 슬리피지 스윕 시작...\n")

    header = f"{'기법':<5} {'slippage':>9} {'실행건':>7} {'승률':>7} {'CAGR':>9} {'MDD':>8} {'샤프':>7}"
    print(header)
    print("-" * len(header))
    for name, runner, base_config in TECHNIQUES:
        for slip in SLIPPAGE_LEVELS:
            config = dataclasses.replace(base_config, slippage_pct=slip)
            result = runner(ohlcv, config)
            m = result.metrics
            print(
                f"{name:<5} {slip:>9.3%} {m.n_trades:>7} {m.win_rate:>6.1%} "
                f"{m.cagr_pct:>8.2f}% {m.max_drawdown_pct:>7.2f}% {m.sharpe_ratio:>7.2f}"
            )
        print()


if __name__ == "__main__":
    main()
