# 아키텍처

이 저장소는 OpenAI의 하네스 엔지니어링 구조를 기준으로, **woonam-auto-trading** 자동매매 시스템을 에이전트가 읽고 확장하기 쉬운 형태로 관리합니다. 시스템은 한국(KOSPI200+KOSDAQ150)과 미국(S&P500+NASDAQ-100) 주식을 대상으로, 기본적·기술적·정보 분석 세 모듈의 복합 결정으로 자동매매를 수행합니다. 모든 도구는 무료 티어만 사용합니다.

## 저장소 지식 구조

```text
AGENTS.md
ARCHITECTURE.md
README.md
backend/                                 # Phase 0.1에서 도메인 폴더로 재구조화
├── core/                                # 공통 — 설정·DB·인증·로깅·LLM·리스크·페이퍼·as_of
│   ├── models/                          # SQLAlchemy ORM (15개 테이블)
│   └── llm/                             # Ollama/Groq/Gemini 어댑터
├── markets/                             # 시장별 어댑터
│   ├── kr/                              # KRX 캘린더·세금·코드·통화
│   └── us/                              # NYSE/NASDAQ 캘린더·세금·코드·통화
├── brokers/                             # 주문 라우팅 + 페이퍼 회계
│   ├── mt5.py                           # 기존 FX/금만 사용
│   ├── paper.py                         # 가상 체결 (Phase 0.7)
│   ├── paper_persistence.py             # 페이퍼 계정/포지션/거래 DB 영구화
│   ├── paper_equity.py                  # equity curve / drawdown / Sharpe-like (pure math)
│   └── db_price_oracle.py               # PaperBroker용 가격 oracle
├── data/                                # 인제스천 (Phase 1)
│   ├── price/  fundamental/  disclosures/  macro/
│   ├── universe/                        # KOSPI200/KOSDAQ150 + SP500/NASDAQ100 + survivorship 추적
│   │   └── membership_query.py          # 시점별 멤버십 O(1) 조회 (backtest용)
│   └── news/                            # BIGKinds + GDELT + Naver + RSS + Wayback
│       ├── ticker_mapper.py             # 별칭 사전 + LLM tickers 통합
│       ├── aliases.py                   # KR/US 시드 별칭
│       ├── historical_backfill.py       # Month-chunked resumable orchestrator
│       └── historical_runner.py         # DB-bound glue
├── fundamental/                         # 기본적 분석 모듈 (Phase 2)
├── technical/                           # 기술적 분석 모듈 (Phase 2)
│   └── signals/                         # red_green.py 외 다중 시그널 (momentum/trend/mean_reversion)
├── information/                         # 정보 분석 모듈 (Phase 2)
├── decision/                            # 복합 결정 엔진 (Phase 4)
│   ├── composite.py · gates.py · sizer.py · runner.py · drift.py
├── training/                            # 학습 (Phase 3) — 프로덕션=시장별 순수 LightGBM(US=tb/KR=mn 라벨), walk-forward
│   └── ensemble_model.py                # EnsembleRankModel: 옵트인 shadow 툴만 (2026-07-10 US 앙상블 검증 후 기각, 기본 OFF)
├── analytics/                           # Pure-function 모듈
│   ├── attribution.py                   # F/T/I per-module Pearson r + sign accuracy
│   └── data_quality.py                  # Gap detection + freshness scoring
├── backtest/                            # Phase 5+
│   ├── replay.py                        # 과거 결정 그대로 시뮬레이션 (pure)
│   ├── runner.py                        # DB → replay glue
│   ├── rescoring.py                     # 현재 가중치를 과거 score에 적용 (pure)
│   └── rescoring_runner.py              # + survivorship-bias 가드
├── scan/                                # 시그널 스캔 (dry-run 결정 엔진)
│   └── engine.py
├── runtime/                             # 봉마감·스케줄러·WS
│   └── scheduler.py                     # APScheduler 16+ 잡
├── routes/                              # FastAPI 라우터 (27개)
├── alembic/versions/                    # DB 마이그레이션 (12개)
├── tests/
│   ├── unit/                            # 520+ 테스트, 41개 파일
│   └── integration/                     # DB 필요
└── pyproject.toml                       # uv 관리

docs/
├── design-docs/
│   ├── index.md
│   └── core-beliefs.md
├── exec-plans/
│   ├── active/
│   │   ├── woonam-auto-trading-implementation.md      # 메인 계획
│   │   └── red-green-auto-trading-implementation.md   # 기술 시그널 1개 계획
│   ├── completed/
│   └── tech-debt-tracker.md
├── generated/
│   └── db-schema.md
├── product-specs/
│   ├── index.md
│   ├── new-user-onboarding.md
│   └── red-green-auto-trading.md
├── references/
│   ├── red-green-strategy-pine-summary.md
│   ├── design-system-reference-llms.txt
│   ├── nixpacks-llms.txt
│   └── uv-llms.txt
├── DESIGN.md
├── FRONTEND.md
├── PLANS.md
├── PRODUCT_SENSE.md
├── QUALITY_SCORE.md
├── RELIABILITY.md
└── SECURITY.md
```

## 자동매매 도메인 레이어

각 자동매매 도메인(`fundamental/`, `technical/`, `information/`, `decision/`, `training/`)은 아래 흐름을 따릅니다.

```text
Types -> Config -> Repo -> Service -> Runtime -> UI
```

교차 관심사는 `Providers`를 통해서만 도메인에 주입합니다.

```text
Providers -> Service -> Runtime -> UI
```

## 레이어 책임

- `Types`: 캔들, 지표 값, 재무 비율, 정보 이벤트, 시그널 점수, 결정, 주문 의도, 포지션 상태, 리스크 설정의 타입 계약을 정의합니다 (`backend/core/models/`, 각 도메인의 dataclass).
- `Config`: 전략 파라미터·계좌별 리스크 한도·시장별 설정을 검증된 설정으로 만듭니다 (`backend/core/config.py`).
- `Repo`: 시세, 재무, 공시, 뉴스, 포지션, 주문 이력, 결정 감사 로그를 읽고 저장합니다 (`backend/core/db.py`, `backend/data/*`).
- `Providers`: MT5/페이퍼 브로커, 시장별 캘린더, LLM, 시간 소스, 알림, 로깅, 인증, 환경 설정을 제공합니다 (`backend/core/llm/`, `backend/markets/*`, `backend/brokers/*`).
- `Service`: 각 도메인의 분석 로직 — 기본적 비율·밸류에이션, 기술적 지표·시그널, 정보 분류·신뢰도, 복합 가중·게이팅·사이저, 클러스터/종목 학습.
- `Runtime`: 봉 마감 감지, APScheduler 잡, 웹소켓 브로드캐스트, 주문 실행, 장애 복구 (`backend/runtime/`).
- `UI`: 전략·결정·종목 분석 상태 표시, KR/US 탭 분리, 시작/중지, 수동 승인, 결정 감사 뷰 (`frontend/`).

## 추가 규약 — woonam 시스템

- **as_of 강제**: 모든 데이터 조회·시그널 생성·결정에 `as_of: datetime` 인자 필수. 학습/백테스트/실거래 동일 파이프라인 (look-ahead bias 차단).
- **시장 분리**: `(market, ticker)` 복합키 필수. KR/US 데이터·점수·결정·UI 절대 혼합 금지.
- **무료 도구 전용**: 유료 API/LLM/뉴스 금지. 기록된 무료 대체가 없는 경우 명시적 갭으로 남김.
- **모든 결정은 감사 로그로**: 입력 점수·가중치·LLM 원문 출력·실행 결과 모두 `decision_audit` 테이블에 영구 저장.
- **페이퍼 → 실거래 단계 강제**: `RUNTIME_MODE=paper`가 기본. `live` 활성화는 두 단계 확인 + 결정 감사 로그 N건 이상 검증 후만.

## 통합 파이프라인 — 선정→실행 (2026-06-29 신설)

원 제작자와의 역할분담 확정: **횡단면 알파(우리 모델) = 지수투자·시황파악·종목선정(WHAT)**, **기존 기술적 요소(레드/그린 포함) = 단일종목 매매 타이밍(WHEN)**. 두 계층을 하나의 일일 파이프라인으로 결합합니다. (상세: `DESIGN_INTEGRATED.md`)

```
A. 시황 READ   regime + breadth + 확신도 → 목표 노출(target_exposure)   ┐ 선정 계층
B. 선정        횡단면 알파 → 바스켓(top-decile, 균등비중)               ┘ decision/selection.py
                                  ↓
C. 실행 타이밍  바스켓 종목별 기술적 일봉 점수(추세·모멘텀·평균회귀·레드그린) ┐ 실행 계층
               → 진입 지연(약세)/청산(붕괴·바스켓이탈). D1: 타이밍만, veto 불가  │ decision/integrated_runner.py
D. 리스크·체결·감사  size=equity×노출×(1/n) → RiskEngine → PaperBroker → 2단계 감사 ┘
```

- **계정 분리(D2)**: integrated = `core-kr`/`core-us`(바스켓), 레거시 composite = `default-*`, ML = `ml-*`. 레드/그린 단일종목 매매와 계정·UX 분리.
- **노출 오버레이(D4) — TREND×VOL-SPIKE 방어 (2026-07-10 채택·커밋)**: 바스켓은 능동 인덱스. `target_exposure = regime기준 노출(위기0·회피0.4·중립0.7·선호1.0) × TREND_mult × VOL_mult` (`decision/selection.py`). `TREND_mult`=시장추세(mean `px_vs_sma50`>0)면 1.0, 아니면 0.4. `VOL_mult`=시장 실현변동성 백분위(`vix_pctile_252d`/`kospi_rv_pctile_252d`)≥0.8이면 0.5, 아니면 1.0. 17신호 스윕(KR+US 2016-2026) 승자 — buy&hold 대비 **drawdown 반감(−44%→−18%)**, KR Calmar 0.92/US 0.80, test 22/22. breadth는 추세 미가용 시 폴백으로만 사용(보고는 유지). ETF 미사용(v1). **정직: 이는 방어(리스크·낙폭) 개선이지 알파(선정) 개선이 아님 — EW시장 오버레이 기준·거래비용 미모델.**
- **데이터 모델**: `market_read`(시황 일별), `selection_basket`(바스켓 일별). 감사는 기존 `decision_audit`에 `model_version=integrated_{m}_v1` + 2단계 snapshot(selection/timing).
- **스케줄러**: `features.rebuild.daily`(20:00 UTC, 라이브캐시 재빌드) → `integrated.daily`(23:00 UTC).
- **UX**: `통합 전략` 섹션 — 시황(`/market`)·선정(`/basket`)·실행(`/execution`). `/api/integrated/*`. 새 방향 전용 설계.

## 메인 전략 경계

메인 시스템은 woonam 복합 결정 엔진이며, `Red-Green Signals + SL/TP v9 (Trail Fix)`는 그 안의 **기술적 분석 모듈의 한 시그널**입니다 (`backend/technical/signals/red_green.py`). 통합 파이프라인에서 이 기술적 모듈은 **실행 타이밍 게이트(C)** 역할을 맡습니다(선정은 거부 못 함, D1).

서버 구현은 Pine Script를 그대로 복사하는 것이 아니라, 같은 계산 순서와 상태 전이를 재현해야 합니다. 특히 다음 순서를 유지합니다.

1. BB-ICHI 클라우드와 Supertrend 계산
2. `freshLong`, `freshShort` 신호 산출
3. 기존 포지션의 트레일링 업데이트
4. SL, TP1, Trail 도달 여부 확인
5. TP1 도달 시 SL을 본절로 이동
6. SL 또는 Trail 청산 처리
7. 신규 진입 처리

## 금지 사항

- UI에서 직접 MT5 주문을 생성하지 않습니다.
- 전략 계산 없이 단순 알림만으로 실거래 주문을 실행하지 않습니다.
- `Service`를 우회해 `Runtime`이나 `UI`가 포지션 상태를 임의 변경하지 않습니다.
- 실거래 모드에서 데모용 mock 데이터와 실제 MT5 데이터를 섞지 않습니다.
- Pine Script의 라벨, 색상, 라인 표시 로직을 주문 판단의 근거로 사용하지 않습니다.

## 강제 적용 대상

향후 구조적 테스트와 린트로 다음 항목을 검증합니다.

- 전략 도메인의 import가 레이어 방향을 따르는지
- 캔들 데이터가 충분한 lookback 이후에만 신호를 생성하는지
- 주문 전 리스크 한도가 항상 검사되는지
- 포지션 상태 전이가 로그로 남는지
- 실거래 명령과 데모 명령이 명확히 분리되는지
