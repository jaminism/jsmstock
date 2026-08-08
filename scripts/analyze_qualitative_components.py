"""정성적 필터 구성요소별 개별 기여도 분석.

전체 유니버스 데이터를 한 번만 로드하고, 각 필터 구성요소(긴N자/무공방/선반등)를 개별적으로
켜서 어떤 항목이 실제로 성과에 도움이 되는지(또는 해가 되는지) 비교한다. 개별 페널티를 0으로
두면 detect_sr_signals 내부 판정(long_n_shape/no_resistance/pre_rebound)은 그대로 계산되지만
점수에는 반영되지 않아, 사실상 해당 항목만 "끈" 것과 같다.

사용 예:
    python scripts/analyze_qualitative_components.py --start 2021-01-01 --end 2024-12-31 --cache-dir .cache/ohlcv
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


def main() -> None:
    parser = argparse.ArgumentParser(description="정성적 필터 구성요소별 기여도 분석")
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

    print(f"[analyze] 유니버스 {len(tickers)}종목 로딩 중 (캐시 활용)...")
    ohlcv = load_universe_ohlcv(tickers, args.start, args.end, cache_dir=args.cache_dir)
    print(f"[analyze] {len(ohlcv)}종목 확보. 구성요소별 백테스트 시작...\n")

    base = SRConfig()

    variants = {
        "baseline (필터 없음)": dataclasses.replace(base, qualitative_filter_enabled=False),
        "전체 필터 (긴N자+무공방+선반등)": dataclasses.replace(base, qualitative_filter_enabled=True),
        "긴N자만": dataclasses.replace(
            base, qualitative_filter_enabled=True, no_resistance_penalty=0, pre_rebound_penalty=0
        ),
        "무공방만": dataclasses.replace(
            base, qualitative_filter_enabled=True, long_n_penalty=0, pre_rebound_penalty=0
        ),
        "선반등만": dataclasses.replace(
            base, qualitative_filter_enabled=True, long_n_penalty=0, no_resistance_penalty=0
        ),
    }

    header = f"{'구성':<28} {'신호수':>7} {'신호승률':>8} {'PF(신호)':>9} {'실행건':>6} {'승률':>7} {'CAGR':>8} {'MDD':>8}"
    print(header)
    print("-" * len(header))

    rows = []
    for name, config in variants.items():
        result = run_sr_backtest(ohlcv, config)
        m = result.metrics
        s = m.signal_level
        row = (
            f"{name:<28} {s.n_trades:>7} {s.win_rate:>7.1%} {s.profit_factor:>9.2f} "
            f"{m.n_trades:>6} {m.win_rate:>6.1%} {m.cagr_pct:>7.2f}% {m.max_drawdown_pct:>7.2f}%"
        )
        print(row)
        rows.append((name, s, m))

    print("\n(참고) '신호수/신호승률/PF(신호)'는 자본 제약 없이 전체 신호 기준, ")
    print("'실행건/승률/CAGR/MDD'는 자본 1억원·동시보유10종목·포지션당3% 제약 하 포트폴리오 기준.")


if __name__ == "__main__":
    main()
