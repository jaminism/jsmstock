"""SR(상한가리바운딩) 전략 백테스트 실행 CLI.

사전에 scripts/fetch_data.py 로 데이터를 캐시해두면 재실행이 빠르다 (미캐시 종목은
자동으로 다운로드한다).

사용 예:
    python scripts/run_sr_backtest.py --start 2022-01-01 --end 2024-12-31 --min-market-cap 300000000000 --limit 200
"""

from __future__ import annotations

import argparse
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich_stock import db
from rich_stock.backtest.engine import run_sr_backtest
from rich_stock.config import SRConfig
from rich_stock.data.loader import load_universe_ohlcv
from rich_stock.data.universe import get_universe
from rich_stock.strategies.sr import detect_sr_signals


def main() -> None:
    parser = argparse.ArgumentParser(description="SR 전략 백테스트")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--min-market-cap", type=float, default=300_000_000_000)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cache-dir", default=".cache/ohlcv")
    parser.add_argument("--initial-capital", type=float, default=100_000_000)
    parser.add_argument("--position-size-pct", type=float, default=0.03)
    parser.add_argument("--max-concurrent", type=int, default=10)
    parser.add_argument("--min-trading-value", type=float, default=50_000_000_000)
    parser.add_argument(
        "--qualitative-filter",
        action="store_true",
        help="정성적 배제 필터 근사치(무공방/긴N자/120일신고가 override/선반등 점수제)를 적용한다. "
        "기본값은 미적용(순수 정량 규칙만)이며, research/step_0_공통자료.md §3~5,7 기반 근사치다.",
    )
    parser.add_argument("--slippage-pct", type=float, default=0.0, help="편도 슬리피지(예: 0.003=왕복 약 0.6%). 기본값 0(끔).")
    parser.add_argument("--equity-csv", default=None, help="자산곡선을 CSV로 저장할 경로(선택)")
    parser.add_argument("--trades-csv", default=None, help="개별 트레이드 내역을 CSV로 저장할 경로(선택)")
    parser.add_argument(
        "--db", default=db.DEFAULT_DB_PATH,
        help=f"신호·트레이드를 DuckDB에 저장할 경로. 빈 문자열(--db \"\")이면 저장 안 함. 기본값 {db.DEFAULT_DB_PATH}",
    )
    args = parser.parse_args()

    universe = get_universe(min_market_cap=args.min_market_cap)
    tickers = universe["Code"].tolist()
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"[run_sr_backtest] 유니버스 {len(tickers)}종목 로딩 중...")
    ohlcv = load_universe_ohlcv(tickers, args.start, args.end, cache_dir=args.cache_dir)
    print(f"[run_sr_backtest] {len(ohlcv)}종목 데이터 확보 완료. 백테스트 시작...")

    config = SRConfig(
        min_trading_value_krw=args.min_trading_value,
        position_size_pct=args.position_size_pct,
        addon_size_pct=args.position_size_pct,
        max_position_pct=args.position_size_pct * 2,
        max_concurrent_positions=args.max_concurrent,
        initial_capital_krw=args.initial_capital,
        qualitative_filter_enabled=args.qualitative_filter,
        slippage_pct=args.slippage_pct,
    )

    result = run_sr_backtest(ohlcv, config)
    m = result.metrics

    s = m.signal_level
    filter_label = "적용" if args.qualitative_filter else "미적용(순수 정량 규칙)"
    print("\n=== SR 백테스트 결과 ===")
    print(f"정성적 필터: {filter_label}")
    print(f"기간: {args.start} ~ {args.end}  |  유니버스: {len(ohlcv)}종목  |  초기자본: {args.initial_capital:,.0f}원")
    print(f"총 신호(진입까지 발생한 트레이드): {len(result.trades)}건")
    print(f"자본/슬롯 제약으로 스킵: {m.n_skipped_capital_limit}건")

    print("\n--- (A) 신호 자체 품질: 자본 제약 없이 모든 신호를 다 받았다면 ---")
    print(f"신호 수: {s.n_trades}건 (승 {s.n_wins} / 패 {s.n_losses})  |  승률: {s.win_rate:.1%}")
    print(f"평균 수익률(건당): {s.avg_return_pct:.2f}%  |  손익비: {s.avg_win_loss_ratio:.2f}  |  Profit Factor: {s.profit_factor:.2f}")
    print(f"청산 사유 분포: {s.exit_reason_counts}")

    print(f"\n--- (B) 실제 계좌 성과: 자본 {args.initial_capital:,.0f}원 / 동시보유 {args.max_concurrent}종목 / 포지션당 {args.position_size_pct:.0%} 제약 하 ---")
    print(f"실행된 트레이드: {m.n_trades}건 (승 {m.n_wins} / 패 {m.n_losses})")
    print(f"승률: {m.win_rate:.1%}")
    print(f"평균 수익률(건당): {m.avg_return_pct:.2f}%")
    print(f"손익비(평균이익/평균손실): {m.avg_win_loss_ratio:.2f}")
    print(f"Profit Factor(총이익/총손실): {m.profit_factor:.2f}")
    print(f"최대낙폭(MDD): {m.max_drawdown_pct:.2f}%")
    print(f"샤프비율(연율화): {m.sharpe_ratio:.2f}")
    print(f"CAGR: {m.cagr_pct:.2f}%")
    print(f"청산 사유 분포: {m.exit_reason_counts}")

    if not result.portfolio.equity_curve.empty:
        start_eq = result.portfolio.equity_curve.iloc[0]
        end_eq = result.portfolio.equity_curve.iloc[-1]
        print(f"자산: {start_eq:,.0f}원 -> {end_eq:,.0f}원 ({(end_eq / start_eq - 1) * 100:.2f}%)")

    if args.equity_csv:
        result.portfolio.equity_curve.to_csv(args.equity_csv, header=["equity"])
        print(f"자산곡선 저장: {args.equity_csv}")

    if args.trades_csv:
        db.trades_to_dataframe(result.trades).to_csv(args.trades_csv, index=False, encoding="utf-8-sig")
        print(f"트레이드 내역 저장: {args.trades_csv}")

    if args.db:
        run_id = db.save_backtest_results(
            args.db, "SR", args.start, args.end, config, ohlcv, result.trades,
            detect_sr_signals, trades_csv_path=args.trades_csv,
        )
        print(f"DB 저장 완료: run_id={run_id}  db={args.db}")

    filter_note = (
        "정성적 배제 필터(무공방/긴N자/120일신고가 override/선반등)를 적용했지만 이 필터들은 "
        "원 자료의 단일 사례에서 역산한 근사 가중치이며 검증된 최적값이 아닙니다. 동테마 "
        "후발주(1등주/2등주) 판정은 정량 기준이 없어 여전히 미구현입니다."
        if args.qualitative_filter
        else "정성적 배제 필터(무공방/긴N자/동테마 후발주 등) 없이 정량 규칙만으로 생성한 신호의 기저 성과입니다."
    )
    print(
        f"\n⚠️ 본 결과는 {filter_note} 근사 거래대금·생존편향 유니버스 등 알려진 한계도 있습니다. "
        "실거래 판단의 근거로 단독 사용하지 마십시오."
    )


if __name__ == "__main__":
    main()
