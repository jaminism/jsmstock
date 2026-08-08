"""K1 전략 백테스트 실행 CLI.

사전에 scripts/fetch_data.py 로 데이터를 캐시해두면 재실행이 빠르다 (미캐시 종목은
자동으로 다운로드한다). K1은 원 자료에 명시적 가격 손절가가 없어 K2와 동일하게 인접 기법에서
차용한 신규 설계(stop_loss_pct=-7%, 전고점 익절, 4일차 강제청산)를 쓴다 — K1Config 참고.
K2와의 핵심 차이는 진입 방식("종가베팅", D+1~D+2 한정)이다.

사용 예:
    python scripts/run_k1_backtest.py --start 2021-01-01 --end 2024-12-31 --min-market-cap 0 --cache-dir .cache/ohlcv
"""

from __future__ import annotations

import argparse
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich_stock.backtest.engine import run_k1_backtest
from rich_stock.config import K1Config
from rich_stock.data.loader import load_universe_ohlcv
from rich_stock.data.universe import get_universe


def main() -> None:
    parser = argparse.ArgumentParser(description="K1 전략 백테스트")
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
    parser.add_argument("--slippage-pct", type=float, default=0.0, help="편도 슬리피지(예: 0.003=왕복 약 0.6%). 기본값 0(끔).")
    parser.add_argument("--equity-csv", default=None, help="자산곡선을 CSV로 저장할 경로(선택)")
    parser.add_argument("--trades-csv", default=None, help="개별 트레이드 내역을 CSV로 저장할 경로(선택)")
    args = parser.parse_args()

    universe = get_universe(min_market_cap=args.min_market_cap)
    tickers = universe["Code"].tolist()
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"[run_k1_backtest] 유니버스 {len(tickers)}종목 로딩 중...")
    ohlcv = load_universe_ohlcv(tickers, args.start, args.end, cache_dir=args.cache_dir)
    print(f"[run_k1_backtest] {len(ohlcv)}종목 데이터 확보 완료. 백테스트 시작...")

    config = K1Config(
        min_trading_value_krw=args.min_trading_value,
        position_size_pct=args.position_size_pct,
        addon_size_pct=args.position_size_pct,
        max_position_pct=args.position_size_pct * 2,
        max_concurrent_positions=args.max_concurrent,
        initial_capital_krw=args.initial_capital,
        stop_loss_pct=args.stop_loss_pct,
        slippage_pct=args.slippage_pct,
    )

    result = run_k1_backtest(ohlcv, config)
    m = result.metrics
    s = m.signal_level

    print("\n=== K1 백테스트 결과 ===")
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
        import pandas as pd

        rows = []
        for t in result.trades:
            rows.append(
                {
                    "ticker": t.ticker,
                    "signal_date": t.signal_date,
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "exit_reason": t.exit_reason,
                    "return_pct": t.return_pct * 100 if t.entry_date else None,
                    "pnl_per_unit": t.pnl if t.entry_date else None,
                    "closed": t.is_closed,
                }
            )
        pd.DataFrame(rows).to_csv(args.trades_csv, index=False, encoding="utf-8-sig")
        print(f"트레이드 내역 저장: {args.trades_csv}")

    print(
        "\n⚠️ 본 결과는 K1 원 자료에 없는 명시적 손절가(인접 기법에서 차용한 신규 설계)를 포함하며, "
        "정성적 배제 필터(1등주 판정 등)와 캔들 패턴 분류(국민1음봉 등)도 단순화되어 있습니다. "
        "근사 거래대금·생존편향 유니버스 등 알려진 한계도 있습니다. "
        "실거래 판단의 근거로 단독 사용하지 마십시오."
    )


if __name__ == "__main__":
    main()
