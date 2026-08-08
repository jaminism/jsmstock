---
name: stock-research-team
description: "주식 리서치·매매기법 연구·포트폴리오·리스크관리 풀 파이프라인. 기술적분석→기업분석→매크로전망→매매기법연구→포트폴리오설계→리스크검증을 증권사 애널리스트·펀드매니저급 에이전트 팀이 협업하여 생성한다. '종목 분석해줘', '주식 기법 연구해줘', '매매전략 만들어줘', '포트폴리오 구성해줘', '목표주가', '기술적 분석', '밸류에이션', '리스크 점검' 등 주식/증권 투자 리서치 전반에 이 스킬을 사용한다. 단, 실제 매매 주문 실행이나 브로커/거래소 API 연동, 실시간 시세·공시 조회, 세금 신고, 법정 투자자문(맞춤형 자산관리 계약) 제공은 이 스킬의 범위가 아니며, 모든 산출물은 참고용 리서치 자료임을 명시한다."
---

# Stock Research Team — 주식 리서치 & 트레이딩 데스크

기술적분석→기업분석→매크로전망→매매기법연구→포트폴리오설계→리스크검증까지, 증권사 애널리스트·펀드매니저급 에이전트 팀이 협업하여 생성한다.

## 실행 모드

**에이전트 팀** — 6명이 SendMessage로 직접 통신하며 교차 검증한다.

## 에이전트 구성

| 에이전트 | 파일 | 역할 | 타입 |
|---------|------|------|------|
| technical-analyst | `.claude/agents/technical-analyst.md` | 차트/보조지표/거래량 분석, 매매타이밍 후보 | general-purpose |
| fundamental-analyst | `.claude/agents/fundamental-analyst.md` | 재무제표/산업분석/밸류에이션, 목표주가 | general-purpose |
| macro-strategist | `.claude/agents/macro-strategist.md` | 경기국면/시장레짐/섹터로테이션 | general-purpose |
| quant-strategist | `.claude/agents/quant-strategist.md` | 매매기법 연구, 팩터, 백테스팅 설계 | general-purpose |
| fund-manager | `.claude/agents/fund-manager.md` | 자산배분, 포지션 설계, 포트폴리오 통합 | general-purpose |
| risk-manager | `.claude/agents/risk-manager.md` | 교차검증, 리스크한도, 최종 통합 보고서 | general-purpose |

## 워크플로우

### Phase 1: 준비 (오케스트레이터 직접 수행)

1. 사용자 입력에서 추출한다:
    - **대상**: 종목명/코드(복수 가능), 또는 매매기법/전략 주제, 또는 기존 보유 포트폴리오
    - **목적**: 종목 분석, 매매기법 연구, 포트폴리오 신규 구성, 기존 포트폴리오 점검 중 어느 것인지
    - **투자자 프로필** (가능한 범위): 투자 기간, 위험 감내 수준, 투자 가능 금액
    - **보유 데이터** (선택): 가격/거래량 데이터, 재무 데이터, 뉴스/공시 요약, 기존 포트폴리오 목록
2. `_workspace/` 디렉토리를 프로젝트 루트에 생성한다
3. 입력을 정리하여 `_workspace/00_input.md`에 저장한다
4. 기존 파일(보유 포트폴리오 등)이 있으면 `_workspace/`에 복사하고 해당 Phase를 건너뛴다
5. **실시간 시세/공시 조회는 지원하지 않음**을 사용자에게 안내하고, 제공된 데이터 또는 사용자가 요약한 최신 정보 기준으로 분석함을 명시한다

### Phase 2: 팀 구성 및 실행

| 순서 | 작업 | 담당 | 의존 | 산출물 |
|------|------|------|------|--------|
| 1a | 기술적 분석 | technical-analyst | 없음 | `_workspace/01_technical_analysis.md` |
| 1b | 기업분석 | fundamental-analyst | 없음 | `_workspace/02_fundamental_analysis.md` |
| 1c | 매크로 전망 | macro-strategist | 없음 | `_workspace/04_macro_outlook.md` |
| 2 | 매매기법 연구 | quant-strategist | 1a, 1b, 1c | `_workspace/03_strategy_research.md` |
| 3 | 포트폴리오 설계 | fund-manager | 2 | `_workspace/05_portfolio_strategy.md` |
| 4 | 리스크 검증 및 통합 | risk-manager | 3 | `_workspace/06_risk_review.md`, `_workspace/07_investment_summary.md` |

**팀원 간 소통 흐름:**
- 1a/1b/1c는 병렬 실행 가능 (서로 의존 없음)
- technical-analyst, fundamental-analyst, macro-strategist 완료 → quant-strategist에게 각자의 핵심 신호/판정 전달
- quant-strategist 완료 → fund-manager에게 확정 전략 규칙(진입/청산/보유기간) 전달
- fund-manager 완료 → risk-manager에게 확정 포트폴리오 전달
- risk-manager는 전 산출물을 교차 검증. 🔴 필수 수정 시 fund-manager(필요 시 원 애널리스트)에게 수정 요청 (최대 2회)

### Phase 3: 통합 및 최종 산출물

1. `_workspace/` 내 모든 파일을 확인한다
2. 🔴 필수 수정이 모두 반영되었는지 확인한다
3. `_workspace/07_investment_summary.md`를 사용자에게 요약 보고한다 (면책 고지 포함)

## 작업 규모별 모드

| 사용자 요청 패턴 | 실행 모드 | 투입 에이전트 |
|----------------|----------|-------------|
| "종목 풀 분석 + 포트폴리오까지 해줘" | **풀 파이프라인** | 6명 전원 |
| "이 종목 기술적 분석만 해줘" | **기술적 분석 모드** | technical-analyst 단독 |
| "이 종목 기업분석/밸류에이션 해줘" | **기업분석 모드** | fundamental-analyst 단독 |
| "요즘 시장 전망 어때" | **매크로 모드** | macro-strategist 단독 |
| "OO 기법(모멘텀 등) 연구해서 전략 짜줘" | **전략 리서치 모드** | quant-strategist (+ 필요 시 technical/fundamental/macro 선택 투입) |
| "이 종목들로 포트폴리오 짜줘" | **포트폴리오 모드** | fund-manager + risk-manager (선행 분석 있으면 활용, 없으면 fund-manager가 가정 명시 후 진행) |
| "내 포트폴리오 리스크 점검해줘" | **리스크 점검 모드** | risk-manager 단독 (기존 포트폴리오를 `_workspace/05_portfolio_strategy.md`로 매핑) |

## 데이터 전달 프로토콜

| 전략 | 방식 | 용도 |
|------|------|------|
| 파일 기반 | `_workspace/` 디렉토리 | 주요 산출물 저장 및 공유 |
| 메시지 기반 | SendMessage | 실시간 핵심 정보 전달, 수정 요청 |
| 태스크 기반 | TaskCreate/TaskUpdate | 진행 상황 추적, 의존 관계 관리 |

## 에러 핸들링

| 에러 유형 | 전략 |
|----------|------|
| 실시간 시세/재무데이터 없음 | 사용자 제공 데이터 또는 정성적 서술 기반으로 제한적 분석, "정보 미검증" 명시 |
| 종목/기법 대상 불명확 | 후보 2~3개 제시 후 사용자 선택 요청 |
| 에이전트 실패 | 1회 재시도 → 실패 시 해당 산출물 없이 진행, 통합 보고서에 누락 명시 |
| 팀원 간 의견 상충 (예: 기술적 매수 vs 펀더멘털 고평가) | 상충을 은폐하지 않고 그대로 보고, fund-manager가 비중 축소/분할진입으로 반영 |
| 리뷰에서 🔴 발견 | fund-manager(또는 해당 애널리스트)에 수정 요청 → 재작업 → 재검증 (최대 2회) |
| 고위험 요청(몰빵, 레버리지, 신용거래 등) | 요청은 리서치 범위 내에서 반영하되 risk-manager가 위험성을 명시적으로 경고 |

## 테스트 시나리오

### 정상 흐름
**프롬프트**: "삼성전자 종목 분석해서 매수 여부 판단하고, 포트폴리오 비중까지 제안해줘. 중기 투자, 중립적 위험선호야."
**기대 결과**:
- 기술적 분석: 추세/지표/지지저항 기반 신호 강도
- 기업분석: 재무비율, PER/PBR 등 밸류에이션, 목표주가·투자의견
- 매크로: 관련 업종의 시장 국면 판정
- 매매기법: 중기 스윙에 적합한 진입/청산 규칙
- 포트폴리오: 비중 제안, 손절/목표가
- 리스크 검증: 집중도·낙폭 점검, 최종 통합 보고서 + 면책 고지

### 기존 파일 활용 흐름
**프롬프트**: "내가 가진 포트폴리오야 (종목/비중 목록 첨부). 리스크만 점검해줘."
**기대 결과**:
- 첨부 목록을 `_workspace/05_portfolio_strategy.md`로 매핑
- risk-manager 단독 투입 — 집중도, 손절 미설정 여부, 낙폭 시나리오 점검
- 🔴/🟡/🟢 등급별 개선 제안 + 통합 보고서

### 에러 흐름
**프롬프트**: "잘 모르는 소형주인데 그냥 단타 기법 하나 추천해줘. 손절 없이 몰빵할 거야."
**기대 결과**:
- quant-strategist가 단타(단기 모멘텀/브레이크아웃 등) 기법을 규칙화하되 손절 조건을 필수 포함하여 제시
- fund-manager가 "손절 없는 몰빵" 요청을 그대로 승인하지 않고, 집중 리스크를 명시하며 대안(분할진입, 손절 설정) 제안
- risk-manager가 최종 보고서에서 🔴로 해당 리스크를 강조

## 에이전트별 확장 스킬

에이전트의 도메인 전문성을 강화하는 확장 스킬:

| 에이전트 | 확장 스킬 | 역할 |
|---------|----------|------|
| technical-analyst | `technical-indicator-library` | 보조지표 산식·해석 기준, 차트 패턴 카탈로그 |
| fundamental-analyst | `equity-valuation-methods` | PER/PBR/PEG/EV-EBITDA, 재무비율 벤치마크, 듀퐁분석 |
| quant-strategist | `backtesting-framework` | 백테스팅 절차, 팩터투자, 과최적화 방지 체크리스트 |
| risk-manager | `risk-position-sizing` | 포지션 사이징(Kelly 등), VaR, 손절 규칙, 집중도 한도 |
