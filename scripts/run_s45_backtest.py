"""S45 전략(S4/S5) 백테스트 실행 CLI.

사전에 scripts/fetch_data.py 로 데이터를 캐시해두면 재실행이 빠르다 (미캐시 종목은
자동으로 다운로드한다). S45는 "세력봉"(power candle) 이벤트를 트리거로 쓴다는 점이 원조
S1/S2/S3(상한가 트리거)와 다르다 — 세력봉 판정식의 실제 파라미터(세력봉대금 등)가 원문에
없어 500억원 거래대금 기준으로 근사했다. S4(종가베팅, 0.236)와 S5(장중매매, 0.5)
중 하나를 --variant 로 선택한다. 자세한 근사·단순화 내역은 S45BaseConfig 참고.

사용 예:
    python scripts/run_kplus_backtest.py --variant s4 --start 2021-01-01 --end 2024-12-31 --min-market-cap 0 --cache-dir .cache/ohlcv
    python scripts/run_kplus_backtest.py --variant s5 --start 2021-01-01 --end 2024-12-31 --min-market-cap 0 --cache-dir .cache/ohlcv
"""

from __future__ import annotations

import argparse
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich_stock import db
from rich_stock.backtest.engine import run_s4_backtest, run_s5_backtest
from rich_stock.config import S4Config, S5Config
from rich_stock.data.loader import load_universe_ohlcv
from rich_stock.data.universe import get_universe
from rich_stock.strategies.s45 import detect_s45_signals


def main() -> None:
    parser = argparse.ArgumentParser(description="S45 전략(S4/S5) 백테스트")
    parser.add_argument("--variant", choices=["s4", "s5"], required=True, help="s4=S4(종가베팅), s5=S5(장중매매)")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--min-market-cap", type=float, default=300_000_000_000)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cache-dir", default=".cache/ohlcv")
    parser.add_argument("--initial-capital", type=float, default=100_000_000)
    parser.add_argument("--position-size-pct", type=float, default=0.03)
    parser.add_argument("--max-concurrent", type=int, default=10)
    parser.add_argument("--min-trading-value", type=float, default=50_000_000_000)
    parser.add_argument("--stop-loss-pct", type=float, default=-0.07)
    parser.add_argument("--slippage-pct", type=float, default=0.0, help="편도 슬리피지(예: 0.003=왕복 약 0.6%%). 기본값 0(끔).")
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

    label = "S4" if args.variant == "s4" else "S5"
    technique = "S4" if args.variant == "s4" else "S5"
    print(f"[run_kplus_backtest:{args.variant}] 유니버스 {len(tickers)}종목 로딩 중...")
    ohlcv = load_universe_ohlcv(tickers, args.start, args.end, cache_dir=args.cache_dir)
    print(f"[run_kplus_backtest:{args.variant}] {len(ohlcv)}종목 데이터 확보 완료. 백테스트 시작...")

    common_kwargs = dict(
        min_trading_value_krw=args.min_trading_value,
        position_size_pct=args.position_size_pct,
        addon_size_pct=args.position_size_pct,
        max_position_pct=args.position_size_pct * 2,
        max_concurrent_positions=args.max_concurrent,
        initial_capital_krw=args.initial_capital,
        stop_loss_pct=args.stop_loss_pct,
        slippage_pct=args.slippage_pct,
    )

    if args.variant == "s4":
        config = S4Config(**common_kwargs)
        result = run_s4_backtest(ohlcv, config)
    else:
        config = S5Config(**common_kwargs)
        result = run_s5_backtest(ohlcv, config)

    m = result.metrics
    s = m.signal_level

    print(f"\n=== {label} 백테스트 결과 ===")
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
            args.db, technique, args.start, args.end, config, ohlcv, result.trades,
            detect_s45_signals, trades_csv_path=args.trades_csv,
        )
        print(f"DB 저장 완료: run_id={run_id}  db={args.db}")

    print(
        f"\n⚠️ 본 결과는 {label} 원 자료에 없는 세력봉 판정 파라미터(500억원으로 근사)와 손절가(인접 "
        "기법에서 차용한 신규 설계)를 포함하며, 정성적 배제 필터(시장중심주/1등주 판정 등)와 회피 "
        "패턴(무공방/역망치 등)도 미구현입니다. 근사 거래대금·생존편향 유니버스 등 알려진 한계도 "
        "있습니다. 실거래 판단의 근거로 단독 사용하지 마십시오."
    )


if __name__ == "__main__":
    main()
