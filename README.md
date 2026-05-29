# woonam-auto-trading

KR (KOSPI200 + KOSDAQ150) + US (S&P500 + NASDAQ-100) 주식을 대상으로
**기본적 / 기술적 / 정보 분석** 세 모듈의 종목별 학습 가중치로 매수·매도·관망과
사이즈를 결정하는 24/7 로컬 자동매매 시스템.

핵심 원칙:
- **무료 도구 전용** — DART / EDGAR / BIGKinds / GDELT / pykrx / yfinance /
  FRED / BOK ECOS / Ollama / Groq 무료 티어. 유료 데이터·LLM 금지.
- **`as_of` 강제** — 모든 데이터 조회·시그널·결정에 `as_of: datetime` 의무.
  Look-ahead bias를 코드 레벨에서 차단.
- **시장 분리** — `(market, ticker)` 복합키 + KR/US 절대 혼합 금지 + 시장별
  페이퍼 계정 (default-kr KRW, default-us USD).
- **페이퍼 → 실거래 단계** — `RUNTIME_MODE=paper` 기본, live 활성화는 JWT
  live 플래그 + "I UNDERSTAND THE RISK" 확인 문구 의무.
- **모든 결정 영구 audit** — composite score, 가중치, gate 결과, risk
  snapshot, 실행 결과까지 `decision_audit` JSONB에 저장.

---

## 사전 환경

| 항목 | 요구 |
|---|---|
| OS | Windows 10/11 (RTX 3090 Ti 운영 PC 기준) |
| Python | 3.12 |
| PostgreSQL | 16 + TimescaleDB 확장 |
| Ollama | 로컬 서비스 (`http://localhost:11434`) |
| 모델 | `qwen2.5:14b` (Information 분류기 기본값) |
| 패키지 관리 | [uv](https://docs.astral.sh/uv/) |

---

## 첫 가동 절차

### 1. 백엔드 환경

```powershell
# 백엔드 의존성 설치
cd backend
uv sync

# .env 작성 — `.env.example`에 모든 키 카탈로그가 있음
copy .env.example .env
# JWT_SECRET_KEY를 채울 것:
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. 데이터베이스

```powershell
# PostgreSQL에 woonam DB + user 생성
psql -U postgres -c "CREATE USER woonam WITH PASSWORD 'woonam';"
psql -U postgres -c "CREATE DATABASE woonam OWNER woonam;"

# TimescaleDB 확장 (시계열 테이블용)
psql -U postgres -d woonam -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"

# 스키마 마이그레이션 (10개 revision)
uv run alembic upgrade head
```

### 3. Ollama (정보 분석 LLM)

```powershell
# 별도 터미널에서 Ollama 서비스 실행 후 모델 pull
ollama pull qwen2.5:14b
```

### 4. 서버 기동

```powershell
uv run python main.py
# 또는
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://localhost:8000` — 로그인 화면.
초기 운영자 계정은 부트스트랩 첫 실행에서 자동 생성됨 (사용자명은
`BOOTSTRAP_USERNAME`, 비밀번호는 `BOOTSTRAP_PASSWORD` 환경변수).

---

## 일일 운영 흐름

스케줄러(APScheduler)가 자동으로 처리합니다:

| 잡 | 시각 | 책임 |
|---|---|---|
| universe.monthly | 매월 1일 06:00 KST | KOSPI200 / KOSDAQ150 / SP500 / NASDAQ100 멤버십 갱신 |
| prices.kr.daily | 평일 15:35 KST | KR EOD OHLCV |
| prices.us.daily | 평일 16:05 EST | US EOD OHLCV |
| macro.daily | 매일 06:30 KST | FRED + BOK ECOS 매크로 30일 |
| disclosures.kr.daily | 평일 19:00 KST | DART 공시 |
| disclosures.us.daily | 평일 17:30 EST | EDGAR 8-K/10-Q |
| financials.monthly | 매월 15일 07:00 KST | 분기 재무 |
| news.intraday.rss | 매 15분 | 주요 RSS 피드 |
| news.daily.kr | 매일 06:45 KST | BIGKinds + Naver |
| news.daily.global | 매일 01:00 UTC | GDELT |
| information.classify.hourly | 매시 :15 | 미분류 기사 LLM 분류 |
| information.score.daily | 22:00 UTC | 정보 점수 산출 |
| technical.score.daily | 21:30 UTC | 기술 점수 산출 |
| fundamental.score.weekly | 토 03:00 UTC | 재무 비율 → 기본 점수 |
| decisions.daily | 22:30 UTC | F/T/I 합성 → 결정 → 페이퍼 체결 |
| training.weekly | 일 04:00 UTC | 클러스터 가중치 OLS 재학습 |
| backtest.walk_forward.weekly | 일 05:00 UTC | (선택) 학습 후 walk-forward 검증 |
| news.backfill.nightly | 매일 03:30 UTC | (선택) historical 뉴스 chip-away |

기본적으로 `walk_forward.weekly`와 `news.backfill.nightly`는 **비활성**.
운영자가 시스템 안정화 후 운영 대시보드에서 활성화합니다.

---

## 페이지 가이드

### 메인
- **대시보드** — 시스템 헬스 7개 영역 + KR/US 시장별 페이퍼 계좌 + equity curve + 최근 결정 + top movers + 데이터 신선도
- **포트폴리오** — 시장별 오픈 포지션 + 자산 배분 도넛 + 미실현 P&L
- **거래 내역** — paper_trades 필터/정렬
- **성과 분석** — KPI + 일간 P&L + 종목별 히트맵 + equity curve + **모듈 성과 기여 (F/T/I attribution)**

### 분석
- **결정 감사** — 모든 결정의 이유, 모듈별 기여, 가중치, 게이트, 리스크, 실행 결과
- **종목 분석** — F/T/I 점수 추이 + 30일 sparkline + decision 마커 + 뉴스 + 지수 멤버십
- **시그널 스캔** — 현재 학습된 가중치로 dry-run, 다음 결정 사이클 예측 (BUY/SELL/HOLD 분포)
- **학습 결과** — 클러스터별 가중치, 메트릭 (R², hit rate), 운영자 override 패널
- **백테스트** — Replay (과거 결정 그대로) vs Re-scoring (현재 가중치 적용) + survivorship-bias 가드 + 저장된 실행 비교

### 데이터
- **종목 마스터** — 활성 종목 + 검색/필터 + cluster 멤버십 + 학습된 가중치
- **뉴스 탐색** — 분류된 기사 + sentiment timeline + event-type 분포
- **매크로 지표** — FRED/ECOS 시리즈 시각화

### 운영
- **운영 대시보드** — 7개 탭 (데이터 / 잡 / Drift / 백필 / 리스크 / 품질 / 비상 정지)
- **LLM 상태** — Ollama 헬스 + 분류 큐 + 모델 버전 분포
- **시스템 설정** — DecisionConfig (read-only inspection)

### MT5 (FX/금 전용 — Legacy)
- **MT5 트레이딩** — 수동 매매 패널
- **Red-Green 전략** — XAUUSD 상태머신 모니터
- **MT5 설정** — MetaTrader 5 연결 설정

---

## 운영 안전망

| 도구 | 위치 | 트리거 |
|---|---|---|
| Kill Switch | 운영 → 비상 정지 | 스케줄러 일시정지 + 모든 페이퍼 포지션 즉시 청산 |
| Scheduler pause/resume | 운영 → 잡 | 모든 잡 일시정지 (kill switch 없이) |
| Cluster weight override | 학습 결과 → 운영자 override | 학습된 weights를 운영자가 override |
| Risk gate dashboard | 운영 → 리스크 | 6개 리스크 한도 통과율 |
| Data quality monitor | 운영 → 품질 | 가격 갭 + DEAD/STALE 종목 |
| Drift detection | 운영 → Drift | 액션 분포 + per-ticker z-score |
| Health summary | 대시보드 상단 | 7개 영역 OK/WARN/ERROR 통합 |
| Toast notifications | 우상단 | 새 BUY/SELL, 헬스 티어 전이 자동 알림 |

---

## 페이퍼 → 실거래 단계

1. **3개월 페이퍼 운영** — `RUNTIME_MODE=paper`로 안정 동작 확인
2. **결정 감사 검증** — 결정 N건 이상 누적, drift 정상, 리스크 게이트 통과율 충분
3. **Walk-forward 백테스트 통과** — 주간 walk-forward가 안정적 양의 수익률
4. **운영자 live 권한** — `POST /api/auth/live-flag`에 `confirmation="I UNDERSTAND THE RISK"` 정확히 입력
5. **단계적 실거래** — 5% → 20% → 100% capital ramp
6. **브로커 어댑터 활성화** — KIS Developers (KR) + Alpaca (US, paper API 우선)

`RUNTIME_MODE=live` 활성화 시 settings 검증에서 JWT secret + LLM provider
필수 확인.

---

## 시스템 아키텍처

`Types → Config → Repo → Service → Runtime → UI` 단방향 의존.
교차 관심사는 Providers로 주입.

자세한 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), [AGENTS.md](AGENTS.md) 참고.

---

## 디렉터리 구조

```
backend/
├── core/                       # 인프라 (config, db, auth, security, risk, types)
│   ├── models/                 # ORM (15개 테이블)
│   └── llm/                    # Ollama/Groq/Gemini 어댑터
├── markets/                    # 시장별 어댑터 (kr, us)
├── brokers/                    # mt5 (FX/금), paper, paper_equity (math)
├── data/                       # 인제스천 (price/fundamental/disclosures/news/macro/universe)
├── fundamental/                # 기본 분석 모듈 (재무 비율 → 점수)
├── technical/                  # 기술 분석 모듈 (다중 시그널, Red-Green 포함)
├── information/                # 정보 분석 모듈 (LLM 분류기 → 점수)
├── decision/                   # 합성 결정 엔진 (composite, gates, sizer, runner, drift)
├── training/                   # 클러스터 가중치 OLS 학습
├── analytics/                  # Attribution + data quality (pure math)
├── backtest/                   # Replay + Rescoring engines
├── scan/                       # 시그널 스캔 dry-run engine
├── runtime/                    # APScheduler (16+ 잡)
├── routes/                     # FastAPI 라우터 (26개)
├── alembic/                    # DB 마이그레이션 (10개)
└── tests/
    ├── unit/                   # 41개 파일, 520+ 테스트
    └── integration/            # DB 필요

frontend (js + css, vanilla — no framework):
├── js/api.js                   # JWT-aware REST 클라이언트
├── js/state.js                 # 글로벌 상태 (market tab, auth)
├── js/router.js                # SPA 라우터
├── js/components/              # MarketTab, Toast watcher
└── js/pages/                   # 21개 페이지
```

---

## 테스트

```powershell
cd backend
uv run python -m unittest discover -s tests/unit
```

523개 테스트 중 deps 없이 즉시 실행되는 약 225개 (순수 함수 모듈).
나머지는 sqlalchemy/structlog/fastapi 의존성이 필요 — `uv sync` 후 활성화.

---

## 라이선스

내부 사용. 외부 공개 없음.
