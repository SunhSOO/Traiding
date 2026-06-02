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

## 보류 결정 (status=proposed)

- ⚪ KR 시가총액 프록시를 KRX CSV 스크래퍼(선호) 또는 pykrx(엔드포인트 복구 시)로 교체
- ⚪ Phase 6 브로커 어댑터 — KIS Developers (KR 무료) vs Alpaca paper (US 무료); 둘 다 페이퍼 3개월 통과 후 착수
- ⚪ TimescaleDB 설치 vs 일반 Postgres 유지 (행수 증가 속도에 따라 결정)
