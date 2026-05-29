# woonam 자동매매 구현 계획

## 목표

KOSPI 200 + KOSDAQ 150 (한국) 및 S&P 500 + NASDAQ-100 (미국) 종목을 대상으로, 기본적·기술적·정보 분석 세 모듈이 종목별로 학습된 가중치로 합쳐 매수/매도/관망과 사이즈를 결정하는 24/7 로컬 자동매매 시스템을 구축한다. 학습은 과거 데이터 기반 supervised learning, 실거래는 페이퍼 트레이딩 검증 후. 무료 도구만 사용.

## 제약과 원칙

- **자금 안전**: 페이퍼 트레이딩 N개월 통과 + 리스크 엔진 6대 한도 모두 통과 + 결정 감사 로그 영구 보존 없이는 실거래 모드 금지.
- **데이터 정직**: 모든 데이터 조회에 `as_of` 인자 필수. look-ahead bias 차단을 코드 레벨에서 강제.
- **시장 분리**: KR/US 데이터·점수·결정·UI 절대 혼합 금지. 백엔드 `(market, ticker)` 복합키, UI 시장별 탭.
- **무료 전용**: 유료 API/LLM/뉴스 금지. 빠진 부분은 자유 대체 또는 명시적 갭.
- **로컬 단일 머신**: 로컬 PC 24/7 운영. 클라우드 의존 없음. 디스크 ~50~150 GB 누적.
- **종목별 차별화**: 종목마다 3대 모듈 발현이 다름을 전제 — 클러스터별 모델 → 종목별 fine-tuning.

## Phase 0: 기반 정비

### 0.1 도메인 폴더 재구조화 (완료, 커밋 보류)

- [x] `backend/mt5_bridge.py` → `backend/brokers/mt5.py`
- [x] `backend/strategies/red_green.py` → `backend/technical/signals/red_green.py`
- [x] `backend/strategies/manager.py` → `backend/technical/red_green_runtime.py`
- [x] 클래스 `StrategyManager` → `RedGreenRuntime`
- [x] 모든 import 갱신
- [x] AGENTS.md / ARCHITECTURE.md 갱신
- [x] `tests/unit/` `tests/integration/` 분리

### 0.2 DB 구축

- [ ] `pyproject.toml` (uv 관리) + `uv sync`
- [ ] PostgreSQL 16 (이미 설치) + TimescaleDB 확장 (Phase 1에서 시계열 테이블에 사용)
- [ ] 데이터베이스 `woonam` 생성 + 사용자
- [ ] `backend/core/db.py` SQLAlchemy 엔진/세션 (sync + async)
- [ ] `backend/core/models/` ORM — Phase 0 테이블 11개
- [ ] `backend/alembic/` 초기화 + 마이그레이션 0001

### 0.3 시크릿 + 설정

- [ ] `.env.example` 모든 무료 API 키 목록
- [ ] `backend/core/config.py` pydantic-settings 기반
- [ ] `.gitignore`에 `.env`, `__pycache__/`, `.venv/`, `*.db` 등 보강
- [ ] `backend/core/logging.py` structlog 기반 JSON 로깅

### 0.4 인증 + CORS

- [ ] `backend/core/auth.py` JWT (HS256)
- [ ] `users` 테이블 + 부트스트랩 단일 사용자
- [ ] `main.py`에서 `allow_origins=["*"]` 제거 → 화이트리스트
- [ ] `/api/strategy/*/start` 등 변경 액션에 인증 의무화
- [ ] 실거래 활성화는 추가 confirmation token 요구

### 0.5 리스크 엔진

- [ ] `backend/core/risk.py` 6대 한도 — max_lot, daily_loss, consecutive_loss, max_positions, max_spread, symbol_allowlist
- [ ] `RiskCheckResult` + `RiskLimitFailure` 타입
- [ ] 시장별 한도 어댑터 (`markets/kr/risk.py`, `markets/us/risk.py`)
- [ ] 모든 주문 의도가 거치는 게이트로 통합

### 0.6 결정 감사 로그

- [ ] `decision_audit` 테이블 — 입력 점수·가중치·LLM 원문·실행 결과 JSONB
- [ ] `risk_snapshots` 테이블 — 6대 검사 결과
- [ ] `backend/core/audit.py` 헬퍼

### 0.7 페이퍼 트레이딩

- [ ] `backend/brokers/paper.py` 가상 시장가 체결 + 슬리피지 ±1bps
- [ ] `paper_accounts` / `paper_positions` / `paper_trades` 테이블
- [ ] 시장별 거래비용 (KR 거래세 0.18% + 수수료, US 수수료 0 + SEC fee)

### 0.8 as_of 가드

- [ ] `backend/core/as_of.py` 데코레이터/컨텍스트
- [ ] 데이터 조회 함수가 `as_of` 누락 시 즉시 실패
- [ ] 단위 테스트로 누수 시나리오 검증

## Phase 1: Historical 데이터 인제스천

### 1.1 종목 universe 관리

- [ ] `securities` 테이블에 KOSPI200 + KOSDAQ150 + S&P500 + NASDAQ-100 적재
- [ ] 상장/상폐 일자 추적 (survivorship bias 차단)
- [ ] 정기 갱신 잡 (월 1회)

### 1.2~1.4 시세 + 재무

- [ ] `data/price/kr_pykrx.py`, `data/price/kr_fdr.py`, `data/price/us_yfinance.py`
- [ ] `data/fundamental/kr_dart.py` (OpenDartReader), `data/fundamental/us_edgar.py`
- [ ] 일/시간 봉 OHLCV + 외국인/기관 매매
- [ ] 분기 재무 (손익·재무상태·현금흐름) + as_of (공시일 기준)

### 1.5 공시

- [ ] DART + KIND 공시 본문 + 분류 코드
- [ ] EDGAR 8-K, 10-K, 10-Q

### 1.6~1.11 뉴스 (역사적 깊이 최대화)

- [ ] BIGKinds 인제스천 (한국 1990년~)
- [ ] GDELT 2.0 BigQuery (글로벌 1979년~)
- [ ] Wayback Machine 보충 백필
- [ ] Common Crawl 한국어 뉴스 도메인 추출 (선택)
- [ ] 언론사 RSS + sitemap.xml 백필
- [ ] Naver 검색 API (일 25,000 무료)

### 1.12~1.14

- [ ] 거시 지표 (FRED, BOK ECOS, FDR)
- [ ] 데이터 품질 검증 (great-expectations)
- [ ] APScheduler 잡 정의

## Phase 2: Feature Engineering

### 기술적

- [ ] pandas-ta 130개+ 지표
- [ ] VWAP, 거래량 프로파일, 시장 레짐 분류기, 종목간 상관·로테이션
- [ ] 시그널 점수화 (-100 ~ +100) + 신뢰도

### 기본적

- [ ] PER/PBR/ROE/ROA/EV-EBITDA/부채/매출성장률
- [ ] 업종 percentile
- [ ] DCF 또는 multiple-based 밸류에이션
- [ ] 점수화 + 신뢰도

### 정보

- [ ] LLM 추론 파이프라인 (Ollama qwen2.5:14b, fallback Groq/Gemini 무료)
- [ ] 종목 매핑 NER (spaCy + KoNLPy)
- [ ] 이벤트/감성/영향도/horizon 추출
- [ ] 신뢰도 가중 (소스 × 검증 × 신선도)
- [ ] LLM 출력 캐싱 + 환각 방어 (원문 인용 강제)
- [ ] 점수화 + 신뢰도

## Phase 3: 종목별 학습

- [ ] 클러스터링 (산업 × 시총 × 변동성 × 거래량) 4~6 클러스터
- [ ] 레이블 (미래 N일 수익률, max DD, Sharpe)
- [ ] Walk-forward CV
- [ ] LightGBM 클러스터별 학습 → 종목별 fine-tuning
- [ ] 모델 레지스트리 (MLflow OSS)
- [ ] vectorbt 백테스트 + quantstats 리포트

## Phase 4: 복합 결정 엔진

- [ ] 종목별 가중치로 (F, T, I) → composite
- [ ] 진입 게이트 (강한 반대 시그널 차단)
- [ ] Kelly 사이저 + 변동성 타겟팅 + 리스크 한도
- [ ] 거래비용 모델
- [ ] 모델 drift 감지 → 자동 중지

## Phase 5: UI

- [ ] KR/US 탭으로 모든 화면 분리
- [ ] 종목별 3모듈 점수 대시보드
- [ ] 결정 감사 뷰
- [ ] 백테스트 비교 뷰
- [ ] 데이터 신선도 + LLM 비용 한도 모니터링
- [ ] 1버튼 킬스위치

## Phase 6: 페이퍼 → 실거래

- [ ] 3개월 페이퍼 트레이딩
- [ ] 단계적 실거래 (5% → 20% → 100%)
- [ ] 월 1회 재학습 정착

## 열려 있는 결정

- 한국 주식 broker 어댑터 (KIS Developers OpenAPI 무료, but Phase 6 직전에 본격 구현 — 그 전엔 paper로 충분)
- 미국 주식 broker 어댑터 (Alpaca paper API 무료, but 동일)
- 종목 fine-tuning 임계 데이터량 (예: 종목당 거래일 500일 이상일 때만)
- 정보 분석 horizon (단일 vs. 1d/5d/20d 다중)
- TP1 부분청산 여부 (기존 Red-Green 명세 미정)
