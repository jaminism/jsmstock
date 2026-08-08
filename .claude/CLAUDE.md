# Stock Research & Trading Desk Harness

주식 종목 리서치(기술적/펀더멘털/매크로) → 매매기법 연구 → 포트폴리오 전략 → 리스크 검증까지, 증권사 애널리스트·펀드매니저급 전문 에이전트 팀이 협업하여 산출물을 생성하는 하네스.

## 구조

```
.claude/
├── agents/
│   ├── technical-analyst.md      — 기술적 분석 (차트/지표/거래량)
│   ├── fundamental-analyst.md    — 증권사 기업분석 애널리스트 (재무/밸류에이션/목표주가)
│   ├── macro-strategist.md       — 매크로/업종 전략가 (경기/금리/섹터로테이션)
│   ├── quant-strategist.md       — 퀀트 전략가 (매매기법 연구, 팩터, 백테스팅)
│   ├── fund-manager.md           — 펀드매니저 (포트폴리오/자산배분/포지션 설계)
│   └── risk-manager.md           — 리스크 관리자 (교차검증, 손절/한도, 최종 통합)
├── skills/
│   ├── stock-research-team/
│   │   └── skill.md              — 오케스트레이터
│   ├── technical-indicator-library/
│   │   └── skill.md              — 보조지표·차트패턴 라이브러리 (technical-analyst 확장)
│   ├── equity-valuation-methods/
│   │   └── skill.md              — 기업가치평가·재무비율 방법론 (fundamental-analyst 확장)
│   ├── backtesting-framework/
│   │   └── skill.md              — 백테스팅·팩터투자 방법론 (quant-strategist 확장)
│   └── risk-position-sizing/
│       └── skill.md              — 포지션사이징·리스크한도 방법론 (risk-manager 확장)
└── CLAUDE.md                     — 이 파일
```

## 사용법

`/stock-research-team` 스킬을 트리거하거나 자연어로 요청한다.

예시:
- "삼성전자 종목 분석해줘 (기술적+펀더멘털+매크로 다 포함해서)"
- "모멘텀 기반 매매 기법 연구해서 백테스트 설계까지 해줘"
- "이 종목들로 포트폴리오 구성하고 리스크 점검해줘"

## 팀 구성 철학

- **셀사이드(증권사) 관점**: technical-analyst, fundamental-analyst, macro-strategist가 리서치 리포트를 생성
- **바이사이드(운용) 관점**: quant-strategist가 매매기법을, fund-manager가 실제 포트폴리오/포지션을 설계
- **컴플라이언스/리스크 관점**: risk-manager가 전체를 교차검증하고 최종 통합 보고서를 편집

## 중요 — 면책 고지

이 하네스의 모든 산출물은 **투자 판단을 돕기 위한 리서치·교육 자료**이며, 특정 금융투자상품의 매수·매도를 권유하는 법정 투자자문이 아니다. 모든 산출물 최하단에 다음 문구를 반드시 포함한다:

> ⚠️ 본 자료는 정보 제공 목적의 리서치 자료이며, 투자 자문이 아닙니다. 실제 투자 결정과 그 결과에 대한 책임은 투자자 본인에게 있습니다. 투자 전 최신 공시자료와 시세를 반드시 별도로 확인하십시오.
