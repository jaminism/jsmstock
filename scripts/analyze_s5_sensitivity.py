"""S5 손익구조 파라미터 민감도 분석.

배경: 기본 파라미터(profit_target_pct=0.07, stop_loss_pct=-0.07)로 2021~2024 전종목 백테스트 시
CAGR+68%, 승률89%, 샤프9.39라는 비정상적으로 좋은 결과가 나왔다([[project-kplus-backtest-engine]]
참고). 세력봉 판정 자체가 큰 변동성(당일 변동폭 15%+)을 요구하는데 익절 목표가 겨우 +7%로 좁아,
"장중 저가/고가 터치=정확히 그 가격에 체결"이라는 기존 단순화의 낙관 편향이 증폭됐을 가능성을
검증한다. 목표가를 넓혀갈수록 CAGR/승률이 원문의 "+4%~+7% 구간" 근처와 크게 동떨어진 형태로
붕괴/변화한다면, 현재 결과가 진짜 신호 엣지가 아니라 "좁은 목표가+낙관적 체결가정" 조합의
산물이라는 정황 증거가 된다. analyze_threshold_sensitivity.py와 동일한 one-at-a-time 스윕 패턴.

사용 예:
    python scripts/analyze_kplus_sensitivity.py --start 2021-01-01 --end 2024-12-31 --cache-dir .cache/ohlcv
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich_stock.backtest.engine import run_s5_backtest
from rich_stock.config import S5Config
from rich_stock.data.loader import load_universe_ohlcv
from rich_stock.data.universe import get_universe


def _print_sweep(title: str, ohlcv, base: S5Config, field: str, values: list) -> None:
    header = (
        f"{field}={'값':<8} {'신호수':>7} {'신호승률':>8} {'PF(신호)':>9} "
        f"{'실행건':>6} {'승률':>7} {'CAGR':>9} {'MDD':>8} {'샤프':>6}"
    )
    print(f"\n### {title} ###")
    print(header)
    print("-" * len(header))
    for v in values:
        config = dataclasses.replace(base, **{field: v})
        result = run_s5_backtest(ohlcv, config)
        m = result.metrics
        s = m.signal_level
        print(
            f"{field}={v!s:<8} {s.n_trades:>7} {s.win_rate:>7.1%} {s.profit_factor:>9.2f} "
            f"{m.n_trades:>6} {m.win_rate:>6.1%} {m.cagr_pct:>8.2f}% {m.max_drawdown_pct:>7.2f}% {m.sharpe_ratio:>6.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="S5 민감도 분석")
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

    print(f"[kplus-sensitivity] 유니버스 {len(tickers)}종목 로딩 중 (캐시 활용)...")
    ohlcv = load_universe_ohlcv(tickers, args.start, args.end, cache_dir=args.cache_dir)
    print(f"[kplus-sensitivity] {len(ohlcv)}종목 확보. 스윕 시작...")

    base = S5Config()

    _print_sweep(
        "익절 목표(profit_target_pct) — 기본 0.07. 넓힐수록 '좁은 목표가 쉽게 걸린다' 가설을 검증",
        ohlcv, base, "profit_target_pct", [0.07, 0.10, 0.15, 0.20, 0.30, 0.50],
    )
    _print_sweep(
        "손절폭(stop_loss_pct) — 기본 -0.07",
        ohlcv, base, "stop_loss_pct", [-0.03, -0.05, -0.07, -0.10, -0.15, -0.20],
    )
    _print_sweep(
        "세력봉 변동폭 요건(candle_range_ratio) — 기본 1.15. 낮출수록(=완화) 세력봉이 덜 극단적이어도 신호",
        ohlcv, base, "candle_range_ratio", [1.05, 1.10, 1.15, 1.20, 1.30],
    )
    _print_sweep(
        "진입 유효기간(entry_valid_days) — 기본 4",
        ohlcv, base, "entry_valid_days", [1, 2, 4, 7, 10],
    )


if __name__ == "__main__":
    main()
