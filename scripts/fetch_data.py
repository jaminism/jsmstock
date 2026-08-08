"""S1 백테스트용 로컬 데이터 캐시를 구축한다.

사용 예:
    python scripts/fetch_data.py --start 2022-01-01 --end 2024-12-31 --min-market-cap 300000000000 --limit 300
"""

from __future__ import annotations

import argparse
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich_stock.data.loader import load_universe_ohlcv
from rich_stock.data.universe import get_universe


def main() -> None:
    parser = argparse.ArgumentParser(description="KOSPI/KOSDAQ 일봉 데이터를 로컬에 캐시한다.")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=300_000_000_000,
        help="유니버스 시가총액 하한(원). 기본 3000억원 — 상한가 발생 시 500억원 거래대금 필터를 " "충족할 가능성이 낮은 초소형주를 사전에 배제해 다운로드량을 줄인다.",
    )
    parser.add_argument("--limit", type=int, default=None, help="시가총액 상위 N종목만 (테스트용)")
    parser.add_argument("--cache-dir", default=".cache/ohlcv")
    args = parser.parse_args()

    universe = get_universe(min_market_cap=args.min_market_cap)
    tickers = universe["Code"].tolist()
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"[fetch_data] 유니버스 {len(tickers)}종목, 기간 {args.start}~{args.end}")

    def on_progress(i: int, total: int, ticker: str, error: Exception | None) -> None:
        status = f"ERROR: {error}" if error else "OK"
        if i % 25 == 0 or error:
            print(f"  [{i}/{total}] {ticker} - {status}", file=sys.stderr)

    result = load_universe_ohlcv(tickers, args.start, args.end, cache_dir=args.cache_dir, on_progress=on_progress)
    print(f"[fetch_data] 완료: {len(result)}/{len(tickers)}종목 캐시됨 -> {args.cache_dir}")


if __name__ == "__main__":
    main()
