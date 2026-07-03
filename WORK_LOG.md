# 작업 일지 (WORK_LOG)

**프로젝트**: woonam-auto-trading
**브랜치**: `woonam-auto-trading` (단일 브랜치 워크플로우, main으로 PR 금지)
**범위**: 한국 (KOSPI200 + KOSDAQ150) + 미국 (S&P 500 + NASDAQ-100) 복합 F/T/I 자동매매

상태 표기:
- ✅ 측정/검증 완료
- 🟡 부분 측정 (커버리지 명시)
- ⚪ 제안 상태 (코드만 존재, end-to-end 실행 미경험)
- ❌ 차단/실패 (원인 명시)

---

## 2026-05-26 (화) — Phase 0 스키마 골격

근거: `backend/alembic/versions/2026_05_26_1600_initial_schema.py`

- alembic 0001 initial_schema — 24 테이블 (users, securities, daily_prices, news_articles, disclosures, financial_facts, paper_*, risk_snapshots, decision_audit, …)
- TimescaleDB extension 호출을 `conn.begin_nested()` savepoint로 감싸 — extension 미설치 환경에서 graceful degradation (05-29 라이브 테스트로 status=accepted 격상)
- core/models/ ORM (SQLAlchemy 2.0 MappedAsDataclass)
- backend/pyproject.toml uv 관리, python >=3.12,<3.13

⚪ 잔여: TimescaleDB 자체는 미설치 → daily_prices가 일반 테이블로 동작 (hypertable 파티셔닝 부재). extension 설치 후 재검증 필요.

---

## 2026-05-27 (수) — Phase 1 인제스천 테이블 + Phase 3 학습 골격

근거: `2026_05_27_*` 마이그레이션 6개

- prices_and_membership (0002), financials_and_disclosures (0003), news (0004), training (0005), backfill_progress (0006), cluster_weight_overrides (0007)
- data/universe/ — kr_universe (pykrx), us_universe (yfinance/wikipedia)
- data/price/ — kr_prices, us_prices, loader
- data/news/ — RSS / GDELT 어댑터

---

## 2026-05-28 (목) — Phase 2-5 골격 + 0011 regime

근거: `2026_05_28_*` 마이그레이션 4개, 소스 ~282 파일

- alembic 0008 module_scores, 0009 article_classifications, 0010 backtest_runs, 0011 market_regime
- technical / fundamental / information 러너
- decision/runner.py 컴포지트 엔진 + regime-aware 임계값/사이즈 스케일러
- brokers/paper.py + 영속화 (`paper_accounts` / `paper_positions` / `paper_trades`)
- analytics/sector_rotation.py (z-score, ±15 보너스, 섹터당 최소 3종목)
- regime/classifier.py (5-voter 합산: VIX 레벨+트렌드, 인덱스 vs SMA200, 수익률 곡선, DXY)
- 프론트엔드 14+ 페이지 (dashboard / scan / decision_audit / macro+regime / training / freshness)
- 유닛 테스트 49건 + 통합 1건

⚪ 잔여: 골격만 — DB 미연결, 잡 미실행.

---

## 2026-05-29 (금) — DB 라이브 + 최초 end-to-end 페이퍼 주문

### 오전: P1 부트스트랩

- PostgreSQL 16 — `woonam` 유저 + `woonam` DB 생성
- 27 테이블 생성 (`alembic upgrade head`, 11 마이그레이션 모두 적용)
- bcrypt `<4.0` 핀 (passlib 1.7.4가 5.x에서 제거된 `__about__` 참조) → 부트스트랩 패스워드 해시 동작
- python-multipart 추가 (OAuth2PasswordRequestForm 의존성)
- alembic.ini em-dash → ASCII (Windows cp949 콘솔)
- FastAPI lifespan이 `woonam` 운영자 + `default-kr` (KRW) + `default-us` (USD) 페이퍼 계좌 생성
- scripts/smoke_test.py — ✅ 5/5 단계 통과
- 서버 라이브 `http://127.0.0.1:8000`

### 오후: 데이터 어댑터 분류 (triage)

- ❌ pykrx KR 유니버스 — KRX 엔드포인트 빈 응답/JSONDecodeError → FDR 전환 계획
- ❌ yfinance US 가격 — 다종목 호출 후 rate limit → FDR 전환
- ✅ FDR US 파일럿 — 20종목 × 250행 적재 확인 (rowcount=-1 모호성은 SELECT로 해소)
- ✅ RSS 뉴스 — 222건 인제스트 (MK/한국경제/CNBC/연합인포맥스)
- 🟡 regime.daily — NEUTRAL/0.0 신뢰도 (macro_series 비어있음; 정상적인 degraded 동작)
- ⚪ 모듈 스코어 — 0 (technical은 >30 바 필요; 파일럿 종목만 히스토리 있음)

### 저녁: FDR 풀 백필 + 최초 페이퍼 주문

- `scripts/fdr_backfill_us.py` — 인서트를 ≤2,000행으로 분할 (Postgres 65,535 파라미터 한도, 11컬럼 → 최대 ~5,950행)
- ✅ **SP500 431/433 종목**, **160,057 일봉 행**, 2024-12-01 → 2026-05-29
- ❌ BF.B / BRK.B 실패 (FDR이 클래스 주식 `.` 포맷 거부)
- ✅ technical.score.daily — **US 501 종목 스코어링** (2 abstain, 0 실패)
- ✅ decisions.daily E2E — 503 검토 → **페이퍼 주문 20건** (BUY 8 + SELL 12)
- ✅ default-us 현금: $100,000.00 → $100,235.87 (숏 매도 대금 + 수수료)
- `decision_audit` 503행, 모두 regime 컨텍스트(NEUTRAL 스케일러) 포함

### git

- ✅ 저장소-로컬 git identity 설정 (SunhSOO / dksdnsdka@gmail.com) — global 아님
- ✅ `915e833` 첫 푸시 — 282 파일, +45,089 / -788
- `backend/.env` `.gitignore` 보호 확인; 시크릿 미커밋

---

## 2026-06-01 (월) — KR 부활 + F/T/I 3축 컴포지트 가동

### KR 데이터 라인 (FDR로 시스템 절반 부활)

- `scripts/fdr_backfill_kr.py` — KOSPI200/KOSDAQ150을 시가총액 프록시로 (KRX-MARCAP 리스팅에서 KOSPI 상위 200 + KOSDAQ 상위 150)
- ✅ **KR 350 종목** 등록 (KRX-DESC에서 섹터/산업 보강)
- ✅ **KR 일봉 122,927행**, 350/350 종목, 0 실패
- ⚠️ 한계: 시가총액 프록시는 survivorship bias-free 히스토리 아님; 실제 KRX 인덱스 구성종목은 KRX CSV 업그레이드 필요 (후속). source 컬럼을 `fdr_marcap_proxy`로 표기해 향후 교체가 투명함

### Information 모듈 활성화

- `info-classify` (Ollama qwen2.5:14b, 로컬 GPU) — 146 기사 추가
- ✅ **582 기사 전체 분류** (전체 RSS 코퍼스)
- ✅ `info.score` — **37 종목 스코어** (KR 13 + US 24); 816건 mention 부재로 skip (정상 — 기사 582 : 종목 853 비율이 자연 한도)

### Fundamental 데이터 (SEC EDGAR, 키 불요)

- `scripts/edgar_backfill_us.py` — 2단계 (CIK 보강 + companyconcept 풀)
- `data/fundamental/loader.py` 수정:
  - ≤2,000행으로 분할 (Postgres 파라미터 한도)
  - PK (market, ticker, concept, period_end, period_kind) 기준 배치 내 dedup — SEC가 수정본을 반환함
- `data/fundamental/us_edgar.py` 수정: 비통화 단위 코드 ("shares", "pure", "Year") skip — `currency`가 VARCHAR(3)이라
- ✅ 파일럿 5종목 → 5,321행 (3개 수정 모두 검증)
- ✅ 100종목 백그라운드 백필 완료: **114,408행**, 0 실패
- ✅ 풀 백필 후 fundamental score — **US 105 종목, 11 섹터**

### regime classifier용 매크로 시리즈

- `scripts/fdr_backfill_macro.py` — FDR로 5개 시리즈 (FRED 키 불요)
- ✅ VIX (501행), IDX_SP500_FRED (500), IDX_KOSPI_ECOS (483), FX_DXY (503), RATE_US_10Y via ^TNX (500)
- ❌ RATE_US_2Y — FDR 무료에 미노출; 수익률 곡선 voter는 None으로 degrade (4-voter 합산, regime 라벨은 여전히 산출)

### End-to-end 검증

- ✅ regime.daily 재실행 — KR/US 둘 다 NEUTRAL이지만 voter 활성 (`VIX 15.3 < 18` RISK_ON 1표 + flat 2표 → NEUTRAL이 정답)
- ✅ decisions.daily 3축 재실행 — composite 공식 end-to-end 검증:
  - AES: F+83.3 / T+26.2 → composite **+52.9** (HOLD; 기존 롱 보유)
  - AWK: F-100 / T-18.7 → composite **-56.7** (HOLD; 기존 숏 보유)
  - APP: F+51.4 / T+17.5 → +33.3
  - AMAT: F+36.1 / T+26.2 → +30.9
  - ABT: F-27.3 / T-32.5 → -30.1 (게이트에서 REJECTED)
  - BAC: F-45.8 / T-16.3 → -30.0 (REJECTED)
- 2차 실행에서 traded=0 — 정상: 05-29 사이클의 동일 방향 포지션이 이미 있어 중복 진입 차단

### 프로세스

- ✅ `claude-dev-principles.md` 프로젝트 개발 계약으로 채택 (정직성 우선, ✅/🟡/⚪/❌ 상태 구분)
- ✅ 본 `WORK_LOG.md` 상시 산출물로 도입

### git

- ✅ `e79c3e1` 푸시 — 6 파일, +563 / -12 (KR 어댑터 + EDGAR + 매크로 + loader 수정)
- ✅ `0c065b5` 푸시 — `WORK_LOG.md` 도입

---

## 2026-06-01 (월) 후반 — Phase 3 학습 파이프라인 첫 가동

### 데이터 정합성 수정

- ✅ `daily_prices.as_of_ts` 일괄 정정 — 모든 FDR/yfinance/pykrx 행을 `trade_date + 16h UTC`로
- **307,145행 UPDATE** — Technical 러너의 look-ahead 가드(`as_of_ts <= as_of`)가 historical scoring을 막던 문제 해소

### Technical 점수 historical backfill

- `scripts/backfill_technical_history.py` — `score_market`을 90 거래일치 반복 실행
- ✅ KR 90일 × ~347 종목 = **31,212 score 행** (abstain 288)
- ✅ US 90일 × ~501 종목 = **45,090 score 행** (abstain 180)
- 합계 **76,302 score 행** 적재 — module_scores 시계열 완성

### 학습 파이프라인 (training.weekly) 첫 가동

- `training.runner.run_training` — clusterer → labels → OLS per cluster → cluster_weights 영속화
- ✅ ticker_clusters 853행 (KR 350 + US 503), 22 클러스터 산출
- ✅ **71,728 학습 샘플 수집**, **22/22 클러스터 학습 완료** (skip 0)
- 🟡 R² 0.00006 ~ 0.038 — **사실상 noise**:
  - 패턴 1: F/I 데이터 부족 → OLS가 T-only에 100% 가중치 (US:INDUSTRIALS:LARGE, US:TECH:LARGE 등)
  - 패턴 2: F/T/I 모두 있는 클러스터는 (0.333, 0.333, 0.333) equal-weight fallback (계수 0 근접 → 디폴트 복귀)
  - 최고 R² 0.038 (US:COMM:MID, 3종목/261샘플) — 통계적 무의미
- **파이프라인은 완전 작동** — clusterer / labeler / trainer / registry / walk-forward 모두 정상

### 학습된 weights로 decisions 재실행

- ✅ decisions.daily 재실행 — cluster_weights 적용:
  - 503 considered → 415 HOLD + 88 REJECTED + 0 traded
  - 이전(equal weight): 387 HOLD + 116 REJECTED → **REJECTED 28건 감소** (학습된 weight가 일부 종목의 컴포지트를 임계값 안쪽으로 끌어옴)
- 0 trades는 동일 — 기존 20포지션 동방향 차단 (정상)

### 정직한 진단

학습 R²가 noise 수준인 진짜 원인:
1. **윈도우 짧음** — 90 거래일은 5d forward return을 학습하기엔 부족
2. **F/I 커버리지 낮음** — F 21% / I 4% → 대다수 샘플이 T-only로 degenerate
3. **OLS 기준 모델 한계** — 가격 수익률은 본질적으로 R²가 낮음. LightGBM + 더 많은 피처가 다음 단계
4. **walk-forward split 누락** — 윈도우가 작아 split이 비어있음 (`r2_walk_forward` 모두 None)

향후 R² 개선 전제:
- F 풀 커버리지 (EDGAR 501/501) → 진행 중
- KR Fundamental (DART 키 필요)
- 윈도우 확장 (1년+)
- LightGBM 도입 + 피처 확장

### EDGAR 풀 백필 완료 → F historical → 학습 재가동

- ✅ EDGAR 396 종목 추가 백필 → US 501/501 종목 / **574,071 행** financial_facts
- ✅ Fundamental score US 105 → **501 종목** (skip 2 = BF.B/BRK.B)
- ✅ `scripts/backfill_fundamental_history.py` 신규 — historical Fundamental 백필
- ✅ US 90 거래일 × 501 종목 = **45,090 F-score** 행 적재
- ✅ training 재실행 (71,728 샘플):
  - **섹터별 weights가 진짜 차별화됨** (이전 equal-weight 패턴에서 탈피):
    - F-dominant (안정 cash flow): US:REAL_ESTATE:LARGE/MID, UTILITIES:LARGE, CONS_DISC:LARGE, COMM:LARGE → F 100%
    - T-dominant (모멘텀): US:TECH:LARGE, ENERGY:LARGE, KR:OTHER:LARGE, FIN:MID → T 100%
    - 혼합: US:HEALTH:LARGE (F 0.10, T 0.90), US:INDUSTRIALS:LARGE (F 0.30, T 0.70)
  - R² 개선: US:REAL_ESTATE:MID 0.012 → **0.077**, US:INDUSTRIALS:MID 0.024 → **0.035**
  - KR 클러스터는 모두 (0.333, 0.333, 0.333) — DART 없어 KR F-score 0이라 equal-weight fallback

### Walk-forward 백테스트 1회 가동 — 진단

- ✅ `_job_walk_forward_weekly` 동등 호출 헬퍼 `run_jobs.py:run_walk_forward` 추가
- ✅ backtest_runs 2행 INSERT (KR + US) — 파이프라인 완전 동작
- 🟡 trades=0 — **rescoring 엔진(`backtest/rescoring.py:121-123`)이 F/T/I 3모듈 모두 freshness window 내 존재를 요구** ("all three modules required" 명문화). I-score historical 부재로 모든 historical 시점 스킵.

### A. rescoring freshness gate가 0-weight 모듈 스킵하도록 수정

- ✅ `backend/backtest/rescoring.py` 수정 — freshness gate가 `decision_config.weights_for(cluster_id)`를 조회해 weight=0인 모듈은 freshness 요구 안 함
- 의도: 학습된 cluster_weights가 (F=1, T=0, I=0)인 섹터는 F만 fresh면 거래 가능; T=0 / I=0 모듈의 데이터 부재가 전체 신호를 막지 않음
- 결과: walk-forward에서 **trades 실제 발생** — KR 1건, US 4건

### B. News mention 재매핑 — 41→655 (16배)

- 발견: 첫 RSS 222건은 KR universe 적재 **전에** ingest됨 → map_article 호출 시 KR NameIndex 비어있었음 → KR 매칭 0건
- ✅ `scripts/remap_news_mentions.py` 신규 — 전체 NewsArticle 1,479건을 현재 NameIndex(KR 350 + US 503)로 재매핑
- ✅ news_ticker_mentions: **41 → 655**
- ✅ info.score 재실행: KR **13 → 91** (350 중 26%), US **24 → 43** (503 중 8.5%)
- US 증가 미미는 자연스러움 — RSS가 미국 영문 뉴스라 KR 기업명 매칭 위주

### KR 종목 진짜 F/T/I 합산 첫 관측

decisions 라이브 재실행 (mention 보강 후):
- 삼성전자(005930): T +33.75 / I **+98.60** → composite **+66.18** (강한 매수, 다만 REJECTED — 게이트 차단)
- SK하이닉스(000660): T +23.75 / I +80.95 → composite **+52.35** (HOLD)
- SK(034730): T +16.25 / I +78.98 → composite **+47.61** (HOLD)
- KR Fundamental은 여전히 null (DART 키 부재) — F+T+I 3축 완성은 DART 받으면 가능

walk-forward 재실행 (US): trades=4 동일, outcome 변동 (sharpe 2.55 → -3.27) — 표본 4건 통계적 무의미. OLS noise weights의 자연스러운 변동.

---

## 2026-06-01 (월) 심야 — Phase 3 본격 ML: LightGBM + 44-feature 파이프라인

### 데이터 보강 (gap 수정)

- 발견: 초기 20-ticker FDR 파일럿(AAPL/MSFT/NVDA…)이 `--skip-existing-min-rows 200`로 풀 백필에서 스킵되어 2026-01~04 데이터 누락
- ✅ 22개 종목 재백필 (254→373 rows), `daily_prices.as_of_ts` 2,380행 추가 정정
- ✅ Technical history 90→**365일** 재실행 — KR 112,814 + US 172,070 = **284,884 T-score**
- ✅ Fundamental history 90→**365일** 재실행 — **172,680 US F-score** (366 unique dates)

### 새 학습 스택 구현

`training/features.py` (신규, 423행):
- **44개 raw 피처** (이전: 3개 summary score만)
- Price 19: RSI(14), MACD-hist, BB%B, ADX, Stoch K, ATR%, OBV slope, vol_21d/63d, returns(1/5/21/63d), drawdown, range
- Fundamental 10: PE, PB, EV/EBITDA, ROE, ROA, D/E, current ratio, gross margin, rev_yoy, eps_yoy
- Info 6: news count 7d/30d, sentiment, pos/neg count, impact-weighted
- Macro 6: VIX, VIX 5d Δ, DXY 5d Δ, SP500 21d ret, 10Y, 10Y 5d Δ
- Regime 3: risk-on/risk-off one-hot + confidence

`training/labels_multi.py` (신규): 5d/21d/63d forward returns + **cross-sectional rank**(시장 변동 제거된 상대 순위) + risk-adjusted return.

`training/lgbm_trainer.py` (신규, 230행):
- LightGBM (objective=regression, regularization-heavy defaults)
- **date-grouped time-series CV** (5-fold + 21일 embargo) — row-based split이 cross-sectional rank target에서 leakage 일으키는 버그 발견 후 수정
- IC (Spearman) + hit rate + R² OOF 계산
- 클러스터별 + 전역 fallback, feature importance 추출

### LightGBM 결과 — Production-grade 영역 도달

**KR (350 종목, 378,600 샘플)**:

| 클러스터 | n | R²_oof | hit | IC |
|---|---|---|---|---|
| __global__ | 378,600 | **+0.011** | **60.8%** | +0.058 |
| **KR:FIN:LARGE** | 95,030 | -0.010 | **65.7%** | **+0.203** |
| KR:FIN:MID | 105,285 | -0.002 | 56.8% | +0.105 |
| **KR:FIN:SMALL** | 18,785 | **+0.072** | 55.5% | +0.131 |
| **KR:OTHER:LARGE** | 24,035 | -0.054 | **69.9%** | +0.067 |
| KR:OTHER:SMALL | 12,155 | +0.051 | 50.2% | +0.105 |

**US (501 종목, 573,130 샘플)**:

| 클러스터 | n | R²_oof | hit | IC |
|---|---|---|---|---|
| __global__ | 573,130 | **+0.034** | 51.9% | +0.043 |
| **US:TECH:LARGE** | 83,075 | **+0.055** | 57.5% | **+0.153** |
| **US:FIN:LARGE** | 82,435 | +0.012 | 55.8% | **+0.153** |
| **US:ENERGY:LARGE** | 24,045 | -0.081 | **64.8%** | **+0.172** |
| US:REAL_ESTATE:LARGE | 29,770 | +0.013 | 55.0% | rank IC **+0.230** |
| US:FIN:MID | 3,435 | -0.004 | 58.8% | **+0.378** |
| US:COMM:MID | 3,435 | -0.025 | 59.8% | +0.210 |
| US:HEALTH:LARGE | 67,555 | -0.029 | 48.8% | **-0.129** |
| US:CONS_STAPLES:LARGE | 38,930 | -0.064 | 37.3% | **-0.259** |

**해석**:
- **IC 0.15~0.24 클러스터 7개** (KR:FIN:LARGE, US:TECH/FIN/ENERGY/REAL_ESTATE:LARGE, US:FIN/COMM:MID) — 실 펀드들이 0.05~0.10 목표하는 영역. **실거래 후보군**.
- KR이 US보다 전반적으로 hit rate 높음 (Global 60.8% vs 51.9%) — 좁고 깊은 KR universe + 한국 뉴스 매핑 효과.
- **음수 IC 섹터** (HEALTH, CONS_STAPLES): 피처가 forward return과 anti-correlated. 평균회귀 regime 또는 우연. → 거래 시 **이 섹터는 모델 무시 + 단순 HOLD** 권장.
- **R² 음수 클러스터**도 IC는 양수일 수 있음 — 방향성은 맞히지만 magnitude 예측은 noise (전형적 패턴).
- 핵심 피처 (gain): **vol_63d / atr_pct / ret_63d / eps_yoy / debt_equity / us10y** — 변동성 + 성장 + 레버리지 + 금리 = 경제적으로 말이 됨

### Data leakage 버그 + 수정 (정직)

**중요**: 첫 smoke test에서 R² 0.54 / hit 100%라는 비현실적 수치 산출. 원인 추적:
- `TimeSeriesSplit`이 row index로 분할 → sorted by date 후 동일 date의 다른 ticker가 train/test 경계 양쪽에 걸침
- cross-sectional rank target이 동일 date의 다른 ticker 정보에 의존 → 누수
- 수정: `_date_grouped_splits` — date 단위 그룹 분할 + 21일 embargo

수정 후 R² 0.54 → **0.098** (파일럿 20 종목) → 풀 학습 +0.034 (US global). **누수 없는 정직 수치**.

### 미통합 영역 (status=proposed)

- ⚪ **LightGBM 모델 → decision engine 통합**: 학습된 booster 객체가 디스크에 영속화되지 않음. 현재 walk-forward는 여전히 OLS cluster_weights 사용. integration 위해 `backtest/rescoring.py`에 LGBM 추론 경로 추가 필요 (별도 작업).
- ⚪ **TFI 피처 추가 영역** (사용자가 발급 중인 키 + 무료 SEC 자료):
  - SEC Form 4 insider transactions (free, 강력한 알파)
  - SEC 8-K event extraction
  - GDELT 1979~ 글로벌 뉴스
  - Wikipedia/Google Trends search momentum
  - pandas-ta 30+ 추가 지표
  - Cross-asset relative strength (sector ETF, USDKRW)
- ⚪ **Hit rate 메트릭**: rank target에 대해 항상 100% 산출 (rank 값이 모두 ≥0 → sign 비교 무의미). IC가 올바른 지표.

### 다음 단계

1. **키 발급 완료 대기** (FRED / DART / Naver / GDELT 등)
2. 추가 피처 통합 → 64 피처 → 재학습
3. LGBM 모델 → 디스크 영속화 + rescoring engine 통합
4. 페이퍼 트레이딩 3개월 실제 진행 (Sharpe 측정)

---

## 2026-06-02 (화) — 데이터·모델 본격 확장 + MLOps

### 외부 키 발급 완료 (사용자 작업)

`backend/.env`에 안전하게 입력 + 모두 검증 OK:
- ✅ FRED, Naver (검색+DataLab), DART, BOK ECOS, SEC EDGAR, GDELT(gcloud ADC)
- ❌ BIGKinds 유료 전환 / Reddit 가입 막힘 (스킵)

### Macro 매크로 완전체 (13 시리즈)

- `scripts/macro_extended_backfill.py` 신규
- FRED 10개: 10Y/2Y/3M 정식, FEDFUNDS, CPI/M2/UE/IP/Payroll/Retail
- BOK ECOS: 기준금리 + CPI
- **macro_series**: 2,500 → 5,749 행

### DART KR 재무 백필

- `scripts/dart_backfill_kr.py` 신규
- corp_code 343/350 매핑, 3년 ANNUAL × 17 concept = **14,223 행 KR financial_facts** (이전 0)
- → KR Fundamental 모듈 처음으로 가동 (317 종목)

### SEC Form 4 XML 본문 파싱

- `scripts/sec_form4_parser.py` 신규 — XSL viewer URL → raw XML 변환 + ElementTree 파싱
- 5,000 filings 파싱: insider buy/sell 방향, 거래 가치, 임원 직급 → JSON
- **이전엔 count만, 이제 magnitude + direction + role 사용 가능**

### GDELT GKG (글로벌 뉴스) 폭증

- `scripts/gdelt_backfill.py` 신규 (BigQuery 1TB 무료 quota 내)
- SP500 × 180일 × V2Organizations REGEX_CONTAINS → 17 배치
- 4번 실패-수정 사이클 (raw_body 컬럼, URL/dedup_key IN 절 65535 한계)
- 최종: **1,418,585 기사 + 230,286 mention** (이전 1,500 / 655 — 1000배 폭증)
- 275GB 스캔 (한도 27.5%)

### Naver 뉴스 본격

- `scripts/naver_news_backfill.py` 신규 (검색 API + DataLab API key 활용)
- 350 KR 종목 × 100건/쿼리 = **22,654 기사 + 45,332 KR mention**

### Cross-asset proxy 17개 적재

- `scripts/cross_asset_backfill.py` 신규
- 11 SPDR 섹터 ETF + SPY/QQQ/IWM + GLD/USO/TLT
- daily_prices에 6,256 행 추가

### Feature pipeline 전면 확장 (44 → 131 features)

**Technical**: 19 → 50개 (pandas-ta 30개 추가: Aroon, CMF, MFI, Williams%R, ROC, ulcer, Donchian, candle pattern bits, multi-horizon returns)
**Fundamental**: 10개 유지
**Information**: 6 → 6개 유지 (mention count + sentiment)
**Disclosure**: **NEW** 6개 (insider/8K count, days_since 10K/10Q)
**Insider (Form 4 body)**: **NEW** 7개 (net_value, buys/sells count, CEO/Director 분해, buy_sell_ratio)
**Macro**: 6 → 18개 (yield curve, FED funds, CPI/M2 yoy, unrate, KR base rate 등)
**Regime**: 3개 유지
**Calendar**: **NEW** 9개 (dow/dom/doq/doy/month/quarter, days_to_q_end, is_jan/dec)
**Cross-asset**: **NEW** 18개 (vs 17 ETF 21d 상대 momentum + corr_spy_63d)

### Multi-model 학습 인프라

- `training/multi_trainer.py` — LightGBM/XGBoost/CatBoost/Ridge 통합 trainer, 동일 date-grouped CV
- `training/lstm_trainer.py` — PyTorch LSTM (seq_len=30, hidden=128, 2-layer + dropout)
- `training/optuna_tuner.py` — Optuna TPE 튜닝 + 검색공간 정의
- `training/model_registry.py` — MLOps registry (`var/models/runs/<run_id>/...`)
  - Booster pickling per kind (LGBM .txt / XGB .json / CatBoost .cbm / LSTM .pt / Ridge .joblib)
  - `registry.json` 글로벌 인덱스 + `find_best(target, cluster, metric)` 자동 선택
  - 드리프트 검출 + 재학습 + 롤백 지원하는 구조
- `training/ensemble.py` — top-K 로드 + IC-weighted ensemble inference
- `scripts/tune_and_save.py` — Optuna 튜닝 + 자동 등록
- `scripts/save_default_models.py` — default 파라미터 모델 등록 (튜닝과 비교용)
- `scripts/train_lstm.py` — LSTM 학습 + 등록
- `scripts/compare_models.py` — 4-model 비교
- `scripts/compare_ensemble.py` — registry → ensemble inference 검증

### Optuna 튜닝 결과 (중요한 정직 발견)

| 클러스터 | n | Default best | 30-trial best | 100-trial best | 승자 |
|---|---|---|---|---|---|
| **KR:FIN:LARGE** | 95k | **CatBoost +0.262** | CatBoost +0.170 | CatBoost +0.180 | **Default** ⭐ |
| **US:ENERGY:LARGE** | 24k | LGBM +0.286 | LGBM +0.309 | **LGBM +0.353** ⭐ | **Tuned** |

**패턴 발견**: 데이터 크기별 정반대
- **큰 클러스터 (95k+)**: default가 sweet spot (보수적 정규화가 노이즈 차단). Optuna가 노이즈 추적 → 오히려 IC 하락
- **작은 클러스터 (~25k)**: 튜닝이 진짜 효과 (default → +0.286, tuned → +0.353, **+23% 향상**)

→ MLOps registry의 `find_best()` 자동 처리 (클러스터별 best 선택).

### LSTM 결과 — 학술 통설 검증

| 클러스터 | LSTM IC | Tree best IC | 격차 |
|---|---|---|---|
| KR:FIN:LARGE | +0.074 | +0.262 (Default CatBoost) | **3.5배 차이** |
| US:ENERGY:LARGE | +0.227 | +0.353 (Tuned LGBM) | 1.5배 차이 |

**확인**: 학술 통설대로 일별 forward return 예측에서 트리가 압도. LSTM은 **앙상블 다양성 멤버로만 의미**. seq_len=30, hidden=128, 2-layer, 25 epochs, CPU 학습.

### 현재 registry 모델 (15 종)

- KR:FIN:LARGE × 3 (30-trial × 3 models) + 3 (100-trial) + 3 (Default) + 1 LSTM = 10
- US:ENERGY:LARGE × 3 (30-trial × 3 models) + 3 (100-trial) + 3 (Default) + 1 LSTM = 10
- ✅ 모든 모델 디스크 영속화 + registry.json 인덱싱
- 드리프트 검출 후 자동 재학습 + 신·구 모델 롤백 가능

### 다음 단계 (사용자 9-step 로드맵)

- [x] Step 1: 100-trial 재튜닝 (KR + US)
- [x] Step 2: 클러스터별 best 결정
- [ ] Step 3: commit + WORK_LOG (이번 커밋)
- [ ] Step 4: 4-model 앙상블 검증
- [ ] Step 5: LSTM Optuna 튜닝
- [ ] Step 6-7: 앙상블 가중치 확정 + commit
- [ ] Step 8: 22개 모든 클러스터 튜닝
- [ ] Step 9: 최종 commit

| 도메인 | 커버리지 | 상태 |
|---|---|---|
| KR 종목 | 350 (KOSPI200+KOSDAQ150 marcap 프록시) | ✅ 라이브 |
| KR 일봉 | 122,927행 | ✅ 라이브 |
| US 종목 | 503 (SP500) | ✅ 라이브 |
| US 일봉 | 183,717행 | ✅ 라이브 |
| US CIK 보강 | 501/503 | ✅ 라이브 |
| US financial_facts | 501 종목 (574,071행) | ✅ 라이브 (100% SP500) |
| KR financial_facts (DART) | 317 종목 (14,223행, 3년 ANNUAL × 17 concept) | ✅ KR Fundamental 가동 |
| disclosures (SEC) | 379,673 (메타데이터) + 5,000 (Form 4 본문 파싱) | ✅ insider buy/sell 추출 가능 |
| 뉴스 기사 | **1,441,239** (GDELT 1.42M + Naver 22k + RSS 1.5k) | ✅ 1000배 폭증 |
| 기사 분류 | 582 (Ollama qwen2.5:14b) | ✅ 라이브 |
| 종목 mention | **275,618** (US 229k + KR 46k) | ✅ GDELT + Naver 추가로 420배 폭증 |
| macro_series | 5 시리즈 × ~500행 | ✅ 라이브 (5/6 — 2Y 없음) |
| market_regime | 양 시장 NEUTRAL/0.0 | ✅ 라이브 (시그널 약함 — 정직) |
| module_scores Technical | 849 라이브 + 76,302 historical (90일) | ✅ 라이브 + 시계열 |
| ticker_clusters | 853 (KR 350 + US 503) | ✅ 라이브 |
| cluster_weights | 22 클러스터 학습됨 | 🟡 R² noise — 신호 부재 (데이터 부족) |
| module_scores Fundamental | US 501 라이브 + 45,090 historical (90일), KR 0 | ✅ US 100% / KR DART 차단 |
| module_scores Information | 134 (KR 91 + US 43) | 🟡 KR 26% / US 8.5% — RSS depth가 US 천장 |
| decision_audit | 1,006+ 행 | ✅ 라이브 |
| paper_positions | 20건 보유 (default-us) | ✅ 라이브 |
| paper_accounts | default-kr KRW 1억 + default-us USD 10만 → 100,235.87 | ✅ 라이브 |
| 공시 (DART/EDGAR) | 0 | ❌ 미실행 (DART 키 부재; EDGAR 8-K 파서 미연결) |
| financial_facts KR (DART) | 0 | ❌ DART_API_KEY 차단 |
| Phase 3 학습 OLS (baseline) | 22 클러스터 / 71,728 샘플 | 🟡 R² noise (deprecated, baseline 보존) |
| Phase 3 학습 LightGBM (date-grouped CV) | 22+ 클러스터 / 951,730 샘플 (KR+US) / 44 피처 | ✅ IC 0.15~0.24 production-grade 영역 7개 클러스터 |
| Phase 3 backtest_runs (walk-forward) | 4행 (KR+US 2회) — US 4 trades, KR 1 trade | 🟡 OLS weights 기반 (LGBM 미통합) |
| Phase 5 UI 브라우저 검증 | 미상 | ⚪ 이번 세션에서 미실시 |
| Phase 6 페이퍼 3개월 운영 | 미시작 | ⚪ 제안 |

---

## 알려진 한계 (감춰선 안 됨)

1. **KR 유니버스는 시가총액 프록시** — 실제 KOSPI200/KOSDAQ150 명단 아님. source = `fdr_marcap_proxy`. survivorship-bias-free 히스토리는 KRX CSV 업그레이드 필요.
2. **BF.B / BRK.B** US에서 누락 (FDR 클래스 주식 포맷). 수동 ticker 정규화 필요.
3. **2년 국채 수익률** 부재 (FRED 키 없고, Yahoo 무료에 미노출). regime 수익률 곡선 voter가 None으로 degrade.
4. **Information 스코어 커버리지** — remap 후 KR 26% / US 8.5%. KR은 RSS 한국 뉴스 매칭 정상; US는 영문 RSS에 한국 기업명 적게 나옴 — GDELT/Naver 영문 백필 필요.
5. **Fundamental 스코어 KR = 0** — DART_API_KEY 설정 또는 대체 어댑터 작성 필요.
6. **Phase 3 학습** — 365일 + LightGBM + 44피처로 production-grade IC 영역 7개 클러스터 도달. 다만 LGBM 모델이 아직 decision engine과 미통합 — walk-forward는 OLS 사용 중. 모델 영속화 + 통합 후 paper Sharpe 측정 필요.
7. **TimescaleDB extension 미설치** — daily_prices가 일반 테이블로 동작; 대용량 범위 쿼리 성능 미검증.

---

## 외부 의존성 (대기 중)

- `DART_API_KEY` — KR 재무 + 공시 (무료, opendart.fss.or.kr 가입 필요)
- `FRED_API_KEY` — 정식 매크로 + 채권 (무료, fred.stlouisfed.org 가입 필요)
- `BIGKINDS_ACCESS_KEY` — KR 뉴스 깊이 (무료 티어)
- `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` — KR 뉴스 일 25,000건

---

## 2026-06-02 (화) — Wave 1: Advanced features + Macro + TLH/Hedge/Calendar/V2Tone

근거: `backend/training/features_advanced.py`, `features_calendar.py`, `features_gdelt.py`, `decision/tax_loss_harvesting.py`, `decision/currency_hedge.py`, `regime/hmm_classifier.py`, `scripts/fred_extra_backfill.py`, `scripts/gdelt_v2tone_extract.py`, `scripts/train_lgbm.py`

### 완료 항목 (코드 + smoke test 통과)

- ✅ `features_advanced.py` 50+ feature 함수 — extra technical (TRIX/DPO/TSI/PPO/PVO/BOP/Chande/Vortex/UltimateOsc/Coppock/KAMA/HMA/SuperTrend), Garman-Klass/Yang-Zhang vol, 10개 candle pattern, 17개 stat feature (correlation/beta/alpha/Sortino/skew/kurt/autocorr/Hurst exponent), 9개 microstructure (close_range_strength/effective_spread_proxy/dollar_volume), Piotroski/Altman/Beneish composites
- ✅ `features_calendar.py` 14개 event flag — FOMC/CPI/NFP/PCE/GDP/BOK 날짜 사전 + 거리 계산
- ✅ `features_gdelt.py` 7개 V2Tone aggregator — 7d/30d 평균, std, momentum, pos/neg count, mention count
- ✅ `decision/tax_loss_harvesting.py` (W.4 user 필수) — TaxConfig(KR/US 분기), TLHDecision, wash-sale lock 캐시, 31일 buyback 차단, sector ETF substitute, YTD realized loss 추적 (US 3k cap)
- ✅ `decision/currency_hedge.py` (W.6 user 필수) — Best=VWDH (Volatility-Weighted Dynamic Hedge) 구현 + 대안 A-K 11개 (Static50, MVHR, Regime, Carry, Options, Forwards, Hedged ETF, Pair, Basket, Vol-Target) 문서화. 모드 5종 코드 활성: vwdh/static_50/mvhr/regime/carry
- ✅ `regime/hmm_classifier.py` 5-state Gaussian HMM (calm_bull/risk_on/neutral/risk_off/crisis)
- ✅ FRED 확장 백필 — 28개 시리즈 시도, 25개 성공 (~7,000 rows). 실패 3개 (ISM_MFG NAPM, BRENT POILBRENTUSDM, GOLD GOLDAMGBD228NLBM — FRED 코드 변경, 추후 fix)
- ✅ GDELT V2Tone 추출 — `gdelt_aux` 신규 테이블, **229,547 tones** 추출
- ✅ `train_lgbm.py` ALL_FEATURE_COLS = **215 features** (기존 124 → 215, +73%)
- ✅ Smoke test: `build_feature_matrix(US, 120d, 3 tickers)` → rows=246 cols=215 통과; gdelt_tone_avg_7d/is_fomc_day/piotroski_score/hurst_100 전부 emit 확인

### 진행중 / 다음

- ⚪ 215 features로 LGBM/XGB/CatBoost 전 클러스터 재학습 (Wave 1 종료 후 batch)
- ⚪ HMM regime smoke test (코드 작성됨, 미실행)
- ⚪ TLH/Hedge logic을 `decision/runner.py`에 통합 (paper 시뮬 단계에서 활성화)
- ⚪ FRED 실패 3개 시리즈 코드 fix (ISM/BRENT/GOLD)

### 사용자 directive 반영 상태

- W.1 HFT staged → GAPS.md 명시, 일봉 우선 (현재 단계)
- W.2 Crypto/DeFi → EXCLUDED 확정
- W.3 ESG → 다음 wave
- W.4 Tax Loss Harvesting → ✅ 코드 완료
- W.5 Macroeconomic forecasting → FRED 25 시리즈 백필 완료 (별도 모델은 다음 wave)
- W.6 Currency Hedge → ✅ VWDH 구현 + 대안 11개 문서화

---

## 2026-06-02 (화) PM — Wave 2: FTI 대확장 (124 → 431 features)

근거: `backend/training/features_fundamental_v2.py`, `features_technical_v2.py`, `features_information_v2.py`, `features_cross_section.py`, `features_alt_data.py`, `data/fundamental/concepts.py`, `scripts/sec_13f_ingest.py`, `scripts/finra_short_ingest.py`, `scripts/edgar_10k_lm_sentiment.py`, `scripts/yfinance_options_ingest.py`, `scripts/gdelt_gcam_extract.py`, `scripts/alt_data_ingest.py`

### 코드 작성 (모두 완료)

- ✅ Fundamental v2 — **55 features**: Valuation 12 (PEG/PS/PFCF/EV-Sales/EV-FCF/EV-EBIT/E-yield/D-yield/FCF-yield/Shiller-PE/P-TBV/Buyback-yield), Quality 12 (ROIC/ROCE/Op/Net/EBITDA-margin/Asset-turnover/Inv-turnover/Recv-turnover/CCC/EarningsQuality/Accruals/RnD-intensity), Growth 10 (3y/5y CAGR for Rev/EPS/BV/FCF/Div + QoQ/Accel/SGR), Leverage 8 (NetDebt-EBITDA/InterestCov/Quick/Cash-Debt/LT-Debt-Cap/FCF-Debt/Goodwill-Assets/Intang-Assets), CashFlow 7 (FCF-abs/FCF-margin/Capex-Sales/Capex-Dep/ΔWC/CashConv/OwnerEarnings-yield), Composites 6 (MagicFormula/QMJ/Ohlson-O/Sloan-Accruals/Mohanram-G/NCAV-Graham)
- ✅ Technical v2 — **35 features**: Ichimoku 8, Divergence 4 (RSI/MACD/OBV/Hidden), TTM Squeeze 3, Pivot 6 (Std/R1/S1/Fib/Camarilla-H3/L3), Order-Flow 5 (Amihud/Kyle-λ/Roll-spread/Uptick-vol/VPOC-dist), Indicator Accel 5 (RSI/MACD-h/ADX/BB-pctB/Vol-z 5d 변화율), S/R 4 (52w-high/low/Round-num/Tests-21d)
- ✅ Information v2 — **19 features**: News v2 8 (velocity/spike-z/source-quality-sentiment/headline-body-divergence/M&A/legal/earnings/product topic counts), Insider v2 6 (cluster-buy/CEO-CFO co-buy/openmarket-ratio/cost-dist/exec-buy-weight/director-buy-weight), SEC-text stub 5 (LM-sent-10K/risk-factor-chg-%/Fog/going-concern/restatement; populated by edgar_10k_lm_sentiment.py)
- ✅ Cross-section v2 — **78 features**: per-date percentile rank for 36 high-signal features (PE/PS/PB/ROIC/Piotroski/momentum/composites), 16 interactions (Quality×Momentum / Value×Momentum / RSI×Vol / Vol×Sentiment / News×Insider 등), 26 lags (5d + 21d × 13 features)
- ✅ Alt-Data v2 — **29 features**: Short Interest 5, Options 6 (PC-vol/OI/IV-ATM/Skew/Term-slope/Unusual), Wiki 3, Google Trends 3, Reddit 3, Patents 3, 13F 2, GCAM 4 (fear/anger/econ-neg/polarity)

**Total feature 등록 수: 124 → 431** (+247%, +307 features)

### Concept registry 확장

- `data/fundamental/concepts.py`: 21 → 38 canonical concepts (EBITDA/DA/Interest/Tax/SGA/RnD/Inventory/Receivables/Payables/ShortDebt/PPE/RetainedEarn/Goodwill/Intang/Minority/Preferred/Buyback/StockIssued/ΔWC 추가). DART_MAP + SEC_MAP 양 마켓 모두 확장.
- `training/features.py::_CONCEPTS_NEEDED`: 17 → 39

### 데이터 수집 스크립트 (7개 신규)

- `scripts/sec_13f_ingest.py` — Top 15 institutional filers (Berkshire/Vanguard/BlackRock/JPM/StateStreet/Bridgewater/Renaissance/Citadel/TwoSigma/DESHaw/Point72/Tiger/Pershing/Lone Pine/Coatue) 13F-HR 인덱스. CUSIP→ticker 매핑은 Phase 2 deferred.
- `scripts/finra_short_ingest.py` — FINRA Reg SHO 일일 short volume (NYSE+Nasdaq) 90일 백필 가능. 무료 public file.
- `scripts/edgar_10k_lm_sentiment.py` — SEC 10-K 본문 → Loughran-McDonald 사전 (positive/negative/litigious/uncertainty 4 카테고리) + Fog index + going-concern count + restatement flag. 사전은 Notre Dame SRAF 무료 CSV.
- `scripts/yfinance_options_ingest.py` — yfinance option_chain → put/call vol·OI ratio, ATM IV, IV skew (10% OTM), term slope, unusual activity (vol > 3× OI).
- `scripts/gdelt_gcam_extract.py` — GDELT 2.1 GKG `gcam` 필드 정규식 파싱 → 11 sentiment dimensions (anger/fear/joy/sadness/econ-neg/econ-pos/pol-neg/pol-pos/polarity/amp1/amp2) + orgs/persons/themes counts. 신규 테이블 `gdelt_gcam`.
- `scripts/alt_data_ingest.py` — 4-in-1 dispatcher: Wikipedia REST API pageviews (무인증), Google Trends via pytrends, Reddit via PRAW (settings.reddit_client_id 필요), USPTO PatentsView API (무료).

### Smoke test 결과

- `build_feature_matrix(US, 180d, AAPL)` → rows=121, **cols=353** (cross-section/alt-data 일부 0/NaN; alt-data 테이블 아직 미존재라 정상). PostgreSQL InFailedSqlTransaction 회피 위해 `session.begin_nested()` savepoint로 alt-data/info-v2 쿼리 wrap.
- Float casting 추가 (PostgreSQL Decimal → float) — tech_v2 silent failure 해결.

### 다음 단계 (이번 wave 외)

- ⚪ 데이터 수집 실제 실행 (FINRA-short/Wiki/Trends/Options/GCAM/LM-sentiment)
- ⚪ 431 features 전체 활성 상태에서 LGBM/XGB/CatBoost 전 클러스터 재학습
- ⚪ 13F XML body 파싱 + CUSIP→ticker 매핑 (OpenFIGI 무료 tier)
- ⚪ Earnings call transcript sentiment (Seeking Alpha 스크래핑 또는 SEC 8-K Item 2.02 exhibit)
- ⚪ Currency Hedge alternative 11개 중 추후 구현 (options-based / forwards / regime-conditional)

---

## 2026-06-02 (화) 저녁 — Wave 3: 10y 백필 + 모델 zoo 확장

근거: `backend/scripts/{eda_data_explore,train_models_v3,finbert_news_score,earnings_call_sentiment,tune_top_clusters}.py`, `training/{models_v3,features_embeddings}.py`

### 10y 백필 완료

- ✅ US prices 2016-2024: 513/517 ticker, **1,118,558 rows** (10.4년)
- ✅ KR prices 2016-2024: 332/350 ticker, **615,831 rows** (10.4년)
- ✅ Cross-asset ETF 10년: 17 ETF, ~2,618 rows each
- ✅ Macro (VIX/SP500/KOSPI/DXY/US10Y) 10년: ~2,600 rows each
- ✅ FRED extra 10년: 25/28 시리즈
- 🔄 EDGAR US financials (진행 중, ETA 3h)
- 🔄 DART KR financials (진행 중, ETA 2.5h)

### Wave 3 모델 zoo (8/10 가용)

- ✅ `training/models_v3.py` 10 모델 통합 wrapper:
  - **8 가용**: StackingEnsemble, GaussianProcessReg, NBEATSModel (darts), TFTModel (pytorch-forecasting), PatchTSTModel, CausalForestModel (econml), BNNModel (MC Dropout), FinBERTScorer
  - **2 deferred**: ChronosForecaster, TimesFMForecaster (transformers version 충돌, 환경 분리 필요)
- ✅ 의존성 설치: darts, pytorch-forecasting, transformers, econml, PyWavelets

### Wave 3 추가 features

- ✅ `features_embeddings.py` 10 features: Wavelet 5-level db4 (6 energy ratios + HF ratio) + STL (trend/seasonal/resid strength). PCA/Autoencoder lazy.
- 전체 feature count: **441** (124 → 215 Wave 1 → 431 Wave 2 → 441 Wave 3)

### Wave 3 trainer + 추가 ingest

- ✅ `scripts/train_models_v3.py` — per-cluster GP/BNN/Stacking trainer with chrono 80/20 split, R²/Hit/IC metrics
- ✅ `scripts/finbert_news_score.py` — FinBERT 3-class 점수를 모든 news_articles에 부여 → `news_finbert_score` 테이블
- ✅ `scripts/earnings_call_sentiment.py` — SEC 8-K Item 2.02 exhibit 본문 → FinBERT + Loughran-McDonald 결합 → `earnings_call_sentiment` 테이블
- ✅ `scripts/tune_top_clusters.py` — top-10 cluster auto-discovery + Optuna 200-trial wrapper
- ✅ `scripts/eda_data_explore.py` — coverage / returns 분포 / cluster 분포 / 피처 non-null rate / survivorship bias / top 20 상관관계 통합 EDA

### 동시 진행 (백그라운드, 8개)

- Ablation A: US 1.5y `bp0b72rnr`
- Ablation B: US 5y `bdi18c45y`
- Ablation C: US 10y `b2cn5vog1`
- EDGAR US fund `bugh2ho9w`
- DART KR fund `bpzl6sdl6`
- EDA 통합 리포트 `brpv8yaij`
- HMM regime classifier (완료, 5 states × 880일)
- Cluster 재산정 (완료, KR 350 + US 517)

### 다음 단계

- ablation 완료 → best 학습 윈도우 결정
- v3 trainer 8 모델 × top 10 cluster 학습
- Optuna 200-trial top-10 cluster 재튜닝
- FinBERT 뉴스 전체 점수 + earnings call 점수 부여
- Ensemble 재최적화 (forward selection + stacking meta-learner)

---

## 2026-06-08~09 — FTI 3축 데이터 수집 전수 점검 + 갭 메우기

근거: `scripts/backfill_information_history.py`(신규), `scripts/remap_news_mentions.py`, `scripts/yfinance_options_ingest.py`, `scripts/dart_backfill_kr.py`, `scripts/edgar_backfill_us.py`, DB 직접 쿼리.

### 배경 — 세션 OOM 후 실제 DB 재점검

WORK_LOG가 06-02에서 멈춰 있어 실제 DB 상태와 괴리. 직접 쿼리로 전수 점검한 결과 **데이터는 문서보다 훨씬 앞서 있었음**: short_volume 17.9M, news_finbert 1.45M, gdelt_gcam 229k, wiki 548k, financial_facts 899k, daily_prices 2.06M, earnings_call_sentiment 11.3k 등 06-02 "다음 단계"가 대부분 적재 완료 상태.

### 핵심 갭: Information(I) 축 historical 부재

- F/T 모듈은 각 ~28만 module_scores 행(365일 백필)인데 **I 모듈은 라이브 345/238행뿐**.
- 원인: `score_market`은 `article_classifications`(LLM 분류) 의존 → 247,528 mention 기사 중 **428개만 분류됨**. LLM 분류 측정: **기사당 38.7초**(Ollama qwen2.5:14b) → 247k = ~110일, 완료 비현실.
- **해결: FinBERT 경로 신설.** 전 뉴스 1.45M에 이미 FinBERT 3-class 점수 존재. `backfill_information_history.py` 작성 — FinBERT label→direction, max(pos,neg)→impact, source_trust+time_decay 적용해 `score_information` 그대로 재사용. 중립 기사가 confidence 부풀리지 않도록 impact를 방향성 prob 기준으로 매핑.
- ✅ **결과: I 모듈 module_scores 345/238 → 38,431행** (KR 16,901 / US 21,530, 2025-11~2026-06 전 구간). 점수 분포 정상(-98.7~100, 73% 방향성 신호).

### Fundamental 갭 메우기

- KR 누락 24개 분석 → 2그룹. **우선주 7개**(corp_code=None): KRX 코드규칙으로 보통주 부모 매핑(005935→005930 등) 후 `financial_facts` 상속 INSERT 3,292행. **나머지 17개**: corp_code 있으나 DART finstate `rows=0`(SPAC/신규상장/외국주 950160 — DART 데이터 자체 없음, 하드리밋).
- US 누락 16개 = ETF 14 + **BRKB/BFB**. BRKB/BFB는 CIK 누락이 원인(SEC는 BRK-B 표기) → CIK 수동 보정(1067983/14693) + EDGAR 재무 3,468행.
- ✅ **F 커버리지: KR 326→333, US 501→503** (잔여는 전부 ETF/SPAC 구조적 한계).

### Alt-data

- ✅ Options: yfinance 현재 스냅샷 **513/517 US** 적재(forward 누적 개시). 과거 옵션체인은 무료 불가.
- ⛔ Google Trends(pytrends 429 차단), USPTO Patents(레거시 엔드포인트 폐기, API키 필요), Reddit(크리덴셜 미설정) — 무료 한계로 차단.
- ⏸️ 13F 기관보유: filing 메타 106건만, 보유내역 0. CUSIP→ticker는 OpenFIGI 무료 가능하나 XML파싱+매핑 신규코드 필요, feature 2개 저ROI → 보류(user 승인).

### remap 성능 버그 → 효율 재작성 (해결)

- 구 `remap_news_mentions.py`: **17.5시간+ 미완료**. 진단 — `_word_boundary`가 US 티커 517개를 기사마다 **새 정규식으로 re.search** → 517패턴 × 2필드 × 144만기사 ≈ **15억 회 정규식 재컴파일**이 18시간 정체의 원인(이름 substring 매칭은 빠름).
- ✅ `scripts/remap_news_mentions_fast.py` 신규: ① 517 티커를 **단일 사전컴파일 alternation 정규식**(필드당 517→1 finditer), ② **24코어 multiprocessing**, ③ 서버사이드 스트리밍 커서(읽기/쓰기 커넥션 분리, OOM 차단). 매칭 의미는 모듈 헬퍼(`_upsert`/`_contains_*`) 재사용 → 500건 정합성 검증 mismatch=0.
- ✅ **결과: 137초 완료**(18h+ → 2분 17초, 실측 ~500배). news_ticker_mentions **275,620 → 1,779,704**(원본이 심하게 under-map). US 종목 커버리지 **288 → 438**.
- ✅ Information 재실행: 37,848 → **66,908 ticker-days**(I 모듈 788종목, US 50,590행).

### 미해결

- ⚪ LLM 분류 247k: 스케줄러 시간당 점진 처리에 위임(강제 실행 비현실, 38.7초/기사).

### 다음 단계

- 442 feature로 OOM 없는 전 클러스터 재학습(이전 캐시 OOM 블로커 별건)
- (옵션) 구 `remap_news_mentions.py` → fast 버전으로 교체 또는 `ticker_mapper._word_boundary` 자체를 사전컴파일로 수정(스케줄러도 수혜)

---

## 2026-06-09~10 — FTI 피처엔지니어링 면밀검토 + 버그수정 + 매크로 심화 + 10년 F/T 백필

근거: `scripts/backfill_ft_history_parallel.py`(신규), `scripts/backfill_regime_history.py`(신규), `training/features.py`(load_macro_features/load_regime_features/compute_fundamental_features), `scripts/train_lgbm.py`(FEATURE_COLS_MACRO/REGIME).

### 면밀 검토 (샘플 매트릭스 실측 + 코드분석)

`build_feature_matrix` 375컬럼 실측 → 무신호(완전NULL+항상0): US 43 / KR 76. 4범주 분류:
- A 진짜버그, B 매크로 얕음, C 죽은 alt-data(외부차단), D KR 구조적 데이터부재.

### 10년 F/T 모듈점수 백필 (저장공간 무관 — module_scores는 수GB)

- ✅ `backfill_ft_history_parallel.py` 신규 — 날짜 병렬화(20워커), score_market은 종목별 lookback만 로드라 OOM-free(RAM 128GB 중 4GB 미만 사용). 1차 18.5분.
- ✅ **버그1: KR `daily_prices.as_of_ts`가 전부 미래스탬프**(2024-12~2026-06, 2018년 봉도 as_of=2026) → look-ahead 가드가 과거봉 가려 KR 기술백필 abstain → T KR 17개월에 멈춤. 정정(trade_date+16h UTC, 738,758행) 후 재백필.
- ✅ 결과: **T KR 113,858 → 710,202행(10년)**, T US 209,148(10년), F US 1,063,124, F KR 392,274. (KR F는 DART 재무깊이로 일부 abstain — 별도 KR재무 10년수집 과제)

### 버그수정

- ✅ **버그2: regime 피처 완전고장** — market_regime에 NEUTRAL 6행뿐(HMM 출력 미영속), 코드는 RISK_ON/OFF(대문자) 탐색 → 항상 0. `backfill_regime_history.py` 신규(HMM 5-state를 882일×2시장 영속화, 라벨분포 neutral39/risk_on·calm_bull각25/risk_off10/crisis1%) + `load_regime_features`를 소문자 5-state 매핑으로 수정 + one-hot 3개(calm_bull/neutral/crisis) 추가. 검증: regime_conf 100%, regime_risk_off 40%, neutral 60% nonzero.
- ✅ **버그3: pb(주가순자산) 양시장 dead** — `SHARES_OUTSTANDING` 개념이 EDGAR/DART에서 미추출. NET_INCOME/EPS로 shares 유도하도록 `compute_fundamental_features` 수정 → pb 100% nonzero(AAPL P/B≈59.6 정상).

### 매크로 심화 (범주B — raw 57시리즈 중 13개만 쓰던 것 확장)

- ✅ `load_macro_features`에 **18 신규피처**: real_yield_10y(+chg), breakeven_10y(+chg), hy_credit_spread(+5d/21d chg), copper_63d_ret, wti_21d_ret, natgas_21d_ret, yield_curve_5_30, yield_curvature(2·10Y−5Y−30Y), usdkrw/usdjpy_21d_chg, vix_pctile_252d, vol_risk_premium(VIX−realized), funding_stress. **전부 100% non-null 검증.** (US2Y/3M은 2024+만 있어 2-10커브 대신 5/10/30 사용)
- ✅ `ALL_FEATURE_COLS` **442 → 462** (+macro 17 +regime 3, 1중복제거).

### 검증된 사항

- ✅ cross-section(percentile/interaction/lag)은 `train_lgbm.py:353` + `cache_feature_matrix_v4.py:125` 둘 다 `apply_cross_section_features` 호출 → 학습경로 정상편입(검토 의심 해소).
- ✅ FinBERT/info_v2 뉴스피처 살아있음(finbert_* 64-65% nonzero) — v1 info_feat(news_sentiment/impact/pos/neg)만 LLM의존으로 dead, FinBERT가 대체커버.

### 남은 구조적 결손 — 후속 처리 결과 (2026-06-10 추가)

처리 후 재분류. **"구조부재"로 적었던 것 중 KR insider는 실은 배선버그였고 수정함.**

- ✅ **KR insider 배선 수정** — `_load_insider_panel`이 KR은 무조건 빈 패널 반환하던 버그. `insider_transactions`(DART 임원·주요주주 소유보고 11,802행/327종목 = Form-4 등가물, 이미 적재돼 있었음)를 `_load_insider_panel_kr`로 연결. 검증: insider_net_value_30d **89% nonzero**(삼성전자 등), ~7 KR insider 피처 부활. 커밋 84c8d15.
- 🟡 **현금흐름 — 대부분 이미 존재**(오판 정정): CFO US 30k/KR 3.8k, CAPEX US 23k, CFI 양시장 모두 적재돼 있음. fcf_yield 등 작동 중. 실제 dead는 fcf_3y_cagr/fcf_total_debt/owner_earnings(3 US 파생 edge, 3y이력/공식 의존) + **KR capex-flow**(DART가 현금흐름표 PPE취득 미수집, raw엔 PPE 잔액만) → minor, 재수집 저ROI.
- ⛔ **죽은 alt-data 확정 차단**: trends(Google 429), reddit(크리덴셜 없음), patents(PatentsView 엔드포인트 폐기·API키 필요), 13F(OpenFIGI+XML 신규코드 저ROI). 빈 테이블 0-fill, LightGBM이 상수 무시하므로 무해.
- ⛔ **KR 공매도 차단 확정**: pykrx가 이제 KRX 로그인(KRX_ID/PW) 요구 → 크리덴셜 없어 실패. KRX 별도 인증 필요.
- ⚪ **KR 진짜 구조부재**: EDGAR 8K·10K·SEC텍스트, GDELT/GCAM(영어) — KR 무료등가물 없음. (insider는 위에서 해결됨)

### 다음 단계

- (저장소 확보 후) 10년 뉴스 → I축 10년 + regime 입력(US2Y) 10년 백필 → regime/yield_curve_2_10도 10년 확장
- 462 feature로 전 클러스터 재학습

---

## 2026-06-10 — 목적지향 데이터 검증 (자동매매 결정 부합성)

"컬럼이 채워졌나"를 넘어 "이 데이터로 학습한 모델이 목표가·매수/매도·사이즈를 뽑을 수 있나" 관점 검증.

- ✅ **타깃 정합**: `ret_fwd_21d = close.pct_change(21).shift(-21)`(21일 선행수익률) + `rank_fwd_21d`. 올바름.
- ✅ **Look-ahead 누수 없음**: `financial_facts.as_of_ts`는 실제 공시일(EDGAR `filed`/DART `rcept_dt`), `_latest_concept_as_of`가 `as_of_ts<=as_of` 필터. 시점별 재무가시 US 2018년 462/503·2026년 503/503으로 정상. (AAPL 단일종목 보고 "장님"이라 한 1차 추정은 오판, 집계로 반증)
- ✅ **결정 사슬 실재**: F/T/I 합성score → ±25 임계 → BUY/SELL → 사이즈(기본 5%, regime 스케일, 최대 20% 캡). `decision/types.py`.
- 📊 **실제 모델 IC**(구버전 1년 데이터): KR IC **0.049**/적중 60.6%, US IC 0.018/적중 52.0%(rank target US 0.076). **과적합 큼**(train R² 0.47 vs test 0.03).
- 🔴 **KR 재무 깊이 부족**: 2016~존재하나 2016-2018 희소(연~1000행 vs 2019+ 10k+) → KR F축 과거 빈약.

**함의(trading)**: IC 0.02~0.05면 단일종목 점추정 오차 큼 → 목표가는 "정밀 숫자"가 아니라 "범위+확률"로. 횡단면 rank가 robust(특히 KR). 사이즈는 소액·분산. 과적합 해소(피처선택) 우선.

**후속 수행(2026-06-10~)**: ① KR DART 10년 깊이 보강 ② 신규피처 캐시 재빌드 ③ 10년+신규피처 재학습 IC 재측정(구버전 대비) ④ 비교실험: 전피처 vs 피처선택, 점추정 vs 분포예측/triple-barrier.

---

## 2026-06-10~11 — 재학습 + 모델링 A/B 실험 (목표가·매수매도 직결)

검증에서 나온 가설들을 직접 학습·비교해 데이터로 결정. 신규 스크립트: `feature_selection_ab.py`, `distribution_ab.py`.

### 실험1 — 신규피처 효과 (동일 1년, 구버전 vs 신규 ALL_FEATURE_COLS 462)
- US ret_fwd_21d IC **0.018 → 0.045**(2.5배), rank 0.076→0.080. KR rank 0.026→**0.065**.
- 단 KR ret_fwd_21d는 0.049→**−0.044**(반전) = 원수익률 과적합.
- **결정: 신규피처(regime/매크로/insider/pb)가 모델 개선 입증. 주 타깃 = rank_fwd_21d.**

### 실험2 — 피처선택 (KR, 동일 데이터·CV; `feature_selection_ab.py`)
| 구성 | ret IC | rank IC |
|---|---|---|
| 전 462피처 | −0.001 | 0.062 |
| **top50(중요도)** | −0.032 | **0.087** |
| top50+강정규화 | +0.002 | 0.060 |
- **결정: rank 타깃 + top~50 피처가 최선(IC 0.062→0.087, +40%).** 과적합 실증 — 피처 "선택"이 핵심 레버(정규화만으론 부족). KR 원수익률은 어떤 구성이든 예측불가(IC~0) → 폐기.

### 실험3 — 분포예측 (KR, 시간순 holdout; `distribution_ab.py`)
- LightGBM quantile q10/q50/q90. q50 방향 IC 0.28*(단일 최근분할, 낙관적; CV 0.087이 정직), 적중 62%.
- **구간 coverage 0.64**(목표 0.80) → 구간 과신, **conformal 보정 필요**.
- 목표가 밴드 ±19%/21일. decile long−short +20.8%*(낙관적).
- **결정: 목표가는 "정밀 숫자" 아닌 "q50 ±밴드"로 산출 가능. 단 밴드 보정 선결.**

### KR DART 깊이 보강
- `dart_backfill_kr --years 10 --annual-only` → +43,544행(2019: 5855→8336, 2020: 10671→13476 등 densify). KR 재무 102,209행.

### 종합 권고 (자동매매 아키텍처)
1. **타깃 = rank_fwd_21d**(횡단면), 원수익률 점추정 지양.
2. **피처 = 중요도 top~50** (462 전부 X, 과적합).
3. **목표가 = quantile 밴드**(q50±[q10,q90]) + conformal 보정.
4. **사이즈 = rank 신뢰도 × 분산 × regime 스케일**(decision layer 기존 로직 활용), 저IC라 소액·다종목.
5. 다음: conformal 캘리브레이션, top-K 피처 재학습 전 클러스터, US 동일 A/B 확인.

---

## 2026-06-11~13 — Conformal 보정 + 프로덕션 번들 + US 확인

### 피처선택 A/B — US 확인 (KR과 동일 결론)
| 구성 | US ret IC | US rank IC |
|---|---|---|
| 전 462 | 0.046 | 0.094 |
| top50 | 0.107 | 0.112 |
| top50+reg | **0.113** | **0.118** |
- **top50이 양 시장 모두 압승** (US 둘 다 ~2배). 과적합 가설 두 시장 견고 입증. US는 원수익률도 예측가능(KR과 달리).

### Conformal 보정 (`conformal_ab.py`) — 목표가 밴드 정직화
- CQR(Romano 2019): 캘리브레이션셋 잔차로 밴드 확장. KR coverage **0.61→0.84**(목표 0.80 달성). 밴드 q50±30%(KR)·±19%(US 보정 후 ±tighter).

### 프로덕션 번들 (`train_production.py`) — 검증된 설정 배포
joblib 번들 = rank모델(top50) + quantile(q10/q50/q90 목표가) + conformal Q + walk-forward 메트릭. 최종모델은 전체데이터 학습.
| 시장 | rank IC (walk-forward) | band coverage | conformal Q | top피처 성격 |
|---|---|---|---|---|
| **US** | **+0.277** (std0.06, 100%양수) | 0.805 | +0.039(타이트) | 펀더멘털(bv/eps_cagr/quick_ratio) |
| **KR** | **+0.207** (std0.06, 100%양수) | 0.843 | +0.127(넓음) | 기술·유동성(STL/amihud/vol) |
- **핵심**: walk-forward(expanding, 라이브와 동일) rank IC가 양 시장 6구간 전부 양수, 평균 0.21~0.28 → 견고한 횡단면 엣지. (단일분할 IC는 −0.02~+0.28로 노이즈 → walk-forward가 정직값.)
- 시장별 신호구조 상이: US=펀더멘털, KR=기술/유동성.
- ⚠️ 데이터가 ~1년(2025-06~2026-06)이라 단일 regime 내 검증. 10년 데이터 확보 시 regime 교차검증 필요.

### 자동매매 결정 산출 (번들로 가능)
- 종목별 **rank** → 횡단면 선택(top decile long, bottom 회피/short)
- **목표가** = last×(1+q50), **신뢰밴드** = last×(1+[q10−Q, q90+Q]) (보정됨)
- **사이즈** = rank 신뢰도 × 분산 × regime 스케일(decision layer)

### 추론 배선 완료 (`decision/production_inference.py`)
- `ProductionRecommender`: 번들 로드 → 종목별 rank_score/rank_pct + pred_ret(q50) + **목표가**=last×(1+q50) + **보정밴드**=last×(1+[q10−Q, q90+Q]) + **action** + **size_fraction**(base×rank신뢰도×regime, 캡).
- **신호 일관성 수정**: action은 rank모델(상대)+return모델(절대 q50 부호)이 **합의**할 때만 BUY/SELL(둘이 충돌 가능 — bottom-rank인데 q50 양수 케이스 제거). KR 공매도 제약상 BUY는 q50>0 필수.
- 스모크: US TOP BUY=LITE/INTC/LRCX/AMAT(반도체), 목표가+밴드 산출. KR TOP BUY=322000/041960 등. SELL은 전부 음수수익+bottom-rank로 일관.

### 글로벌 vs per-cluster 결정
- **글로벌 유지** — ~1년 데이터를 클러스터로 쪼개면 과적합 위험(글로벌 top50이 walk-forward IC US+0.28/KR+0.21로 견고). per-cluster는 10년 데이터 확보 후 refinement.

### 다음
- decision/runner를 production 번들 추론과 통합(현 composite F/T/I와 병행/대체 결정)
- 모델레지스트리 통합 + 페이퍼 시뮬로 실측 검증
- (저장소) 10년 데이터로 regime 교차 walk-forward

---

## 2026-06-13 — 무미래정보 백테스트 + 결정 시스템 통합

### 자본 백테스트 (`backtest_capital.py`, look-ahead 차단: R−21 임베고, 리밸런스마다 재학습)
2개월 단일창 + 전기간(9개월, 18 리밸런스, step 10td) walk-forward. long-only 상위decile, 비용 US 0.1%/KR 0.3%/리밸.

| | US ($1,000) | KR (100만원) |
|---|---|---|
| 종료(전략) | $1,550 (+55.0%) | 2,242,103원 (+124.2%) |
| 종료(벤치 동일가중) | $1,084 (+8.4%) | 1,954,663원 (+95.5%) |
| **알파(초과)** | **+46.5%p** | **+28.7%p** |
| MDD | −6.1% | −7.0% |
| 벤치 상회 | 89%(16/18) | 72%(13/18) |
| 하락구간 | 전략+0.4% vs 벤치−1.7% | 전략−0.8% vs 벤치−1.9% |

- **핵심 정직**: 이 9개월은 강세장(특히 KR 벤치 +95%) → 총수익 대부분은 **베타**. 모델 실력 = **알파(+28~47%p)** + 낙폭 낮음 + 상승·하락 모두 알파 + 일관성(우연 아님). **long-only라 약세장에선 덜 잃을 뿐 손실 가능**. 단일 1년(단일 regime) 표본.

### 결정 시스템 통합 (`scripts/run_ml_recommendations.py`)
- `ProductionRecommender` 추천을 `decision_audit`에 기록(시스템 감사/UI에 흐름): US 44 BUY/18 SELL, KR 34 BUY/4 SELL. inputs_snapshot에 target_price·band·rank·size 저장.
- additive/안전. **풀 페이퍼실행(gates→risk→broker) 배선은 다음 단계** — 라이브 피처빌드 + 인터페이스 정합 필요.

### 다음
- 풀 페이퍼실행 배선(gates/risk/paper-broker) + 스케줄러 잡 등록
- 약세장 대응(현금화/숏/regime 게이팅) — long-only 한계 보완
- (저장소) 10년·다(多)regime 데이터로 백테스트 재검증

---

## 2026-06-13 후반 — 풀 페이퍼실행 배선 + regime 게이팅

### 풀 페이퍼실행 (`decision/ml_runner.py`, `scripts/run_ml_decisions_job.py`)
- `run_ml_decisions`: ProductionRecommender 추천 → OrderIntent → **RiskEngine.check → PaperBroker.execute → 실제 체결 → decision_audit**. 기존 인프라(`core.risk`, `brokers.paper`, `_default_risk_state`) 재사용. 전용 계좌 `ml-kr`/`ml-us`.
- 사이징: 총자산(현금+포지션) 기준 균등 1/n_long, 가용현금 한도. (1차 버그: `get_account().equity`가 현금만 반환 → 총자산으로 수정.)
- 스모크: US/KR 각 19개 BUY 실제 체결, 전액 정확 배분(US 현금$50+포지션$950=$1000). risk_engine 통과, 포지션·audit 영속.
- **스케줄러 등록**: `runtime/scheduler.py`에 `_job_ml_decisions_daily` + JobSpec `ml_decisions.daily`(22:45 UTC, 총 22잡). 캐시(`_fs_ab_{m}_365.parquet`)+번들 없으면 skip(안전).

### Regime 게이팅 (`backtest_capital.py --regime-gate`, ml_runner `regime_gate`)
risk_off/crisis → 현금화. 전기간 백테스트 비교:
| | 무게이팅 | 게이팅 |
|---|---|---|
| US | +55.0% | +27.0% (−28%p, **손해**) |
| KR | +124.2% | +140.6% (+16%p, **이득**) |
- **시장·기간 의존적**(정직): KR은 risk_off가 실제 하락과 맞아 이득, US는 짧은 눌림목→반등이라 손해. MDD는 양쪽 불변(−6~7%). **약세장 데이터 없어 진짜 가치 검증 불가** → 튜닝 옵션으로 유지(기본 ON).

### 다음
- 라이브 피처 리프레시 잡(현재 ML잡은 수동빌드 캐시 의존) → 일일 자동빌드
- per-market 게이팅 튜닝 + 약세장 포함 데이터로 재검증
- 페이퍼 3개월 실적 누적 후 live 단계 검토

---

## 2026-06-14~15 — 🔴 약세장 검증: 모델 엣지가 regime에 robust하지 않음 (중대)

10년 가격/재무 데이터로 2022 약세장(US −18.8%/KR −19.4%, 동일가중)을 무미래정보 walk-forward 검증. top50이 가격/재무 기반(뉴스/regime 거의 미포함)이라 과거 적용 가능. `backtest_capital.py --build-start/--build-end --long-short --vix-gate`.

### 결과 — 2021-2022 (강세장 백테스트와 정반대)
| | US long-only | US 롱숏 | US VIX방어 | KR long-only | KR 롱숏 | KR VIX방어 |
|---|---|---|---|---|---|---|
| 수익 | −11.0% | **−19.7%** | −11.5% | −26.5% | **−24.2%** | −9.8% |
| 알파 | −12.2%p | −21.0%p | −12.8%p | −18.5%p | −16.2%p | −1.8%p |
| MDD | −22% | — | −20% | −34% | −31% | **−19%** |

(참고 강세장 2025-26: 알파 US +46.5%p / KR +28.7%p)

### 결론 (정직, 중대)
- **모델 엣지가 regime에 robust하지 않음.** 강세장(모멘텀 우호) +두자릿수 알파 ↔ 약세장 −두자릿수 알파(언더퍼폼·손실).
- **롱숏도 실패**(US는 long-only보다 더 손실) → 2022 모멘텀 급락장에서 **rank가 역예측**(고른 롱이 숏보다 더 하락). 즉 **하락장에서 +수익 불가**(market-neutral조차).
- **2025-26 알파는 단일 regime(모멘텀 강세) 과적합**이었을 가능성이 큼. 그 기간 내 walk-forward가 전부 양수였던 건 "한 regime 안"이라 fragility를 못 드러냄.
- 유일하게 유효한 건 **VIX 현금방어**(KR MDD −34→−19%, 벤치 근접) — 알파가 아니라 손실회피.
- **→ 현 모델은 실거래 부적합.** 강세장 백테스트만 믿었으면 2022형 장세에서 크게 잃었을 것. **약세장 검증이 이를 사전 차단.**

### 과적합/robustness 대응 (다음 필수)
1. **다(多)regime 학습** — 2016-2026 전체(2018/2020/2022 약세장 포함)로 학습. 가격/재무 top피처는 10년 가용 → 빌드 가능. (이게 핵심 fix)
2. **regime-conditional 모델**(Wave4 regime×feature) — regime별 분리 학습/게이팅.
3. **모멘텀 과의존 완화** — mean-reversion/quality/defensive 피처 비중↑.
4. **VIX/regime 노출 게이팅**을 리스크 오버레이로 상시 적용.
5. **기대치 현실화** — 무료데이터 주식 엣지는 작고 regime 가변. "+46%p"는 신기루.

---

## 2026-06-15 — 다regime 검증 (2018-2024): 자멸은 해결, 알파는 ~0

병렬 빌드(`build_period_parallel.py`, 전코어)로 2018-2024 매트릭스(2018조정·2020COVID·2022약세·2023회복 = 다regime, US 756k/KR 423k행) 구축 후 무미래정보 walk-forward(step 21, 69/68 리밸런스) + VIX국면별 알파 분해.

| | 전략 | 벤치 | 알파 | MDD | VIX저/중/고 알파 |
|---|---|---|---|---|---|
| US | +113.0% | +107.7% | **+5.4%p/6년** | −40% | −0.69/+0.41/+1.02%p |
| KR | +114.3% | +117.5% | **−3.1%p/6년** | −38% | +0.10/−0.56/+0.29%p |

### 결론 (정직, 확정)
1. **다regime 학습 = robustness fix.** 단일regime 모델은 2022서 −18.5%p 자멸 → 다regime는 벤치 추종(자멸 없음). 약세장을 학습에 포함하면 무너지지 않음.
2. **그러나 정직한 cross-regime 알파는 ~0(KR)~0.9%/년(US).** 두 자릿수 아님. **2025-26의 +29/+46%p는 regime(모멘텀강세) 행운 확정.**
3. **MDD ~40%** — VIX/regime 리스크 오버레이 필수(생존성).
4. US는 **고VIX(스트레스)에서 알파 +1.0%p**(calm −0.7%p) — 모델이 위기장에서 역설적 유용(소표본).

### 두 자릿수 목표로 가는 진짜 경로 (데이터 양 아닌 신호 질)
- **"더 많은 가격/재무 데이터 학습"은 자멸만 막을 뿐 알파 생성 못 함** — 가격 피처만으론 cross-regime 엣지 한계.
- 진짜 알파 후보: ① **정보/뉴스 축**(3-모듈 본래 취지, 가격에 없는 정보 — 10년 뉴스 확보 시 차별화) ② **regime-conditional 모델**(국면별 모멘텀↔mean-reversion 전환) ③ **정교한 라벨**(triple-barrier/vol-adjusted) ④ VIX 노출 게이팅 상시 적용(생존).
- 현 시스템은 이제 **정직**함(과적합 자멸 없음). 목표 달성은 신호 품질 개선의 문제.

---

## 2026-06-15~16 — 알파 개선 캠페인: 시장중립 라벨 = 검증된 첫 승자

`alpha_lab.py` 신규(2018-2024 다regime 캐시 재사용 A/B 하니스, 다중시드 mean±std). 레버: 라벨/regime-conditional/피처선택/포트폴리오/게이팅.

### 🔴 규율이 2개 artifact를 잡음
1. **'1회 피처선택' 속도최적화 = test-기간 선택편향 artifact.** US baseline 알파를 +22%/년(+126%p)으로 부풀림. **재선택(리밸런스마다)으로는 ~0**, backtest_capital과 일치. 첫 실험의 "regime-conditional +8%/년"은 전부 이 artifact 위 가짜.
2. **random_state 미고정 → 시드노이즈 ±~9%/년.** baseline이 시드만 바꿔 +3.95↔−4.96%/년. 단일 백테스트 무의미 → 다중시드 필수.

### ✅ 다중시드(3seed, step42)로 검증된 진짜 승자: 시장중립 잔차 라벨
타깃을 `rank_fwd_21d` → **`mn_fwd_21d = ret_fwd_21d − 횡단면평균`**(베타 제거, 순수 알파):
| | baseline_rs | **mn_rs** |
|---|---|---|
| KR | −9.8 ± 1.3%/년 (항상 음수) | **+9.5 ± 5.5%/년** (항상 양수, 비중첩) |
| US | +3.4 ± 3.7%/년 | **+10.0 ± 1.8%/년** (저분산) |
- **양 시장 ~+10%/년 알파, 전시드 양수, 2018-2024 다regime(약세장 포함).** 노이즈 아닌 진짜.
- **regime-conditional은 승자 아님**(다중시드서 mn 단독과 동일). 첫 실험 결과는 artifact였음.
- 경제적 타당: 시장중립 잔차 예측 = 베타 제거 후 순수 알파 학습(퀀트 표준).

### topk 스윕 — KR +39% 스파이크는 일반화 실패(또 artifact)
mn 위에 피처수 스윕(다중시드 step42): KR mn_top30 **+39.3±3.5%/년**(전시드 +35~43)로 폭등했으나 **US mn_top30은 +11.3%**(평범) → KR top30은 **기간 과적합/다중검정 artifact**. US는 top100(+17.1%)이 최고지만 이 역시 topk-튜닝 과적합 위험.
- **안전한 robust 주장 = "mn 라벨 ~+10%/년"**(양 시장, 전 시드 양수, 보수적). 고수치(KR+39/US+17)는 신뢰 안 함.
- mn_ls(롱숏): −15%/년 — 숏 leg 역예측, market-neutral 포트는 실패(롱only 유지).

### 의미 / 다음
- 정직한 cross-regime 알파 **~0(rank) → ~+10%/년(mn)**. 신뢰가능한 두 자릿수 근접 — 캠페인이 검증된 레버 1개 확보.
- 방법론 교훈(재확인): **엄밀 평가(재선택+다중시드+cross-market) 필수**. 좋아 보이는 단일/단일시장 스파이크는 대부분 artifact(once-select +22, regime +8, KR-top30 +39 전부 가짜로 판명).
- 다음: **mn 라벨(top50) 채택** → 프로덕션 타깃 교체. 그 위에서 레버 계속: 섹터/베타 중립화, 앙상블(XGB/Cat), Wave4 상호작용 피처, Optuna OOF튜닝.

---

## 2026-06-16 후반 — mn 라벨 프로덕션 반영 (캠페인 [2→1])

### [2] 모델링 레버 마무리 — 앙상블·섹터중립 모두 비robust
- **섹터중립 라벨(sn)**: KR +0.65±6.0%/년 (mn +9.5의 1/15) → 폐기. mn 알파의 상당부분이 "섹터 선택" 신호.
- **앙상블(mn_ens, LGBM+HGB rank-avg)**: KR +16.1±**15.2**%/년(−5.1~+29.3) → 분산 3배, 음수 가능 → 비robust 폐기.
- **결론: 엄밀검증 통과 = mn 라벨 단 하나.** regime-cond/topk30/sn/ls/gate/ensemble 전부 artifact.

### [1] mn 라벨 프로덕션 반영
- `train_production.py`: 타깃 `rank_fwd_21d` → **`mn_fwd_21d`(시장중립 잔차)**, `--cache`로 **다regime 2018-2024** 학습.
- 번들 재빌드: KR WF rank IC **+0.040**(83% 양수, 416k행), US **+0.023**(83% 양수, 746k행). 밴드 0.79/0.82 보정.
- `production_inference.py`: action을 **rank기반 상위/하위 decile**로(이전 q50>0 합의필터는 mn=상대신호와 불일치 → KR BUY=0 유발). 스모크: US BUY52/KR BUY35 정상.
- ※ 모델은 2018-2024 학습, 추론은 2026 최신피처(2024-26 갭) — 추후 2018-2026 통합 캐시로 재학습이 refinement.

### 캠페인 총평
정직한 cross-regime 알파를 **~0 → ~+10%/년(mn)**으로. 수많은 그럴듯한 숫자(+46/+22/+39/+16%) 중 **다중시드·cross-market·다regime 검증을 통과한 건 mn 하나**. 다음 도약은 모델링 아닌 **신호(정보/뉴스축)**.

---

## 2026-06-16 — regime-adaptive 4종 전수 테스트: 전부 mn_long에 패배

user 아이디어("국면 판별→best 레버 라우팅") + 제안한 안전판 3종을 최초원칙대로 전수 구현·검증(`alpha_lab` run_regime_suite/mn_regfeat, KR 3시드 step42).

| 접근 | 알파/년 mean±std | vs base |
|---|---|---|
| **base = mn_long 단독** | **+9.5 ± 5.5%** | 기준 |
| 하드 라우터(best-past-in-regime) | −8.2 ± 8.0% | ✗ |
| 노출 오버레이(dn→0.5배) | +5.9 ± 6.2% | ✗ |
| 소프트 블렌딩(추세로 mn_long↔ls) | −2.7 ± 6.7% | ✗ |
| regime을 피처로(mkt_trail/vol 주입) | +6.1 ± 4.5% | ✗ |

### 결론 (정직, 데이터로 기각 — 미리 판단 아님)
- **regime-conditioning은 어떤 형태(스위치/오버레이/블렌딩/피처)로도 robust mn 신호를 못 이김** — 전부 희석.
- 라우터 `chosen` 분석: 상승장에 cash, 횡보장에 ls를 고름 = **"국면별 best"라는 선택 자체가 적은 regime샘플의 노이즈에 과적합**.
- 근본 이유: mn 모델은 이미 다regime 학습 + 시장중립 타깃이라 cross-regime robust. regime 특화는 적은 샘플로 과적합만 추가.
- (앞선 regime-conditional 모델 artifact와 일관) → **regime은 알파 신호로 쓰지 말 것.** mn 단독 유지가 최선.
- 남은 regime 용도는 리스크 한도/포지션캡 정도(알파 아닌 거버넌스), 그것도 신호품질 전제.

### 캠페인 총괄 (모델링 레버)
**검증 통과 = mn 라벨 단 하나(~+10%/년).** 기각: regime-conditional, topk30, 섹터중립, 롱숏, VIX게이팅, 앙상블, regime-라우터/오버레이/블렌딩/피처. → **모델링 레버 수렴. 다음 도약은 신호(정보/뉴스축).**

---

## 2026-06-16 — 알파 기법 battery 전수 테스트 착수 (열린 루프)

user 지적: "수렴" 성급했다 — 라벨/전처리/모델클래스/신규피처 미탐색. 전 기법 battery를 **GAPS.md §X에 영구 enumeration**(tested/untested/기각). 평가바: 2018-2024 다regime walk-forward 다중시드로 mn_long(+9.5%/년) 넘는가, cross-market 확인.

- **Wave1**(진행): 횡단면 z-score 정규화 / Ridge 선형 / vadj(vol조정) 라벨.
- **Wave2**(코드 ready): rank-transform·winsorize 전처리, ElasticNet/Lasso/XGBoost/CatBoost/ExtraTrees 모델, conviction 가중. (xgboost/catboost 설치 확인)
- **Wave3**: Wave-4 상호작용피처(미구현), residual momentum, idio-vol, 샘플 uniqueness 가중.
- **Wave4**: meta-labeling, triple-barrier, MLP/LSTM/TFT, Optuna OOF-IC.
- `alpha_lab.py`에 전 레버 옵션화(label/normalize/model/portfolio/regfeat). 각 Wave 승자만 cross-market 확인 후 채택.

**원칙(영구 기록 [[feedback-no-complacency]])**: 미테스트 0이 돼도 새 기법·아이디어 떠오르면 즉시 복귀. "끝" 선언 금지.

## 2026-06-17 — Wave1/4 결과 + 가짜 승리 적발(ridge) + 하니스 IC 업그레이드

**Wave1(CPU)**: mn_norm(8.79±7.84)/mn_ridge(5.62)/vadj(4.97±9.91) 전부 mn_rs(9.48) 미달 기각.
**Wave4 MLP(GPU)**: mn_mlp(0.98)/wide(8.37)/ens(0.56) 전부 GBDT 미달 — 횡단면 tabular는 GBDT 우위. RAM 128GB/77GB여유, GPU 28%로 Wave1 CPU와 무충돌 병행 확인.

**가짜 승리 적발 — 규율이 막음**:
- `mn_ridge_raw` KR+21/US+27%/yr → `rcond≈1e-36` ill-conditioned 스케일 artifact(정규화하면 붕괴). 기각.
- `mn_ridge`(정규화) US+20.9±0.74 (GBDT 2배!) → `ridge_diag.py` 진단: **OOS IC 음수(US−0.017/KR+0.015), top5 fold 84~160% 집중**. 정체 = 저변동성/저베타 팩터 틸트(지배피처 tracking_err/vol/beta/corr_spy)가 메가캡 지배기에 등가중 벤치를 이긴 것. **종목선택 알파 아님**. 기각.

**방법론 업그레이드(열린루프 복귀)**: alpha_lab가 등가중-벤치 대비 수익으로만 평가 → 팩터틸트=알파 혼동했음. `run_experiment`에 **OOS rank-IC + 양수fold% 컬럼 추가**. 이제 모든 config는 IC(선택능력)로도 판정. **mn_rs는 IC 양수로 진짜 알파 재확인**. (`ridge_diag.py` 신규, alpha_lab IC 컬럼)

> 교훈 [[feedback-no-complacency]]: *벤치-상대 수익 ≠ 알파*. IC≈0인데 수익 큰 건 팩터 베타. 화려한 숫자일수록 IC·집중도·메커니즘 먼저 본다.

### mn_norm(per-date 정규화) — 캠페인 첫 검증된 개선, 채택

ridge와 달리 mn_norm은 성급 기각 안 하고 4컷×bear까지 검증 → **진짜 안정화 확인, 프로덕션 채택**:
- 4컷(KR/US × step21/42): mn_rs 대비 3/4 우세. mn_rs는 step취약(KR 9.48→1.11), mn_norm은 8~15 안정.
- **bear(hi-vix)**: US mn_norm +0.80 vs mn_rs −0.94 / KR +0.61 vs +0.04. 3 regime 전부 양수(US).
- MDD −22~27 vs mn_rs −34~42%. conc5 94%(최저).
- **IC 유지(0.025 vs 0.030)** → 저변동 틸트면 IC 떨어졌을 것. 같은 선택능력 = 같은 신호를 더 안정 추출.
- 배선: `train_production.py`(per-date z-score in-place, `--no-normalize`로 끌 수 있음, bundle에 `normalize` 플래그) + `production_inference.py`(당일 횡단면 z-score 재현, PIT 안전). KR/US 번들 재학습.

> ridge vs mn_norm 대조가 규율의 핵심: 둘 다 정규화인데 ridge(선형)는 IC붕괴+step취약+84%집중=가짜, mn_norm(트리)은 IC유지+bear양수+step강건=진짜. **숫자 크기 아니라 IC·집중도·regime·step강건성으로 판별.**

### Wave2(정규화 위 모델/전처리/포트) — mn_norm 넘는 것 없음, 전부 기각

정규화 baseline 위에서 7종 테스트, IC+conc5+bear로 판정:
- **mn_et**(ExtraTrees) US수익 21.7로 최고였으나 **IC 0.0077/fold 46%=선택능력 0** → 함정 기각(ridge와 동일 패턴).
- **mn_rank** US 20.5이나 IC<mn_norm+고분산 → 틸트 기각.
- **mn_cat**(CatBoost) US IC 0.0297 최고지만 **KR 분산 ±14.04**(앙상블 기각기준 동일) 기각.
- **mn_xgb** mn_norm 미달. **mn_conv** KR conc5 1136% 극단집중 기각.
- **mn_winsor**(근소 아쉬움): IC 양시장↑(US 0.0305/KR 0.0168) + US 전면우세(bear 1.21). 하지만 **KR top-decile 수익↓(음수)**. 교훈: **rank-IC(전체횡단면) ≠ top-decile 수익(실거래분)** — winsor가 KR 전체순위는 개선해도 우리가 롱하는 상위10%엔 무용. 시장대칭 파이프라인엔 미채택.

**결론: GBDT+per-date정규화(mn_norm)가 모델/전처리 최적 확정.** Wave2는 신규 채택 0 — 깨끗한 승리만 채택하는 규율의 정상 작동. (mn_conv Decimal 버그 수정)

> 전 과정 시간순 랭킹은 [ALPHA_CAMPAIGN.md](ALPHA_CAMPAIGN.md), 미테스트 battery는 [GAPS.md](GAPS.md) §X.

## 2026-06-18 — Wave3 기각 / Wave4(LSTM 기각·Blitz 채택·활성화)

- **Wave3 잔차신호(idio-vol+resid-mom)**: 단일 config은 양시장 IC↑로 보였으나 **3-seed ablation이 시장모순 폭로**(KR은 resid_mom 단독 최고/idio_vol 해로움, US는 resid_mom 단독 IC↓). 미채택.
- **Wave4 GPU 시퀀스(LSTM, seq_lab.py)**: US +18%/KR ±11%의 화려/불안정 수익이 **전부 IC≈0**(US 0.0003) regime 틸트 → 기각. **딥러닝(MLP+LSTM) 트랙 소진** — 신경망은 횡단면 알파서 GBDT 불가.
- **✅ Blitz residual momentum 채택·활성화**: 정식 구성(일별 잔차 12-1m/잔차vol 표준화). 양시장 IC↑(KR 0.0060/US 0.0286)+집중↓+MDD↓+US 전regime↑. 배선: `features_advanced.compute_stat_features`+ALL_FEATURE_COLS, `augment_blitz.py`로 캐시 병합(추론과 동일 SPY 프록시), 번들 재학습(blitz top-50 선택). **KR WF IC 0.0387→0.0412**(US는 노이즈 범위).

## 2026-06-19 — Wave5/6(swabs 채택) / Wave7(다호라이즌 누수→기각)

- **✅ |label| 샘플가중(mn_swabs) 채택·활성화**: 큰 변동 종목에 학습 가중 → 선택능력 sharpen. **양시장 IC↑(US 0.0278→0.0356, KR 0.0071→0.0109)**, bear 통과. 배선: `train_production` sample_weight=|mn label|. **번들 WF IC 양시장 최고: KR 0.0440, US 0.0313**(blitz US 하락분 상쇄+개선). 추론 smoke 정상.
- **Wave5/6 나머지 기각**: w5(리버설/계절성/상호작용 — KR IC붕괴), recency가중, decile변형(IC불변), 팩터중립화(시장갈림 KR↑/US↓).
- **Wave7 다호라이즌 라벨 — 누수 적발**: 1차 KR 245%/yr·IC 0.13 = 임팩트 불가능 → **21d 임베고 < 63d 라벨** look-ahead 누수 규명. embargo=63 교정 후 시장갈림(US IC↓) 기각. 교훈 [[feedback-no-complacency]]: 임베고≥최대 라벨 호라이즌.
- 진행중: Optuna OOF-IC 튜닝(opt_lab.py, reselect=False 빠른 탐색), triple-barrier 라벨(_tb_label).

**채택 레버 4개**: mn 라벨 + per-date 정규화 + Blitz resid-mom + |label| 샘플가중. 누적 번들 WF IC: KR 0.0387→**0.0440**, US 0.0289→**0.0313**.

## 2026-06-20 — 잔존(triple-barrier/Optuna) + 하니스 함정 수정

- **하니스 함정 적발·수정**: 번들 WF IC가 `pred vs 학습라벨`을 재서 라벨을 바꾸면 비교 불가였음 → **라벨무관 기준(실현 mn수익)으로 고정**(train_production). 라벨 비교가 정직해짐.
- ❌ **Optuna 튜닝 기각**: US OOS-IC BEATS(0.0379→0.0403)였으나 **KR 분산 폭발(±1.32→±8.13)**·IC 개선0 = US 과적합. cross-market 일반화 실패. ([[feedback-no-complacency]] 튜닝은 단일시장 IC에 과적합).
- ✅/❌ **triple-barrier 라벨 = US만 채택(per-market)**: 경로인지 라벨(±k·vol·sqrt(H) 첫터치 수익). 라벨무관 번들 WF IC로 **US 0.0313→0.0407(100% 양수fold, 채택)** / **KR 0.0325<mn 0.0440(기각, mn유지)**. 시장갈림이나 번들이 시장별 분리라 per-market 라벨 네이티브. `train_production --label`(기본 US=tb/KR=mn), `_tb_label` 재사용, **추론 불변**. 양 번들 재학습+smoke 정상.

**최종 채택: 공통 4개(mn족 라벨+정규화+Blitz+swabs) + US 전용 1개(tb).** 번들 WF IC: KR **0.0440**, US **0.0407**(시작 0.0387/0.0289 대비). 잔존: meta-labeling(사이징), uniqueness 가중.

## 2026-06-23 — 피처 frontier 점검 + macro-deep 기각 + 재점검 발굴

- **"전 카테고리 소진"은 과장 정정**: 진단상 I축 뉴스 40피처가 번들 top-50 선택 0개(2018-2024 뉴스 historical 없음), macro 35개도 거의 미선택. 모델·전처리는 소진했어도 **피처 공간 미답**.
- ❌ **Wave-4 macro-deep 상호작용 기각**: factor×regime/vix/yield/credit 13종+subset(vix/reg/macro) ablation 전부 US IC 미달(swabs 0.0356 > md 0.0246/vix 0.0308/reg 0.0347/macro 0.0166). **명시 상호작용 3연속 실패**(regfeat·Wave5·macro-deep) — GBDT가 이미 regime split으로 포착. 메모리 "+15-25%" 추정 근거없음 정정.
- 🔍 **"다 찾았나?" 2차 push → 간과 발굴**: ①**learning-to-rank**(L2회귀만 썼음, 과제=랭킹 불일치 — 구현·검증중, smoke US IC 0.041) ②cross-market pooled 학습 ③short-volume 피처(DB 17.9M행 미사용) ④PEAD/어닝서프라이즈(피처 0). → GAPS §C2.

> 교훈 [[feedback-no-complacency]]: "소진" 두 번 과장. 세게 보면 *미사용 데이터+간과 기법*이 계속 나온다. 특히 목적함수(회귀 vs 랭킹)를 평가지표와 맞추는 근본 레버를 놓쳤음.

---

## 2026-06-29 — 통합 파이프라인 구축(선정→실행) + 새 UX

원 제작자와 역할분담 확정 후 실제 개발 착수. **기존 기술적 요소(레드/그린 포함)=단일종목 매매 타이밍, 우리 횡단면 알파=지수투자·시황파악·종목선정**. 기존 코드 면밀 탐구 후 2계층 파이프라인 신설. 설계 확정: D1 타이밍만(veto 불가)·D2 신규 core-kr/us 계정·D3 레거시 잔존·D4 바스켓=능동지수+노출오버레이(ETF 미사용)·D5 균등비중. (설계: DESIGN_INTEGRATED.md)

- **P1 선정·시황 백엔드**(cbe23b7): `MarketRead`/`SelectionBasket` 모델+마이그레이션(0012). `decision/selection.run_selection`: 추천→시황read(regime별 노출 위기0/회피0.4/중립0.7/선호1.0 ×breadth nudge)+바스켓 산출·영속. smoke: US 노출0.7/바스켓52, KR 노출0.31(breadth 0.038 낮아 자동 축소)/바스켓32.
- **P2 실행 백엔드**(9519807): `integrated_runner` A+B(선정)→C(기술적 일봉 타이밍 게이트: 진입지연/약세청산, **D1 veto 불가**)→D(리스크/체결/2단계 감사). 사이즈=equity×목표노출×(1/n). `score_one_ticker` 재사용. smoke: KR 바스켓32→BUY15/WAIT17(기술 17종목 진입지연)·SELL0, US 바스켓52→BUY20/WAIT19/REJECTED13(리스크한도)·errors0 — 2단계 파이프라인 정합.
- **P3 스케줄러+테스트**(e7ab04c): `integrated.daily`(23:00 UTC)+`features.rebuild.daily`(20:00 UTC, **라이브캐시 staleness 갭 해소**: blitz 포함 재빌드). 단위테스트 5종(노출매핑/breadth-exposure/바스켓비중/타이밍임계값) 통과.
- **P4 새 UX**(2b7f349): 새 방향 전용 설계(기존 화면 복제 아님). `/api/integrated` 라우트(market-read/basket/execution) + 페이지 시황(노출게이지·breadth·확신도 2시장카드)/선정(바스켓 테이블·NEW/보유/제외 diff)/실행(BUY/WAIT/SELL/거절·기술점수·타이밍사유) + nav '통합 전략' 섹션. TestClient로 3엔드포인트 정상 검증(US 52종목 BUY20/WAIT19/REJECTED13, 샘플 SATS BUY tech=32.5).
- **P2.5 라이브캐시 재빌드**: `build_live_cache.py`로 _fs_ab(최근 365일, blitz 포함) 재빌드 → 라이브 잡 동작화 (진행중).

---

## 2026-06-30 — 통합 파이프라인 전수감사 + P0/P1/P2

**전수감사(6-pass)**: 모델·마이그레이션·선정·러너·라우트·스케줄러·프론트·문서·테스트 전량 검토. 정합 확인(recommend 출력열=선정 소비열, market_column=마이그레이션 String(8), 노출매핑·D1-D5·2단계감사 코드-설계 일치, 보안: decode verify_exp=False 후 유일호출자가 만료강제). 발견·해소:
- **F2 확신도 상수화(실질)**: `mean|rank_pct-0.5|`는 바스켓=top-decile이라 ~0.45 상수 → **바스켓 평균 예측 21일 수익**으로 재정의(US 4.1%/KR 2.4% 변동). selection.py+모델doc+시황UI.
- **F14 테스트갭**: `run_integrated_decisions` 단위테스트 6종 신설(진입/대기/청산×2/방어/거절).
- F1/F10 문서 정정, 미사용 import 정리. 커밋 `1a7cb65`.

**P1 breadth 캘리브레이션**: q50 절대수준 의존을 완화 — **가격기반 breadth(200일선 위 종목 비율, px_vs_sma200>0)를 모델 breadth와 평균 blend**. 캘리브레이션-무관 표준지표로 노출 오버레이 안정화(US 0.944→0.752, KR 0.804→0.763). 근거는 market_read.inputs에 pred/price 분해 기록. 단위테스트 추가.

**P2 통합 포트폴리오 화면**: `core_portfolio.js`(core-kr/us 평가금액·손익·MDD·승률·보유, 시황/선정/실행 링크). 라우트/nav/스크립트/CSS 배선.

**P0 통합 walk-forward 백테스트**(`scripts/backtest_integrated.py`) — paper 수개월 대신 역사로 조기 신호:
- ⚠️ **in-sample 함정 적발·회피**: 프로덕션 번들(2018-24 전체학습)로 2018-23 백테스트 시 US +29,700%(가짜). **walk-forward(매 시점 embargo 이전만 재학습)로 교정** → 정직 수치.
- 📉 **간이 프록시 알파 OOS ≈ 벤치**: US 20회 분기 리밸런스 — 알파바스켓 +30.8%(Sharpe0.43) vs 벤치 +36.7%(0.59) = **-5.9%p**. 단, 프로덕션 5레버 미복제 프록시라 과소평가. "선정 엣지 미묘" 재확인.
- 🟢 **타이밍 게이트가 값을 더함(통합가설 지지)**: 타이밍(cash) Sharpe 0.67, **타이밍(집중) +50.6%/Sharpe0.72로 벤치·알파 모두 상회**(+19.9%p vs 알파, invested 61%). "알파=WHAT, 기술적=WHEN"이 실제로 위험조정수익 개선.
- ⚠️ **as_of_ts 데이터한계 발견**: 역사 봉의 as_of_ts=일괄적재시각(~2026-06)이라 `score_one_ticker` PIT가드가 과거를 전부 거부(None). 백테스트는 trade_date 스코어러로 우회. 라이브는 정상. (GAPS 기록)

> 정직 종합: 배관은 견고하고, **타이밍 게이트는 역사에서 값을 더했다**(간이 알파 기준). 다만 선정 알파 자체의 OOS 초과수익은 미묘하며, 확정은 프로덕션 번들로 core-* paper 누적(P0 본체). 다음: 백테스트에 프로덕션 학습레시피 이식 + KR 백테스트 + paper 운영.

---

## 2026-06-30 (2) — 프로덕션 레시피 백테스트 + 양시장 매트릭스 + paper 운영 착수

**① 프로덕션 학습레시피 백테스트 이식**(`backtest_integrated.py --production`): 간이 rank 프록시 대신 **프로덕션 5레버를 walk-forward로 복제** — 라벨(US=tb 삼중배리어/KR=mn), per-date z-score 정규화, per-fold top-50 피처선택, |label| 샘플가중(blitz는 피처에 이미 포함). tb 라벨은 벡터화(pivot+shift)로 일회 계산 후 ≤train_cut만 학습(PIT-safe).

**②·완성된 프로덕션 2×2 매트릭스(walk-forward OOS, Sharpe):**

| 구성 | US(강세, 벤치+36.7%) | KR(약세, 벤치-17.1%) |
|---|---|---|
| 벤치(알파✗기술✗) | +36.7% / 0.59 | -17.1% / -0.11 |
| **알파 단독**(✓✗) | **+61.5% / 0.70** | **-11.5% / +0.03** |
| 병합·cash(✓✓) | +30.4% / 0.70 | **+2.7% / +0.11** |
| **병합·집중**(✓✓) | **+83.2% / 0.98** | -10.0% / +0.05 |

- **프록시가 프로덕션 알파를 크게 과소평가했음이 입증**: US 알파단독 프록시 +30.8%(벤치 하회) → 프로덕션 **+61.5%(벤치 +24.8%p 상회)**. 앞선 정직 단서가 정확.
- **프로덕션 알파는 두 시장 모두 벤치 상회**(US +24.8%p, KR +5.6%p) — 실 엣지 확인.
- **타이밍 게이트는 적응적**: US(강세)=집중으로 +83.2%/Sharpe0.98, KR(약세)=현금대기로 벤치 -17.1%를 **+2.7%로 방어(+19.8%p)**. "실행 타이밍"이 강세엔 공격·약세엔 방어.
- Sharpe는 타이밍으로 양 시장 개선(US 0.70→0.98, KR 0.03→0.11).
- 단서: 20회·특정구간이라 노이즈 존재하나, 프로덕션 레시피로 **정직한 초과수익·통합 시너지 재확인**.

**③ paper 운영 착수**:
- **자동 일일운영 배선 확인**: `WoonamScheduler`가 DEFAULT_JOBS 등록 + app lifespan에서 `start()` → **서버 기동만으로** features.rebuild.daily(20:00 UTC)→integrated.daily(23:00 UTC)가 매일 core-kr/us에 누적.
- **멱등성 검증**: `run_integrated_job` 2회 연속 실행이 동일 상태(BUY0/SELL0, 잔고불변) — 보유분 스킵·대기분 유지, 재실행 안전.
- **관측성**: core-kr(20보유/756K KRW)·core-us(20보유/731 USD) 시딩됨, market_read 일별 적재, 통합감사 242행, 통합 포트폴리오 화면으로 조회. P1 blended breadth(KR 0.763/US 0.752) 라이브 반영.
- 남은 것은 **시간**(수개월 누적으로 실현 성과 확인) — 코드/운영은 준비 완료.

> 종합: 프로덕션 레시피로 재보면 **알파는 실재하고(양시장 벤치 상회), 타이밍 게이트는 적응적으로 값을 더한다**(강세 공격/약세 방어, Sharpe 개선). 통합(선정→실행) 설계가 정직한 walk-forward로 뒷받침됨. 최종 판정은 paper 누적.

---

## 2026-06-30 (3) — 통합 전략 전차원 스윕 + regime-적응 병합

"테스트 가능한 모든 가능성" 지시 → `backtest_integrated.py --sweep`로 **1패스에 그리드 전체** 측정(리밸런스마다 프로덕션 모델 1회 학습 + 상위20% 기술스코어 1회 계산 후 조합을 메모리 평가). **바스켓크기{5,10,20%} × 진입임계{none,-20,-10,0,+10,+20} × 병합{cash,집중} = 36조합 × 2시장 = 72.** (이미검증 알파레버 mn/tb·zscore·top50·|label|가중은 ALPHA_CAMPAIGN.md 참조, 재측정 안 함.)

**핵심 발견 — 최적 병합이 regime으로 뒤바뀜:**

| 시장 | 상위 config (Sharpe) | 지배 패턴 |
|---|---|---|
| US(강세, 벤치 0.59) | 20%/+20/집중 **1.10** · 10%/0/집중 +83.2%/0.98 | **집중** 지배(상승 증폭) |
| KR(약세, 벤치 -0.11) | 5%/+20/현금 **0.53** · 20%/+10/현금 0.32 | **현금** 지배(하락 방어) |

- **단일 최적 config 없음** — 강세엔 통과분 집중, 약세엔 현금 대기가 정답. regime이 스위치.
- no-timing(none) 대비 timing이 양 시장 상위권을 채움 → 타이밍 게이트 유효 재확인.
- 진입임계는 0~+20이 상위(엄격할수록 강한 기술만); 바스켓 10%가 US 스윗스팟(집중 시).

**→ regime-적응 병합 구현**(integrated_runner): `target_exposure ≥ CONCENTRATE_MIN_EXPOSURE(0.6)`면 통과분에 노출 **집중**(bull), 미만이면 **현금-스타일** 슬롯 유지(bear). 노출 오버레이(regime×breadth)가 스위치. 2단계 ENTRIES(타이밍→사이징)로 리팩터, `rep.concentrated` 리포트. 단위테스트+라이브 smoke 통과.

**step(리밸런스 주기) 비교 → 노이즈 노출 + regime-적응 재확인:**

| | US alpha vs벤치 | US 집중 Sharpe | KR 벤치 | KR 최적병합 |
|---|---|---|---|---|
| step=63 | +24.8%p | 0.98 | -17.1%(하락) | **현금**(+2.7 vs 집중-10) |
| step=42 | +4.0%p | 0.55 | +20.4%(상승) | **집중**(+3.5 vs 현금+1.2) |

- ⚠️ **매그니튜드는 step(날짜샘플)에 민감** — KR 벤치가 부호까지 뒤집힘(-17%↔+20%). step=63의 강한 US 수치(Sharpe0.98)를 과신 금물, ~20-30윈도 표본 한계.
- 🟢 **방향성은 견고**: 알파≥벤치(US 양 step), 그리고 **KR이 하락(63)→상승(42)으로 바뀌자 최적 병합이 현금→집중으로 동반 반전** → regime-적응(상승=집중/하락=현금)이 시장무관·**방향종속**임을 재확인. 구현이 옳은 방향.
- 결론: 방향성 채택(구현 완료), 매그니튜드는 paper 누적으로 확정. 진입임계·바스켓크기는 0/10% 기본 유지(스윕 상위권).

**윈도-단위 regime-split(`--regime-split`) → 가설 결정적 검증 (US-약세 셀 포함):**

각 리밸런스 윈도를 벤치 부호로 UP/DOWN 분리 후 집중 vs 현금 비교(시장·step 교란 제거). 이전엔 "US=강세/KR=약세"로 시장에 묶여 US-하락을 못 봤으나, 윈도 분리로 **4칸 전부** 확인:

| | UP(상승) 집중 vs 현금 | DOWN(하락) 집중 vs 현금 |
|---|---|---|
| US(29윈도) | +4.69% > +2.17% → **집중** | −4.85% < −3.28% → **현금** (n=9: 2020·2022 약세) |
| KR(29윈도) | +6.04% > +3.03% → **집중** | −9.21% < −5.07% → **현금** (n=10) |

- **양 시장 양 방향 일관**: 상승 윈도엔 집중(+2.5~3%p/win 이득), 하락 윈도엔 현금(+1.6~4.1%p/win 방어). regime-적응이 **시장무관·방향종속**임이 ~58윈도로 확정.
- 사용자 지적("US-약세 미분석")이 이 결정적 테스트로 이어짐 — US 하락 윈도가 예측대로 현금 우세, 가설 강건.

**⚠️ direction-test(`--direction-test`) → 스위치 자체는 방향 예측 못 함 → 집중 기본 OFF 교정:**

regime-split은 "**방향을 알면** 적응이 이롭다"만 보였음(실현 부호=사후지식). 사용자 재지적("상승/하락 구분은 힘들다며?")에 스위치의 *예측력*을 직접 검증:
- **US(58윈도)**: breadth(t) vs forward 벤치 Spearman **rho −0.14**(역전!), 고breadth fwd +0.77% < 저breadth +1.76%, 방향적중 57% < baseline 64%.
- **KR(57윈도)**: rho −0.04(무예측), 고 +1.68% ≈ 저 +1.69%, 적중 49% < baseline 65%.
- **결론**: 가격 breadth는 forward 21d 방향을 **예측 못 함**(오히려 약간 평균회귀). → 집중은 우리가 못 하는 방향 예측에 거는 도박.
- **비대칭 원칙**: 위험회피 노출 축소(방어, VIX↑면 줄이기)는 방향 예측 불필요 → 견고. 상승장 집중(공격)은 정확한 강세 예측 필요 → 불가. 
- **교정**: `run_integrated_decisions(concentrate=False)` **기본 현금-스타일**(예측 불필요·방어적). 집중은 opt-in 실험으로만, **검증된 forward-방향 신호가 나올 때까지 미채택**. rep.concentrated는 실제값 반영. 테스트 갱신.

> 교훈: 백테스트가 "조건부 이득"(방향 알 때)을 보여도, 그 조건을 라이브에서 만들 수 있는지(방향 예측)를 별도 검증해야 함. 사후지식 성과를 라이브 엣지로 오인 금지. [[feedback-no-complacency]]

**정정(2026-07-02) — "방향 예측 불가"는 성급했음. 전용 모델은 방향 신호 있음:**

direction-test는 **breadth 하나**만 봤음(평균회귀 IC −0.14). 사용자 재지적("이렇게 포기?")에 제대로 재시도 — `market_direction_test.py`: VIX(+percentile)·HY신용스프레드·DXY·SMA추세·시장모멘텀(63/126/252d)·regime분류기출력을 **시장레벨 집계 → forward 21d 시장수익 walk-forward 예측**(비중첩 평가):
- **US**: Spearman IC **+0.135**, 상승-콜 윈도 fwd +1.75% vs 하락-콜 +0.46%(분리 +1.29%p). 타이밍 Sharpe 0.69 vs BH 0.72.
- **KR**: IC **+0.119**, 상승-콜 +2.44% vs 하락-콜 +0.65%(+1.79%p). 타이밍 Sharpe **0.75 > BH 0.71**.
- top신호: SMA50>200 비율·supertrend·시장 252/126/63d 모멘텀·VIX·VIX백분위 (고전적 추세+변동성 타이밍).
- **결론**: breadth 단독 실패 ≠ 방향 예측 불가. **전체 신호 모델은 양 시장서 방향을 유의미 분리**(IC ~0.12). 집중 레버를 이 모델로 게이팅하면 살아날 가능성 → `--direction-gated`로 페이오프 검증중.
- 단, IC 0.12는 수수하고 US 타이밍은 BH 소폭 하회(강세장 현금비용) — 확정 아님. 게이팅 결과가 판정.

> 재교훈: "한 신호(breadth) 실패"를 "전 신호 실패"로 일반화한 것도 성급한 소진. 방향 예측은 *어렵지만 불가는 아님* — 표준 타이밍 신호 전체를 써야 함. [[feedback-no-complacency]]

**페이오프 검증(`--direction-gated`) → 부분적 승리(US 성공, KR 실패):**

방향 모델로 집중/현금 라우팅(상승-콜=집중, 하락-콜=현금) vs 고정 정책:

| | 벤치 | 현금고정 | 집중고정 | **방향게이팅** |
|---|---|---|---|---|
| US(29윈도) | +40.9%/0.62 | +12.6%/0.31 | +51.3%/0.55 | **+55.7%/0.68** ← 전부 상회 |
| KR(29윈도) | +20.4%/0.29 | +1.2%/0.09 | +3.5%/0.17 | **−8.6%/0.01** ← 현금고정보다도 나쁨 |

- **US 성공**: 게이팅이 벤치·집중고정·현금고정 **모두 상회**(Sharpe 0.68). 방향 모델(IC 0.135, 정확도 57%)이 집중 레버를 살림.
- **KR 실패**: KR 방향모델 정확도(54%<baseline 65%)가 게이팅이 요구하는 수준에 못 미쳐, 틀린 상승-콜에서 집중이 손실 증폭.
- **정직 결론**: "방향 예측 불가"는 틀렸음(US서 게이팅 명백히 작동). 그러나 게이팅은 **방향모델이 충분히 정확한 곳에서만** 페이오프 → 보편 채택 불가. US는 유망, KR은 모델 강화 필요.
- **채택 상태**: 집중은 여전히 기본 OFF. 방향모델 게이트가 (exposure/breadth 게이트보다) **옳은 메커니즘**임이 US서 입증. 라이브 채택 전 필요: ①KR 방향모델 강화(풀드 학습/KR고유 매크로/신뢰임계) ②더 많은 윈도 검증 ③방향모델을 라이브 러너에 컴포넌트로 통합.

> 종합: 처음 "무의미?"에서 → 방향 신호 실재 확인 → US서 집중 레버 페이오프 입증. 완전 부활은 아니나 **명확한 전진**. 다음 레버: 방향모델 강화(특히 KR).

**게이팅 전면 검증(신뢰임계 스윕 × 풀드학습) + 라이브 배선(T1~T4):**

방향-게이팅을 끝까지 검증(임계 {0,+0.3,+0.5,+1.0,+1.5%} × per-market/pooled):
| | 최선 게이팅 | vs 벤치/현금/집중고정 |
|---|---|---|
| **US** | per-market@+1.0% +58.7%/0.72, **pooled@0 +60.0%/0.72** | 전부 상회(벤치0.62) — 견고 |
| **KR** | 전 임계·per-market·pooled **모두 음수/현금고정 미달** | 실패 — KR forward 방향 예측 부족 |

- **US: 방향-게이팅 집중 채택가(검증)** — 임계·풀드로 0.72까지. **KR: 임계+풀드 다 시도해도 실패** → KR은 현금 유지가 정답. **시장별 결정 확정.**
- **T3 라이브 배선**: `decision/market_direction.py`(시장방향 모델, `predict_direction`) + 러너 `direction_score` 게이트(집중은 방향-업일 때만) + 잡이 방향점수 계산·로깅. **concentrate 기본 OFF**(config `CONCENTRATE_BY_MARKET`), 활성화 전제: 라이브용 장기 방향캐시(현 365일=250거래일은 검증 장기히스토리 대비 얇음)+paper 베이스라인. 라이브 확인: US dir −0.015/KR +0.213 로깅, concentrate=False 안전.
- **T4 리밸런싱**: `rebalance_band` opt-in 트림(승자 과중 방지).
- 테스트 584 통과(방향모델·게이팅·리밸런싱·트림 커버).

> no-complacency 3회 루프: 포기→재도전→breadth실패→전체모델→게이팅→임계/풀드까지 소진. 결과: US 레버 검증·배선(활성화 대기), KR 정직한 미해결(현금 유지). "다 해봤다"에 근접한 소진 후의 정직한 부분승리.

**T5/T6 잔여 과업 소진:**
- **T5 as_of_ts 백필(완료·적용)**: `scripts/backfill_as_of_ts.py`(dry-run 기본, --apply)로 as_of_ts를 trade_date 장마감 UTC로 재계산(US131만+KR74만=205만행). **역사 PIT 기술 스코어링 복구**(score_one_ticker @2021 = 20.0, 이전 None; 라이브도 정상). GAPS 한계 해소.
- **T6 KR winsor(측정·기각)**: KR alpha zscore(-11.5%/+5.6%p) vs winsor(-9.4%/+7.6%p) — winsor 총수익 +2%p 나으나 **Sharpe 동일(0.03), 여전히 음수** → 알파캠페인 "wash" 재확인. zscore 프로덕션 기본 유지, winsor는 `--norm-mode winsor`로 선택 가능(채택 안 함).
- 584 테스트 통과.

> 이번 배치(T1~T6)로 열린 과업 소진: 방향레버(US검증/KR미해결)·리밸런싱·as_of_ts복구·winsor측정. 남은 근본 상한은 여전히 (a)알파 포화 (b)대체데이터(저장공간) (c)paper 시간 — 코드/검증로 더 짜낼 여지는 이번에 대부분 소진.

**국면 3분(상승/횡보/하락) × 전략 매트릭스(`--regime-split` 3분화, |bench 21d|<=2%=횡보):**

| 구간 | US 최선(윈도당%) | KR 최선 |
|---|---|---|
| 상승(>+2%) | **알파단독** +7.25(현금 +3.52 최악) | **알파단독** +8.98 |
| 횡보(±2%) | 집중 +1.93(소액) | 알파 +1.27(소액) |
| 하락(<-2%) | **현금타이밍** -2.99(알파 -6.09) | **현금타이밍** -4.90(알파 -10.45) |

- **명쾌한 역할 분담**: 현금타이밍=방어전용(하락 최선/상승 최악), 알파단독=공격(상승 최선/하락 최악), 집중=중간(횡보 소폭우위). 국면만 알면 각 구간 1등 취함 → 방향-게이팅이 US서 통한 이유.
- 단서: 실현국면 사후분류(hindsight) → 활용엔 국면예측 필요(US 방향모델 O/KR X). 58·57윈도 노이즈. 방향성 견고, 크기는 paper. 현 기본값(현금)은 하락방어 우선 보수 선택.

**정책 A(집중) vs B(풀투자) 게이팅 — 기존 설계 검증, B 기각:**

매트릭스가 "진짜 상승 윈도엔 알파단독>집중"이라 해서, **예측-상승 시 풀투자(B)** 가 **집중(A)** 을 이기는지 테스트:
- US pooled@0: **A(집중) +60.0%/0.72 vs B(풀) +45.4%/0.58** → **A 우세**(per-market도 A 58.7 vs B 43.9).
- **이유**: 매트릭스의 "알파>집중"은 *완벽한 방향지식* 전제. 실제 방향모델은 **오탐(false-up)** 존재 → 오탐 윈도에서 B(전 바스켓 노출)는 손실 그대로, A(타이밍 통과분만)는 **기술필터가 손실 완충**. **불완전 예측 하에선 집중의 타이밍 필터가 오탐을 쿠션**.
- **결론**: 현 구현(A=집중 게이팅)이 옳음. "상승장 풀투자(B)"는 유혹적이나 테스트로 기각 — 검증 없이 채택했으면 실수. hindsight 조건부 최적(알파)을 노이즈 예측기에 그대로 적용하면 안 됨을 재확인.

**전체가능성 추가 소진 — 방향모델 개선 + 타이밍 신호 ablation:**
- **방향모델 스윕**(`direction_sweep.py`, 회귀/분류×6피처그룹): **US clf/all 68%>baseline 63%**(회귀 58%보다 개선!), **KR은 clf도 62%<64%**(대부분 always-up 편향, 분리력 없음) → KR 방향 예측 불가 재확정.
- **clf 채택**: `market_direction.predict_direction(mode="clf")` 기본화. clf-게이팅 US pooled **+61.0%/Sharpe0.73 > reg +60.0%/0.72**(방향정확도 68>58 반영). 소폭이나 실측 개선.
- **타이밍 신호 ablation**(`--timing-ablation`, 신호명 수정 후 clean): 하락장 방어 cash/win —
  - US(벤치-5.25): momentum -6.83(방어↓!)·trend -4.67·meanrev -5.60·**red_green -3.35★**·composite -3.60
  - KR(벤치-8.78): momentum -6.38·trend -7.12·meanrev -8.00·**red_green -5.93★**·composite -6.07
  - **red_green이 양 시장 방어 1등**(composite보다 약간 나음, 방어 주동력). **momentum은 방어 약화**(상승캡처 최고나 하락 해로움). 상충: red_green=최고방어/최저상승, momentum=반대. **어느 단일신호도 양축 지배 못함 → composite 유지 합리적.** red_green=방어담당 확인. 리드(defer): momentum 축소한 방어편중 가중.

---

## 2026-07-02 (2) — 전수 가능성 워크플로우 + 거래비용 발견(중요)

**멀티에이전트 워크플로우**(14축 병렬탐색+5발견 적대검증+종합; 검증/종합은 세션한도로 중단→직접수행): 12축 평가 확보. **오직 `transaction_costs`만 run 권고**, 나머지(멀티호라이즌·노출오버레이·롱숏·섹터중립·red_green단독·defense-tilt=skip / 리스크한도·라벨앙상블·tech-as-feature·exit임계·vol사이징=defer)는 이미소진/저가치/노이즈. **독립 전수탐색이 "가능성 공간 대부분 소진, 남은 고가치 미탐=거래비용" 확인.**

**직접 적대검증(5발견)**: 프로덕션알파 벤치상회=holds(방향, embargo정확·크기noisy) · 집중-A>풀투자-B=holds(오탐쿠션) · red_green=방어주동력=holds(composite대체는 미지지) · KR방향불가=holds(가용피처, **단 KR고유신호 외국인수급·원화·중국은 캐시부재로 미검증**) · clf게이팅0.73>0.72=**suspect**(29윈도서 0.01은 노이즈, 단 clf정확도68>58%는 견고→채택유지·개선폭 과신금지).

**🔴 거래비용 발견(`--cost-test`, 회전율×round-trip bps 차감) — 무마찰 백테스트가 타이밍 엣지 과대평가:**

| 순 Sharpe | US alpha/cash/conc | KR alpha/cash/conc |
|---|---|---|
| 0bps | 0.60/0.45/0.44 | 0.40/0.40/0.49 |
| 40bps | 0.53/0.27/0.35 | 0.34/0.29/0.43 |
| 60bps | 0.49/**0.18**/0.31 | 0.31/0.23/0.40 |
| 회전율/rebal | alpha 54%/**cash 71%** | alpha 70%/**cash 80%** |

- **거래비용이 타이밍(cash)을 가장 심하게 침식**(회전율 최고 71-80%): US cash Sharpe 0.45→0.18(60bps, **60% 증발**). 알파단독(회전율↓)은 0.60→0.49로 견고. concentrate 중간.
- **무마찰 백테스트가 타이밍 게이트 엣지를 과대평가.** 현실 비용(US~20-40bps, KR~60bps)서 타이밍 매력 급감.
- **→ 회전율 통제가 신규 실질 레버**(리밸런싱 트림은 가중드리프트용, 회전율 감축 아님). 바스켓 히스테리시스(`--hysteresis`, 보유종목 넓은밴드 유지) 테스트중.
- 주의: LGBM n_jobs=-1 비결정성으로 gross 절대값 run간 변동(예 alpha gross Sharpe 0.53~0.60), **비용 상대영향(회전율 높을수록 침식↑)은 견고**.

**🟢 회전율 통제(바스켓 히스테리시스) — 검증·구현:** 보유종목을 top-(decile+band) 내면 유지(청산 안 함).
- 실측(US, 밴드0.10): **알파 회전율 54%→37%**, **순Sharpe@40bps 0.53→0.59 / @60bps 0.49→0.56**(gross도 0.60→0.63 소폭↑ — 승자 홀딩 momentum). 거래비용 침식을 부분 상쇄.
- (cash-timing 회전율은 61%로 잔존 — 기술필터 churn 지배. 타이밍 히스테리시스는 아래 별도 보정.)

**타이밍 히스테리시스 보정(`--timing-hyst`) — naive 백테스트가 cash 회전율 과대추정:** cost-test cash leg를 러너 실제거동(진입 tech≥0/청산 tech≤-30, 사소한 하락엔 홀딩 유지)으로 보정. (cost_test/timing_ablation을 use_prod에 추가해 production 알파로 통일.)
- US production: cash 회전율 **77%→63%**, 순Sharpe **@40bps 0.11→0.26 / @60bps 0.01(사망)→0.19(생존)**, gross@0 0.31→0.41. **비용 피해 대략 절반으로 교정.**
- 정정: 무마찰이 엣지 과대평가는 맞으나, **러너 히스테리시스(타이밍ENTRY/EXIT+바스켓)가 회전율 완화**해 실제 비용피해는 naive 추정의 ~절반. 그럼에도 cash-timing은 비용조정 최약체(alpha/concentrate 우위) — 방어 성격 재확인.
- 러너 구현: `run_integrated_decisions(basket_hysteresis=0.0)` 기본 OFF, EXIT 로직이 hold band 내 보유종목은 exit_basket_drop 면제. recs rank_pct로 판정. 단위테스트+라이브 smoke 통과(20테스트). **거의 무손실 개선이라 default-on 유력 후보이나 29윈도라 opt-in 유지, paper 확인 후 상향.**

---

## 2026-07-03 — 미탐 축 추가 소진: 포지션 스톱(최대 발견)·LTR·숏볼륨

저장공간 미확보로 뉴스 10년백필은 보류. 그 외 미탐 축 테스트:

**🟢🟢 포지션 스톱(SL/TP/트레일링) — 이번 세션 최대 발견, 채택:** `--stops-test`(21일 보유 중 일봉경로에 스톱 적용).

| config | US Sharpe / 최악윈도 | KR Sharpe / 최악윈도 |
|---|---|---|
| none | 0.53 / -14.8% | 0.27 / -31.0% |
| **SL10(손절10%)** | **0.75 / -8.5%** | **0.99 / -9.7%** |
| SL15 | 0.64 / -10.7% | 0.68 / -14.5% |
| TP20(익절) | 0.43(↓) | -0.02(↓↓) — 승자 조기청산 해로움 |
| trail10 | 0.55 / -9.7% | 0.68 / -12.5% |

- **10% 손절이 최악윈도 손실을 절반으로 줄이며 Sharpe·총수익도 개선**(패자 조기청산, 승자 홀딩). 양시장 강력. **익절(TP)은 해로움**("손실 자르고 이익 달리게" 재확인).
- **채택(기본 ON)**: 손절은 수익추종 베팅이 아닌 **리스크 관리**라 기본 켬이 타당. 러너 `stop_loss` 파라미터(일봉 체크, entry×(1-sl) 하회 시 exit_stop_loss) + 잡/스케줄러에 **0.15** 배선(백테스트 최적 0.10보다 넓게 — 실제 갭·휘프소가 clean 일봉백테스트보다 나쁨 보정). rep.stops 카운터. 단위테스트 2종+라이브 smoke(22테스트).
- 단서: 29윈도 노이즈, 단순 일봉스톱이라 실제 슬리피지·갭다운은 더 나쁨 → 방향 견고/크기 낙관적. KR 0.27→0.99는 2018-23 약세장서 스톱이 큰 낙폭 회피한 영향.

**숏볼륨(C) 종결:** short_ratio/short_squeeze 피처는 캐시에 있으나 알파 top-50 미선택(중요도 순위 149~441/464). 숨은 알파 아님 — 알파포화 재확인.

---

## 보류 결정 (status=proposed)

- ⏸️ **10년치 뉴스 백필 — 저장공간 확보 후 진행**(user 2026-06-09 결정). 현황: 뉴스가 ~6~7개월치(GDELT 영어 141.8만 2025-12~2026-06, Naver 한국어 2.3만)뿐이라 I축 historical이 F/T(10년)에 비해 빈약.
  - **영어/GDELT**: GKG는 2015~ 존재해 확장 가능하나 **BigQuery 스캔 ~5.5TB**(180일=275GB 실측 × 20). 무료 한도 월 1TB → 한번에 하면 ~$22 과금(무료제약 위반). 옵션: ①월1TB 분할(무료,~6개월) ②원본 CSV 다운로드로 재작성(진짜 무료, ~5TB 디스크 필요) ③소액 과금. **저장공간(~5TB)+분할수집 전제라 보류.**
  - **한국어**: Naver API=최근만(깊은 과거 불가), BIGKinds=로그인월·이전 크롤 0건 실패. **KR 10년 뉴스는 무료로 사실상 막힘** → 별도 BIGKinds 스크레이퍼 해결 필요.
  - 공시(EDGAR/DART)는 이미 수년치 보유 → I축의 "공식 정보" 신호는 깊이 확보됨.
- ⚪ KR 시가총액 프록시를 KRX CSV 스크래퍼(선호) 또는 pykrx(엔드포인트 복구 시)로 교체
- ⚪ Phase 6 브로커 어댑터 — KIS Developers (KR 무료) vs Alpaca paper (US 무료); 둘 다 페이퍼 3개월 통과 후 착수
- ⚪ TimescaleDB 설치 vs 일반 Postgres 유지 (행수 증가 속도에 따라 결정)
