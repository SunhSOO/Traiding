# GAPS — 전체 상태 / 미실현·구현·측정 전수 목록

> **이 문서의 목적**: "전부 시도" 원칙을 지키기 위한 영구 산출물.
> 작업하면서 발견되는 누락은 *즉시* 이 문서에 append.
> 채팅/회의에서만 논의하지 말 것. 휘발됨.
>
> **상태 표기**:
> - ⬜ TODO — 미구현
> - 🟦 WIP — 구현 진행 중 (background)
> - ✅ DONE — 구현 + 측정 완료
> - ⏭️ EXCLUDED — 측정 결과 효용 없음 (이유 기록)
> - ⛔ BLOCKED — 외부 의존성 부재 (이유 기록)

---

## 0. 구현·측정 완료 종합 (Phase 별)

이 섹션은 *지금까지 구축한 모든 것*. 카테고리별 상세는 A-W 섹션 참조.

### 0.1 Phase 0 — 기반 인프라 (Sprint 2026-05-26~05-29)
- ✅ PostgreSQL 16 + 27 테이블 + alembic 0001~0011 마이그레이션
- ✅ FastAPI lifespan 부트스트랩 (woonam 운영자 + default-kr/-us paper account)
- ✅ Auth: JWT HS256 + bcrypt<4.0 + python-multipart
- ✅ Risk Engine + RiskLimits (max_lot/daily_loss/consecutive_loss/max_positions/allowlist)
- ✅ decision_audit + risk_snapshots 테이블
- ✅ paper_accounts/paper_positions/paper_trades 영속화
- ✅ as_of guard (look-ahead bias 차단 데코레이터)
- ✅ structlog JSON 로깅
- ✅ APScheduler 17 잡 + mlops.retrain.weekly + mlops.drift_check.daily (총 19)

### 0.2 Phase 1 — 데이터 인제스트
**Universe**:
- ✅ securities 870 (KR 350 marcap 프록시 + US 503 SP500 + 17 cross-asset ETF)
- ✅ universe_membership (KR fdr_marcap_proxy, US wikipedia-derived)
- ✅ corp_code 343/350 KR (DART)
- ✅ CIK 501/503 US (SEC EDGAR)

**Daily prices (315,781 rows)**:
- ✅ KR 350 종목 × 540일 (FDR)
- ✅ US 503 종목 × 540일 (FDR, 22개 gap fill 후)
- ✅ 17 cross-asset ETF × 540일 (XLK/XLF/XLV/XLE/XLY/XLP/XLI/XLB/XLU/XLRE/XLC + SPY/QQQ/IWM + GLD/USO/TLT)
- ✅ as_of_ts 정정 (307k 행, trade_date+16h UTC)

**Financial facts (588,294 rows)**:
- ✅ US 574,071 (EDGAR companyconcept 501 종목 17 concepts)
- ✅ KR 14,223 (DART finstate 317 종목 3년 ANNUAL × 17 concepts)
- ✅ Historical Fundamental 45,090 rows (US 365일 × 501 종목)

**Disclosures (379,673 rows)**:
- ✅ EDGAR INSIDER (Form 4) 302,073 metadata
- ✅ EDGAR MATERIAL_EVENT (8-K) 58,581 metadata
- ✅ EDGAR QUARTERLY (10-Q) 13,473 metadata
- ✅ EDGAR ANNUAL (10-K) 4,703 metadata
- ✅ Form 4 본문 5,000건 XML 파싱 (buy/sell direction + value + CEO/Director 역할 → JSON)

**News (1,441,239 articles, 275,618 mentions)**:
- ✅ GDELT GKG 1,418,585 articles + 230k US mentions (180일, SP500, 275GB 스캔)
- ✅ Naver 검색 API 22,654 articles + 45k KR mentions (350 종목, 100건/쿼리)
- ✅ RSS 1,500+ articles (MK/한국경제/CNBC/연합인포맥스)
- ✅ Ollama qwen2.5:14b 분류 1,446건

**Macro (5,749 rows, 13 시리즈)**:
- ✅ FDR: VIX, IDX_SP500_FRED, IDX_KOSPI_ECOS, FX_DXY, RATE_US_10Y (^TNX)
- ✅ FRED 정식: DGS10, DGS2, DGS3MO, FEDFUNDS, CPIAUCSL, M2SL, UNRATE, INDPRO, PAYEMS, RSAFS
- ✅ BOK ECOS: 기준금리, CPI_KR

**Module scores (570,773 rows historical)**:
- ✅ Technical 365일 백필: KR 31,212 + US 45,090 + 라이브 849 = ~285k
- ✅ Fundamental 365일 백필: 172,680 US (단일 라이브 105)
- ✅ Information 라이브: 134건 (KR 91 + US 43)
- ✅ as_of_ts 정정 + 1차/2차 재실행으로 누락 보강

**Cluster + Regime**:
- ✅ ticker_clusters 4,265 rows (853 종목 × 다중 할당 시점)
- ✅ market_regime 6 rows (KR/US 분류)

### 0.3 Phase 2 — Feature engineering

**총 131 features 구현 + LightGBM trainer 사용 중**:
- ✅ Price/Technical 50 (RSI 5/14, MACD-hist/cross, BB-pctB/squeeze, ADX, Stoch-K, Williams-R, MFI, CMF, ATR%, OBV slope, Ulcer, Donchian-pos 20/55, Aroon up/dn/osc, ROC 10/21, multi-horizon returns 1/2/3/5/10/21/42/63/126/252d, vol 5/21/63/252d, skew/kurt 21d, sharpe 21/63d, drawdown 63/252d, range %, volume z 21/63, gap %, SMA 20/50/200 비교, candle body/wick %, doji)
- ✅ Fundamental 10 (PE-TTM, PB, EV/EBITDA, ROE-Q, ROA-Q, debt/equity, current ratio, gross margin, rev YoY, EPS YoY)
- ✅ Information 6 (news count 7d/30d, sentiment 7d, pos/neg count 7d, impact 7d)
- ✅ Disclosure 6 (insider count 7d/30d, 8K count 7d/30d, days since 10K/10Q)
- ✅ Insider (Form 4 body) 7 (net_value 7d/30d, buys/sells 30d, CEO/Director buys, buy_sell_ratio)
- ✅ Macro 18 (VIX/VIX 5d-21d chg, DXY 5d-21d chg, SP500 21d-63d ret, US10Y level + 5d-chg, US2Y level, yield curve 2-10, FEDFUNDS, CPI YoY, M2 YoY, UNRATE level + 3d-chg, KR base rate, CPI KR YoY)
- ✅ Regime 3 (risk-on/-off one-hot + confidence)
- ✅ Calendar 9 (dow/dom/doq/doy/month/quarter/days-to-q-end/is-jan/is-dec)
- ✅ Cross-asset 18 (각 17 ETF 21d 상대 momentum + corr_spy_63d)

**기타**:
- ✅ daily_prices.as_of_ts 정정 (trade_date 기반)
- ✅ Disclosure 캐시 (1회 bulk load, ticker별 lookup)
- ✅ Cross-asset 캐시 (panel 1회 로드)

### 0.4 Phase 3 — ML 학습 + Optuna 튜닝

**Trainer 인프라**:
- ✅ training/lgbm_trainer.py (date-grouped CV + 21d embargo, LGBM 단독)
- ✅ training/multi_trainer.py (LGBM/XGB/CatBoost/Ridge 통합)
- ✅ training/lstm_trainer.py (PyTorch seq_len=30 hidden=128 2-layer dropout)
- ✅ training/optuna_tuner.py (TPE + 8-dim 검색공간 per model)
- ✅ training/model_registry.py (var/models/runs/<run_id>/ + registry.json + find_best)
- ✅ training/ensemble.py (IC-weighted top-K)
- ✅ training/ensemble_spec.py (per-(cluster,target) 영속 spec)
- ✅ training/ensemble_optimizer.py (forward selection + 3-window holdout + 통계 유의 검증)
- ✅ training/drift_detector.py (KS-test + rolling IC drop ratio)
- ✅ decision/ensemble_decision.py (inference bridge, booster cache, mtime invalidation)

**Trained models (현재 registry 22 모델, 계속 증가 중)**:
- ✅ KR:FIN:LARGE — LGBM/XGB/CatBoost × (default + 30-trial + 100-trial) = 9 모델, + LSTM 1
- ✅ US:ENERGY:LARGE — 동일 패턴 9 + LSTM 1
- ✅ KR:OTHER:LARGE — 100-trial × 3 + (default WIP)
- ✅ KR:OTHER:MID — default × 3 (모두 weak)
- ✅ KR:FIN:MID — default × 3
- ✅ KR:OTHER:SMALL — default × 3 (CatBoost +0.209 ⭐)
- ✅ US:FIN:LARGE — default × 3 (CatBoost +0.230 ⭐)
- ✅ US:TECH:LARGE — default × 3 (CatBoost +0.233 ⭐)
- ✅ US:INDUSTRIALS:LARGE — default × 3
- ✅ US:CONS_DISC:LARGE — default × 3
- ✅ US:FIN:MID — default × 3 (CatBoost +0.218 ⭐)
- 🟦 KR:FIN:SMALL 100-trial × 3 진행 중
- 🟦 US:UTILITIES:LARGE 100-trial × 3 진행 중
- 🟦 US:HEALTH:LARGE default × 3 진행 중
- 🟦 US:MATERIALS:LARGE / COMM:LARGE / CONS_STAPLES:LARGE / REAL_ESTATE:LARGE default × 3 진행 중
- 🟦 KR:FIN:SMALL / KR:OTHER:SMALL / US:COMM:MID / US:REAL_ESTATE:MID / US:INDUSTRIALS:MID / US:CONS_STAPLES:MID default × 3 진행 중

**측정된 IC (OOF + holdout)**:
- ✅ US:ENERGY:LARGE tuned LGBM 100-trial **IC +0.353** (펀드급 신호)
- ✅ KR:FIN:LARGE default CatBoost **IC +0.262** (튜닝보다 강함)
- ✅ US:TECH:LARGE default CatBoost **IC +0.233**
- ✅ US:FIN:LARGE default CatBoost **IC +0.230**
- ✅ US:FIN:MID default CatBoost +0.218
- ✅ KR:OTHER:SMALL default CatBoost +0.209
- ✅ KR:OTHER:LARGE tuned CatBoost +0.091, hit 69%
- ✅ KR:FIN:MID default CatBoost +0.103
- ✅ LSTM US:ENERGY +0.227, LSTM KR:FIN:LARGE +0.074
- ✅ Holdout test US:ENERGY default LGBM **+0.200** (OOF +0.286 → 실제 +0.200, overfit 측정)
- ✅ Holdout test KR:FIN:LARGE default LGBM +0.159

### 0.5 Phase 4 — Decision Engine + Paper Trading

- ✅ Decision composer (F/T/I 가중합산 + regime-aware threshold/size scaler)
- ✅ Sector rotation overlay (±15 bonus)
- ✅ Regime classifier 5-voter (VIX level/trend, index vs SMA200, yield curve, DXY)
- ✅ Composite gates (confidence floor 0.40, cooldown 1일, threshold ±25)
- ✅ Sizer (Kelly-like × regime size_frac)
- ✅ PaperBroker + 시장가 체결 + 슬리피지 ±1bps
- ✅ 503 의사결정 (US) → 20 페이퍼 주문 (8 BUY + 12 SELL)
- ✅ default-us 잔고 변동 검증 ($100k → $100,235.87)
- ✅ decision_audit 1,006+ rows 적재
- ✅ Walk-forward backtest pipeline (rescoring engine + paper sim)
- ✅ Freshness gate 완화 (0-weight 모듈 스킵 — backtest/rescoring.py:121-127)
- ✅ Walk-forward 실제 trades 발생 검증 (KR 1 + US 4)

### 0.6 Phase 5 — UI (스캐폴드만, 라이브 검증 미실시)

- ✅ FastAPI routes 19개 (auth/admin/scan/decision/regime/news/macro/backtest/etc.)
- ✅ Frontend 14+ pages (dashboard/scan/decision_audit/macro+regime/training/freshness/login)
- ⬜ 브라우저 라이브 테스트 (미실시)
- ⬜ 종목별 3-module 점수 dashboard 검증
- ⬜ 1버튼 킬스위치 UI

### 0.7 Phase 6 — 운영 (미시작)

- ⬜ 페이퍼 트레이딩 3개월 자동화 (Phase 6 본격)
- ⬜ KIS Developers API 어댑터 (KR live)
- ⬜ Alpaca API 어댑터 (US live)
- ⬜ 단계적 실거래 (5% → 20% → 100%)

### 0.8 MLOps Infra (완료)

- ✅ Model registry + 영속화 (LGBM .txt / XGB .json / CatBoost .cbm / LSTM .pt / Ridge .joblib)
- ✅ Ensemble spec append-only registry (supersedes chain → rollback 지원)
- ✅ Drift detector (KS-test on output + rolling IC vs validated)
- ✅ Scheduler integration (mlops.retrain.weekly Sunday 06:00 UTC + drift_check.daily 07:00 UTC, both disabled until first manual)
- ✅ Multi-version models stored (data scientist가 historical 비교 가능)
- ✅ Find_best by (target, cluster, metric) — 클러스터별 자동 best 선택

### 0.9 Tests

- ✅ Unit tests 49개 (tests/unit/)
- ✅ Integration tests 1개 (tests/integration/test_strategy_api.py)
- ⬜ Regression test 자동화 (CI 부재)

### 0.10 외부 키 검증 완료

- ✅ FRED (GS10 observations OK)
- ✅ Naver Client ID + Secret (news search total=4,307,447 OK)
- ✅ DART (status=000 OK)
- ✅ BOK ECOS (기준금리 OK)
- ✅ SEC EDGAR (AAPL Assets USD OK)
- ✅ GDELT BigQuery (gcloud ADC 인증, 275GB 스캔 검증)
- ⛔ BIGKinds (유료 전환)
- ⛔ Reddit (가입 실패, 스킵)

### 0.11 Git

- ✅ `915e833` 첫 푸시 — Phase 0-4 scaffold (282 files)
- ✅ `e79c3e1` — KR FDR + EDGAR + macro + loader fixes
- ✅ `0c065b5` — WORK_LOG.md
- ✅ `dff8cc8` — Phase 3 historical Tech + LightGBM v1 first training
- ✅ `2478aaa` — EDGAR full + F historical + 섹터별 weights 학습
- ✅ `8cd6e6d` — rescoring freshness gate + news mention remap
- ✅ `303093d` — LGBM v2 122 features + XGB/CatBoost multi-trainer
- ✅ `1b2f6f1` — 데이터 폭증 + Optuna 튜닝 + MLOps 1차
- ✅ `47c609c` — MLOps infra (ensemble_spec/optimizer/drift/orchestrator)
- ✅ `feea63d` — GAPS.md 영구 산출물

### 0.12 Scripts (전수)

backend/scripts/:
- ✅ `smoke_test.py` (5-phase 부트스트랩 검증)
- ✅ `fdr_backfill_us.py` (SP500 일봉 + chunk 인서트)
- ✅ `fdr_backfill_kr.py` (KOSPI200+KOSDAQ150 marcap 프록시)
- ✅ `fdr_backfill_macro.py` (5종 매크로 FDR)
- ✅ `macro_extended_backfill.py` (FRED 10 + BOK 2 시리즈)
- ✅ `dart_backfill_kr.py` (corp_code enrich + financial facts)
- ✅ `edgar_backfill_us.py` (CIK enrich + companyconcept)
- ✅ `sec_disclosures_backfill.py` (8-K/Form 4 metadata)
- ✅ `sec_form4_parser.py` (XML body 파싱 → JSON)
- ✅ `gdelt_backfill.py` (BigQuery GKG)
- ✅ `gdelt_test.py` (연결 검증)
- ✅ `naver_news_backfill.py` (검색 API 350 종목)
- ✅ `cross_asset_backfill.py` (17 ETF FDR)
- ✅ `backfill_technical_history.py` (365일 historical 스코어링)
- ✅ `backfill_fundamental_history.py` (365일 historical)
- ✅ `remap_news_mentions.py` (NER name index 재매핑)
- ✅ `run_jobs.py` (technical/regime/decisions/info-classify/info-score/fundamental/training/walk-forward CLI)
- ✅ `verify_keys.py` (5 외부 API 키 검증)
- ✅ `train_lgbm.py` (LightGBM 단독 학습 + 클러스터별 + 메트릭 JSON)
- ✅ `train_lstm.py` (PyTorch LSTM + registry 등록)
- ✅ `smoke_lgbm.py` (20-ticker 파일럿)
- ✅ `tune_and_save.py` (Optuna + 자동 등록)
- ✅ `save_default_models.py` (default 파라미터로 학습 + 등록)
- ✅ `compare_models.py` (단일 cluster 4-model 비교)
- ✅ `compare_ensemble.py` (registry → holdout test → ensemble)
- ✅ `optimize_ensembles.py` (전체 클러스터 spec 빌드)
- ✅ `mlops_retrain.py` (retrain orchestrator + drift-only 모드)
- ⏭️ `fdr_probe_kr.py`, `debug_features.py`, `debug_regime.py` (개발 도중 디버그용, 정리됨)

### 0.13 데이터 수집 세션 (2026-06-09) — FTI 3축 갭 메우기

세션 OOM 후 DB 직접 점검 → 데이터가 문서보다 앞서 있었음. 핵심 갭(I축 historical) 해결 + 기타 보강. 상세는 WORK_LOG 2026-06-08~09.

- ✅ **Information(I) 축 historical 백필** — 신규 `scripts/backfill_information_history.py`. LLM 분류는 38.7초/기사(=247k 110일, 비현실)라 **FinBERT(전 뉴스 1.45M 기보유) 경로 신설**. module_scores I축 **345/238행 → 67,491행(788종목)**. F/T축과 동등한 시계열 확보(범위는 뉴스 7개월에 종속).
- ✅ **news mention 재매핑 효율화** — 신규 `scripts/remap_news_mentions_fast.py`. 구버전 `remap_news_mentions.py`는 티커별 정규식 재컴파일로 18h+ 미완 → 단일 사전컴파일 alternation + 24코어 병렬로 **137초**. mention **275,618 → 1,779,704**, US 종목 커버리지 288→438. (정합성 500건 mismatch=0)
- ✅ **Fundamental 갭** — 우선주 7개 보통주 재무 상속(+3,292), BRKB/BFB CIK보정+EDGAR(+3,468). F 커버리지 KR 326→333, US 501→503. 잔여(KR 17 SPAC/신규상장/외국주, US 14 ETF)는 무료 소스에 데이터 없음 = 하드리밋.
- ✅ **Options**(A.9) — `yfinance_options_ingest.py`로 전 US 513종목 현재 스냅샷. **과거 옵션체인은 무료 불가 → forward 누적만**.
- ⛔ **Google Trends / USPTO Patents**(A.6/A.7 영역) — ⬜에서 ⛔로 정정. pytrends Google 429 차단, PatentsView 레거시 엔드포인트 폐기(API키 필요). 무료 수집 불가 실측.
- ⏸️ **13F holdings** — 보유내역 파싱+OpenFIGI CUSIP매핑 신규코드 필요, feature 2개 저ROI → user 승인 하에 보류.
- ⏸️ **10년 뉴스 백필** — 저장공간(~5TB) 확보 후 진행(user 결정). GDELT=BigQuery 5.5TB(무료 월1TB), KR뉴스=무료로 막힘. → "보류 결정" 섹션 + memory `project_news_history_plan` 참조.

### 0.14 피처엔지니어링 면밀검토 + 버그수정 (2026-06-09~10)

샘플 매트릭스 실측 결과 무신호 US 43/KR 76 → 검토 후 핵심 버그/얕음 해소. 상세 WORK_LOG 2026-06-09~10.

- ✅ **10년 F/T 모듈점수 백필** — `backfill_ft_history_parallel.py`(20워커, OOM-free). T KR **113,858→710,202**(10년), T US 209k, F US 1.06M, F KR 392k.
- ✅ **버그: KR daily_prices.as_of_ts 미래스탬프** 정정(trade_date+16h) → KR 기술백필 historical 가능해짐.
- ✅ **버그: regime 피처 dead**(market_regime NEUTRAL 6행+라벨불일치) → `backfill_regime_history.py`로 HMM 5-state 882일 영속화 + load_regime_features 소문자매핑 + one-hot 3추가. regime_conf/risk_on/risk_off 부활.
- ✅ **버그: pb dead**(SHARES_OUTSTANDING 미추출) → NET_INCOME/EPS로 shares 유도, pb 100% 부활.
- ✅ **매크로 심화 18피처**(범주B, raw 57시리즈 중 13→활용 확대): TIPS/breakeven/HY신용/copper/WTI/natgas/yield곡률/FX/VIX-percentile/VRP/funding-stress 전부 100% non-null. `ALL_FEATURE_COLS` 442→462.
- ✅ cross-section 학습경로 편입 확인(train_lgbm+cache 둘다).
- ⚪ 잔여 구조적결손: 현금흐름 concept 미추출(재백필 필요), insider-v2, 죽은 alt-data(trends/reddit/patents/13F 빈테이블), KR 구조부재(Form-4/FINRA/EDGAR/GDELT 무료등가물 없음).

---

## A. 데이터 / 피처

### A.1 Technical / Price (현재 ~50, 가능 200+)

**Returns**:
- ✅ ret_1d, 2d, 3d, 5d, 10d, 21d, 42d, 63d, 126d, 252d
- ⬜ ret_7d, 14d, 30d, 60d, 90d, 180d, 504d
- ⬜ 비표준 윈도우 (22d US 거래일, 20d KR)
- ⬜ Returns adjusted for ex-dividend (currently use adj_close)

**Volatility**:
- ✅ vol_5d, 21d, 63d, 252d (realized)
- ⬜ vol_10d, 30d, 60d, 90d, 126d
- ⬜ Volatility ratios (5d/63d, 21d/252d)
- ⬜ GARCH(1,1) conditional vol
- ⬜ EWMA vol (λ=0.94)
- ⬜ **Garman-Klass** vol (OHLC 활용)
- ⬜ **Parkinson** vol
- ⬜ **Rogers-Satchell** vol
- ⬜ **Yang-Zhang** vol (overnight + intraday)
- ⬜ Vol clustering measure (ARCH effect)
- ⬜ Vol skew (left vs right tail)
- ⬜ Vol-of-vol

**Drawdown / Trend**:
- ✅ dd_from_high_63d, dd_from_high_252d
- ⬜ Days since 52-week high/low
- ⬜ Days in current trend
- ⬜ Trend strength (R² of price vs linear regression)
- ⬜ Hurst exponent (mean-reverting vs trending)
- ⬜ Detrended Price Oscillator

**Volume**:
- ✅ volume_z21, volume_z63, obv_slope21
- ⬜ volume_z 5d, 252d
- ⬜ Volume trend slope
- ⬜ Accumulation/Distribution Line slope
- ⬜ Force Index (Elder)
- ⬜ Ease of Movement (Arms)
- ⬜ Chaikin Oscillator
- ⬜ Klinger Volume Oscillator
- ⬜ Volume Price Trend (VPT)
- ⬜ Negative Volume Index (NVI), Positive (PVI)
- ⬜ Volume Profile (Point of Control)
- ⬜ VWAP deviation
- ⬜ Dollar volume z-score
- ⬜ Volume regime (z relative to ticker history)

**Momentum/Oscillators**:
- ✅ RSI 5/14, MACD-hist/signal, ADX 14, Stoch K 14, Williams %R 14, MFI 14, CMF 21, Aroon up/dn/osc, ROC 10/21
- ⬜ RSI 28, 50
- ⬜ Stoch D (slow %D)
- ⬜ Stoch RSI
- ⬜ Connor's RSI (3-period composite)
- ⬜ Ultimate Oscillator
- ⬜ TRIX (triple smoothed)
- ⬜ KST (Know Sure Thing)
- ⬜ DPO (Detrended Price Oscillator)
- ⬜ TSI (True Strength Index)
- ⬜ PPO (Percentage Price Oscillator)
- ⬜ PVO (Percentage Volume Oscillator)
- ⬜ BOP (Balance of Power)
- ⬜ Chande Momentum Oscillator
- ⬜ DMI Plus / Minus separately
- ⬜ Vortex Indicator (VI+/VI-)
- ⬜ Mass Index
- ⬜ Coppock Curve
- ⬜ Fractal Adaptive Moving Average

**Trend / Moving Averages**:
- ✅ vs SMA 20/50/200, SMA50_above_SMA200
- ⬜ vs SMA 10, 100, 400
- ⬜ EMA 12, 26, 50, 200
- ⬜ DEMA, TEMA, TRIMA, KAMA, MAMA, T3
- ⬜ WMA, Hull MA (HMA), ALMA, McGinley, ZeroLag EMA, VIDYA
- ⬜ Linear Regression Line / slope
- ⬜ MA crossover signals (golden/death cross flags)
- ⬜ Distance from MA in ATR units
- ⬜ Parabolic SAR with current direction
- ⬜ SuperTrend

**Cycle / Hilbert**:
- ⬜ Hilbert HT_DCPERIOD, HT_DCPHASE, HT_PHASOR, HT_SINE, HT_TRENDMODE
- ⬜ Schaff Trend Cycle
- ⬜ Ehlers Cycle indicators

**Volatility Bands**:
- ✅ Bollinger %B + squeeze
- ⬜ Bollinger width trend
- ⬜ Keltner Channels position
- ⬜ Donchian channel width

**Candlestick Patterns** (TA-Lib has 61, 우리 4개):
- ✅ doji, body_pct, upper/lower_wick_pct
- ⬜ Engulfing (bullish/bearish)
- ⬜ Harami / Harami Cross
- ⬜ Hammer / Hanging Man / Inverted Hammer
- ⬜ Shooting Star / Morning Star / Evening Star
- ⬜ Three White Soldiers / Three Black Crows
- ⬜ Dark Cloud Cover / Piercing Pattern
- ⬜ Marubozu (long/short, white/black)
- ⬜ Spinning Top
- ⬜ Three Inside Up/Down, Three Outside Up/Down
- ⬜ Belt Hold / Counterattack / Tasuki Gap
- ⬜ Mat Hold / Concealing Baby Swallow
- ⬜ Stick Sandwich, Stalled Pattern
- ⬜ (TA-Lib 60+ 패턴 모두)

**Statistical**:
- ✅ skew_21d, kurt_21d, sharpe_21d/63d
- ⬜ Skew / Kurt at 63d, 126d, 252d
- ⬜ Sharpe at 5d, 10d, 252d
- ⬜ Sortino (downside vol)
- ⬜ Calmar
- ⬜ Information ratio
- ⬜ Treynor (need beta)
- ⬜ **Rolling Beta** to market (5d, 21d, 63d, 252d)
- ⬜ Alpha (CAPM residual)
- ⬜ Tracking error vs benchmark
- ⬜ Active return
- ⬜ Pearson/Spearman corr with market
- ⬜ Autocorrelation (1, 5, 21 lags)
- ⬜ VaR(5%), VaR(1%), CVaR
- ⬜ Drawdown duration & recovery time
- ⬜ Pain Index

### A.2 Fundamental (현재 ~10, 가능 50+)

**Valuation**:
- ✅ pe_ttm, pb, ev_ebitda
- ⬜ P/S (price/sales)
- ⬜ P/CF (price/cash flow)
- ⬜ EV/Revenue, EV/FCF
- ⬜ PEG (P/E to growth)
- ⬜ Dividend Yield, Earnings Yield (1/PE), FCF Yield
- ⬜ Shareholder Yield (div + buybacks)
- ⬜ Forward P/E (if analyst data available)

**Profitability**:
- ✅ roe_q, roa_q, gross_margin
- ⬜ ROIC (Return on Invested Capital)
- ⬜ ROCE (Return on Capital Employed)
- ⬜ Operating Margin, Net Margin, EBITDA Margin, FCF Margin
- ⬜ Gross Profitability (Novy-Marx)

**Growth**:
- ✅ rev_yoy, eps_yoy
- ⬜ Revenue QoQ, EPS QoQ
- ⬜ Revenue CAGR (3yr, 5yr), EPS CAGR
- ⬜ Margin expansion
- ⬜ Revenue / EPS growth acceleration
- ⬜ Book value growth
- ⬜ Dividend growth rate

**Quality / Balance Sheet**:
- ✅ current_ratio, debt_equity
- ⬜ Quick Ratio, Cash Ratio
- ⬜ Total Debt/Total Capital
- ⬜ Debt/EBITDA
- ⬜ Interest Coverage, Times Interest Earned
- ⬜ Working Capital / Sales
- ⬜ Asset Turnover, Inventory Turnover, Receivables Turnover
- ⬜ Days Sales/Inventory/Payable Outstanding
- ⬜ Cash Conversion Cycle (CCC)
- ⬜ Goodwill / Total Assets

**Cash Flow Quality**:
- ⬜ CFO/Net Income (Sloan ratio)
- ⬜ FCF / Net Income
- ⬜ Capex / Revenue, Capex / Depreciation
- ⬜ FCF stability (3yr std)

**Composite Scores**:
- ⬜ **Piotroski F-Score** (9 binary bits)
- ⬜ **Altman Z-Score** (부도위험)
- ⬜ **Beneish M-Score** (이익조작 탐지)
- ⬜ Magic Formula (Greenblatt)
- ⬜ Mohanram G-Score

**Sector-relative percentile**:
- ⬜ 위 모든 메트릭의 sector 백분위 rank (현재 composite 하나만)

**Earnings timing**:
- ⬜ Days since earnings
- ⬜ Days to next earnings
- ⬜ Earnings surprise (actual vs consensus or trailing 4Q)

### A.3 Information / News (현재 ~6, 가능 30+)

- ✅ news_count_7d/30d, sentiment_7d, pos/neg counts, impact
- ⬜ News count: 1d, 14d, 60d, 90d, 180d
- ⬜ Sentiment: 1d, 14d, 30d, 90d
- ⬜ Sentiment momentum (delta vs 30d)
- ⬜ Sentiment volatility
- ⬜ News count 백분위 (해당 ticker 자체 historical)
- ⬜ Publisher diversity (Shannon entropy)
- ⬜ Publisher trust weighted score
- ⬜ Topic-specific counts (M&A, earnings, lawsuit, dividend, layoff)
- ⬜ Article length distribution
- ⬜ **GDELT V2Tone direct extraction** (현재 summary에만 저장!)
- ⬜ **GDELT V2Themes** (M&A/earnings/lawsuit 코드 직접)
- ⬜ GDELT V2Locations
- ⬜ GDELT V2Persons (CEO 언급)
- ⬜ GDELT V2Organizations co-mention
- ⬜ Source country diversity
- ⬜ LLM classification (1.4M GDELT 미분류, sample-based)
- ⬜ Co-mention network (A-B 동시 언급)
- ⬜ Mention timing (pre-market vs after-hours)
- ⬜ Weekend news count
- ⬜ Mention rank vs sector peers

### A.4 Disclosure (현재 ~13, 가능 30+)

- ✅ insider count 7d/30d, 8K count 7d/30d, days since 10K/Q
- ✅ Form 4: net_value, buys/sells, CEO/Director, buy_sell_ratio
- ⬜ **8-K item codes** (1.01 M&A, 2.02 earnings, 4.01 auditor, 5.02 exec, 7.01 Reg FD, 8.01 other) — 본문 fetch 필요
- ⬜ 10-K Item 1A Risk Factor sentiment/length
- ⬜ 10-K Item 7 (MD&A) length/sentiment
- ⬜ 10-Q forward-looking statements count
- ⬜ 10-K word complexity (Flesch reading ease, FOG index)
- ⬜ Auditor change events
- ⬜ Going concern qualifications
- ⬜ Restatement events
- ⬜ Form 4 patterns: 3-day cluster of buys
- ⬜ **13F holdings changes** (institutional flow, 분기마다 free)
- ⬜ **13D/13G** (5%+ activist holder)
- ⬜ Schedule 14A (proxy)
- ⬜ Form 144 (planned insider sales)
- ⬜ Form S-3/S-4/S-8 (registration)
- ⬜ DART 주요사항보고 (M&A, 합병/분할)
- ⬜ DART 임원·주요주주 변경 (KR insider)
- ⬜ DART 단일판매·공급계약 (대형 수주)
- ⬜ DART 합병/분할
- ⬜ **KRX 공매도 잔고** (KR-specific)
- ⬜ **KRX 외국인 보유 한도 비율**
- ⬜ KIND 공시 (한국거래소)
- ⬜ 한국 신용평가 (NICE, KIS)

### A.5 Macro (현재 18, 가능 50+)

**FRED 추가**:
- ✅ 10Y, 2Y, 3M, FEDFUNDS, CPI, M2, UE, IP, Payroll, Retail
- ⬜ **Initial Jobless Claims** (weekly leading)
- ⬜ Continued Claims
- ⬜ **ISM Manufacturing PMI**
- ⬜ **ISM Services PMI**
- ⬜ ISM New Orders
- ⬜ Manufacturers' New Orders
- ⬜ Capacity Utilization
- ⬜ Housing Starts, Building Permits
- ⬜ Existing Home Sales
- ⬜ Case-Shiller Home Price Index
- ⬜ **UMich Consumer Sentiment**
- ⬜ Conference Board Consumer Confidence
- ⬜ **PPI (Producer Price Index)**
- ⬜ **Core PCE (Fed 선호)**
- ⬜ Personal Income, PCE, Real Disposable Personal Income
- ⬜ Trade Balance, Current Account
- ⬜ Federal Debt
- ⬜ Treasury yields: 1M, 6M, 1Y, 5Y, 7Y, 20Y, 30Y
- ⬜ **TIPS yields** (real rates)
- ⬜ **Breakeven inflation** (TIPS spread)
- ⬜ Term premium
- ⬜ Effective Fed Funds Rate
- ⬜ Bank Prime Rate, SOFR

**BOK ECOS 추가**:
- ✅ 기준금리, CPI
- ⬜ KR M2 (item code 수정 필요)
- ⬜ **KR PMI** (HSBC)
- ⬜ KR Consumer Sentiment
- ⬜ KR Industrial Production
- ⬜ **KR Exports** (수출 잠정치 — leading)
- ⬜ KR Imports
- ⬜ KR Trade Balance
- ⬜ KR FX Reserves
- ⬜ KR CD Rate
- ⬜ KR Gov Bond Yields (1Y, 3Y, 5Y, 10Y)
- ⬜ KR PPI
- ⬜ KR Housing Price Index
- ⬜ KR Construction Index

**Commodities** (FRED + FDR):
- ⬜ WTI Crude, Brent Crude
- ⬜ Natural Gas
- ⬜ Silver, Copper, Steel, Coal
- ⬜ Wheat, Corn, Soybeans
- ⬜ Coffee, Sugar
- ⬜ Baltic Dry Index (shipping)

### A.6 Cross-asset (현재 18, 가능 50+)

**FX**:
- ✅ DXY
- ⬜ USD/KRW (핵심 — KR 수출주 영향)
- ⬜ USD/JPY (carry trade)
- ⬜ USD/CNY (KR 무역 영향)
- ⬜ EUR/USD
- ⬜ USD/CHF (safe haven)
- ⬜ USD/AUD (commodity FX)
- ⬜ USD EM basket

**Bond/Credit**:
- ✅ TLT
- ⬜ HYG (high yield)
- ⬜ LQD (investment grade)
- ⬜ HYG/LQD ratio (credit spread)
- ⬜ IEF (7-10Y), SHY (1-3Y), BIL (1-3M)

**Volatility**:
- ✅ VIX
- ⬜ **VVIX** (vol of vol)
- ⬜ VIX9D, VIX3M, VIX6M (term structure)
- ⬜ **MOVE** (bond vol)
- ⬜ OVX (oil vol)
- ⬜ GVZ (gold vol)
- ⬜ **CBOE SKEW**
- ⬜ VXFXI (China vol)
- ⬜ Put/Call Ratio
- ⬜ Realized vs Implied vol spread

**Commodity ETFs**:
- ✅ GLD, USO, TLT
- ⬜ UNG (nat gas), DBC (broad commodities), DBA (agriculture)
- ⬜ SLV (silver), CPER (copper)
- ⬜ Gold/Silver ratio, Copper/Gold ratio

**International**:
- ⬜ EEM, VWO (EM)
- ⬜ FXI (China)
- ⬜ EWJ (Japan)
- ⬜ EFA (developed ex-US)
- ⬜ INDA (India), EWZ (Brazil)

**Sector specifics**:
- ✅ XLK/XLF/XLV/XLE/XLY/XLP/XLI/XLB/XLU/XLRE/XLC + SPY/QQQ/IWM
- ⬜ SOXX (semiconductors)
- ⬜ IBB (biotech)
- ⬜ KRE (regional banks)
- ⬜ XHB (homebuilders)
- ⬜ JETS (airlines)
- ⬜ GDX (gold miners)

**Crypto** (sentiment proxy):
- ⬜ BTC price
- ⬜ ETH price

**Sector rotation features**:
- ⬜ XLU/XLI (defensive vs cyclical)
- ⬜ TLT/SPY (bond vs equity)

### A.7 Alternative Data (현재 0, 가능 15+)

**Free / no key**:
- ⬜ **Wikipedia pageviews API** (retail attention proxy)
- ⬜ **Google Trends** via pytrends (search interest)
- ⬜ **Reddit** r/wallstreetbets, r/stocks (PRAW free)
- ⬜ **GitHub** commit activity (tech 종목 R&D proxy)
- ⬜ **USPTO Patent filings** (R&D output)
- ⬜ Marine Traffic AIS (shipping, free tier)
- ⬜ Job postings (LinkedIn/Indeed scraping)
- ⬜ App store rankings (consumer apps)

**With existing keys**:
- ⬜ Naver DataLab 검색어 트렌드 (API 키 있음)

### A.8 Microstructure (Daily Proxies)

- ⬜ Daily range / ATR ratio
- ⬜ Opening gap %
- ⬜ Closing range % (close vs high-low range)
- ⬜ Number of distinct closes in 21d (price clustering)
- ⬜ Volume-weighted price changes
- ⬜ Effective spread proxy (CRSP-style estimator)
- ⬜ Bid-ask spread time series (if available)
- ⬜ Order book imbalance (if intraday data)
- ⬜ VPIN (Volume-synchronized Probability of Informed Trading)

### A.9 Options-derived (CBOE 일부 무료)

- ⬜ Implied volatility ATM/OTM (need source)
- ⬜ Implied skew (put 비대칭)
- ⬜ Put/Call open interest ratio
- ⬜ Options volume / stock volume
- ⬜ IV rank (현재 IV 백분위)
- ⬜ IV term structure
- ⬜ Gamma exposure (dealer positioning)

### A.10 Calendar (현재 9, 가능 25+)

- ✅ dow, dom, doq, doy, month, quarter, days_to_q_end, is_jan, is_dec
- ⬜ **FOMC meeting day flag**
- ⬜ **CPI release day flag**
- ⬜ **NFP (jobs) report day flag**
- ⬜ **PCE release day flag**
- ⬜ **GDP release day flag**
- ⬜ **FOMC minutes day flag**
- ⬜ Earnings announcement day (per-ticker)
- ⬜ Days since/to next earnings
- ⬜ Triple witching (3rd Fri Mar/Jun/Sep/Dec)
- ⬜ Options expiration (3rd Fri monthly)
- ⬜ Russell rebalance window
- ⬜ Tax loss harvest (Nov-Dec)
- ⬜ Tax day (Apr 15)
- ⬜ Halloween / Santa rally
- ⬜ Chinese New Year window
- ⬜ Korean Chuseok / Seollal
- ⬜ US election year
- ⬜ Korea presidential election

### A.11 Regime (현재 3, 가능 20+)

- ✅ regime_risk_on/off/conf (5-voter classifier)
- ⬜ **HMM (Hidden Markov Model)** 잠재 regime
- ⬜ 5/7/9-state regime
- ⬜ Days since regime change
- ⬜ Regime transition velocity
- ⬜ Sector-specific regimes
- ⬜ Cross-market regime divergence (KR vs US)
- ⬜ Vol regime (low/normal/high)
- ⬜ Trend regime (up/sideways/down)
- ⬜ Liquidity regime (tight/normal/wide spread)
- ⬜ Macro regime (expansion/peak/contraction/trough)
- ⬜ Risk-on/off intensity (continuous, not categorical)

### A.12 Network Effects

- ⬜ Mention graph centrality
- ⬜ Lead-lag with sector ETF
- ⬜ Lead-lag with most-correlated peer
- ⬜ Sector momentum spillover
- ⬜ Supply chain network position
- ⬜ Customer concentration (if known)

### A.13 Market Structure

- ⬜ Short interest ratio
- ⬜ Days to cover
- ⬜ Float / total shares
- ⬜ Insider ownership %
- ⬜ Institutional ownership %

### A.14 Text/NLP Heavy

- ⬜ Earnings call transcripts (Seeking Alpha, when available)
- ⬜ 10-K language complexity (Flesch reading ease, FOG)
- ⬜ 10-K Risk Factor section sentiment
- ⬜ 10-K MD&A section
- ⬜ Forward-looking statement counts
- ⬜ Litigation mention counts
- ⬜ Restatement detection
- ⬜ Auditor going-concern language
- ⬜ CEO letter sentiment
- ⬜ Press release tone shift

---

## B. 모델 / 방법론

### B.1 Tree-based (현재 LGBM/XGB/CatBoost)

- ✅ LightGBM (default + Optuna)
- ✅ XGBoost
- ✅ CatBoost
- ⬜ HistGradientBoosting (sklearn)
- ⬜ ExtraTreesRegressor
- ⬜ Random Forest
- ⬜ AdaBoost
- ⬜ ExplainableBoostingMachine (EBM)
- ⬜ **NGBoost** (probabilistic predictions)
- ⬜ **TabNet** (deep tabular)
- ⬜ SAINT (self-attention tabular)
- ⬜ FT-Transformer (tabular transformer)
- ⬜ TabPFN (foundation model for tabular)

### B.2 Linear / Bayesian

- ✅ Ridge (구현됨, 버그)
- ⬜ Lasso, ElasticNet, LARS
- ⬜ OMP (Orthogonal Matching Pursuit)
- ⬜ Bayesian Ridge
- ⬜ ARD (Automatic Relevance Determination)
- ⬜ Spike-and-Slab
- ⬜ Quantile Regression
- ⬜ Huber Regression (robust)
- ⬜ RANSAC
- ⬜ Theil-Sen (robust)

### B.3 Sequence / Neural

- ✅ LSTM (default)
- ⬜ LSTM Optuna tuning
- ⬜ GRU
- ⬜ BiLSTM
- ⬜ Transformer encoder
- ⬜ **TFT (Temporal Fusion Transformer)** — Google
- ⬜ **N-BEATS / N-HiTS** (시계열 SOTA)
- ⬜ **PatchTST** (Patch-based transformer 2023)
- ⬜ **iTransformer** (2024)
- ⬜ TimesNet, Crossformer
- ⬜ DLinear / NLinear (simple but strong)
- ⬜ **Chronos** (Amazon foundation model 2024)
- ⬜ **TimesFM** (Google foundation 2024)
- ⬜ **Lag-Llama** (Lag-based foundation)
- ⬜ TabTransformer
- ⬜ WaveNet (dilated conv)
- ⬜ TCN (Temporal Convolutional Network)

### B.4 Bayesian / Probabilistic

- ⬜ Gaussian Processes (소규모 + 불확실성)
- ⬜ Bayesian Neural Networks (variational)
- ⬜ Deep Kernel Learning
- ⬜ Bayesian Model Averaging
- ⬜ MC Dropout (cheap BNN approx)

### B.5 Graph

- ⬜ GCN (Graph Convolutional)
- ⬜ GAT (Graph Attention)
- ⬜ GraphSAGE
- ⬜ Knowledge Graph embeddings (TransE, RotatE)
- ⬜ Hyperbolic embeddings (계층)

### B.6 Signal Decomposition

- ⬜ Wavelet transform (Daubechies, Haar)
- ⬜ EMD (Empirical Mode Decomposition)
- ⬜ SSA (Singular Spectrum Analysis)
- ⬜ Fourier / FFT
- ⬜ TDA (Topological Data Analysis) — persistence diagrams

### B.7 Self-supervised / Pretraining

- ⬜ Contrastive learning (SimCLR-style for time series)
- ⬜ Masked Autoencoder (MAE-style)
- ⬜ TS2Vec
- ⬜ Time-series BERT pre-training

### B.8 Generative / Anomaly

- ⬜ VAE (Variational Autoencoder)
- ⬜ GAN (for synthetic augmentation)
- ⬜ Diffusion models (DDPM for time series 2024)
- ⬜ Flow-based models
- ⬜ Isolation Forest (anomaly)
- ⬜ Local Outlier Factor (LOF)
- ⬜ One-Class SVM
- ⬜ Autoencoder reconstruction error

### B.9 Meta-learning / AutoML

- ⬜ MAML (Model Agnostic Meta-Learning)
- ⬜ AutoGluon (auto ensemble)
- ⬜ FLAML (Microsoft auto ML)
- ⬜ TPOT (Genetic programming)
- ⬜ Auto-sklearn
- ⬜ Neural Architecture Search (NAS)

### B.10 Causal Inference

- ⬜ **Causal Forests** (econML, Athey 2019)
- ⬜ DoubleML
- ⬜ TMLE (Targeted Maximum Likelihood)
- ⬜ DiD (Difference-in-Differences)
- ⬜ IV (Instrumental Variables)
- ⬜ Synthetic Control

### B.11 Reinforcement Learning

- ⬜ DQN (discrete actions)
- ⬜ PPO (proximal policy)
- ⬜ A2C
- ⬜ SAC (continuous)
- ⬜ Decision Transformer (offline RL)
- ⬜ Multi-armed bandit (Thompson sampling)
- ⬜ Contextual bandits

### B.12 Finance-specific Foundation Models

- ⬜ **FinBERT** (free, 뉴스 임베딩)
- ⬜ **FinGPT** (open source, BloombergGPT 대안)
- ⬜ Sentence Transformers — all-MiniLM-L6-v2, all-mpnet-base-v2, multi-qa-mpnet-base, paraphrase-multilingual-mpnet (KR/EN 다국어)

### B.13 Stacking / Meta-ensemble

- ⬜ Stacking meta-learner (LinearRegression on OOF preds)
- ⬜ Blending (holdout-based)
- ⬜ Voting (rank-based)
- ⬜ Bayesian Model Averaging

---

## C. 검증 (현재 매우 부족)

- ✅ TimeSeriesSplit (date-grouped, embargo 21d)
- ⬜ **Combinatorial Purged CV** (López de Prado, 정통)
- ⬜ Walk-forward expanding window
- ⬜ Walk-forward rolling window
- ⬜ Triple-barrier method (López de Prado)
- ⬜ Meta-labeling
- ⬜ Cross-validation with embargo (multi-window)
- ⬜ **Deflated Sharpe Ratio** (multiple test correction)
- ⬜ **Probability of Backtest Overfitting (PBO)**
- ⬜ White's Reality Check
- ⬜ SPA test (Superior Predictive Ability)
- ⬜ Stepdown / Romano-Wolf
- ⬜ BIC / AIC for model selection
- ⬜ **Bootstrap confidence intervals** (IC, Sharpe)
- ⬜ Block bootstrap (autocorrelation 반영)
- ⬜ Stationary bootstrap
- ⬜ Information leakage detection

---

## D. 포트폴리오 구성

- ✅ Equal weight (current paper broker)
- ⬜ Mean-Variance Optimization (Markowitz)
- ⬜ **Black-Litterman**
- ⬜ **Hierarchical Risk Parity (HRP)** — López de Prado
- ⬜ Risk Parity
- ⬜ Maximum Diversification
- ⬜ Equal Risk Contribution
- ⬜ Most-Diversified Portfolio
- ⬜ Minimum Correlation
- ⬜ Inverse Volatility weighting
- ⬜ **CVaR-optimal**
- ⬜ Robust optimization (Tütüncü)
- ⬜ Resampled efficient frontier
- ⬜ Mean-CVaR
- ⬜ Mean-Drawdown optimization
- ⬜ Tail-risk parity
- ⬜ Volatility targeting overlay
- ⬜ Beta-neutral construction
- ⬜ Factor-neutral construction

---

## E. 실행 / Execution

- ✅ Market order via PaperBroker
- ⬜ Limit order
- ⬜ TWAP / VWAP execution
- ⬜ **Implementation Shortfall** (Almgren-Chriss)
- ⬜ Volume Participation Rate
- ⬜ Iceberg orders
- ⬜ Smart Order Routing
- ⬜ Adaptive slippage estimation
- ⬜ Bid-ask spread cost model
- ⬜ Market impact model (linear / sqrt)
- ⬜ Latency model
- ⬜ Order book pressure
- ⬜ Time-of-day execution profiles

---

## F. 리스크 관리

- ✅ Position size limit, daily loss, consecutive loss, max positions, allowlist
- ⬜ **Sector exposure cap**
- ⬜ Industry concentration limit
- ⬜ Beta exposure cap
- ⬜ **Vol-adjusted position sizing**
- ⬜ **Kelly Criterion** (full / fractional)
- ⬜ VaR limit
- ⬜ CVaR limit
- ⬜ Drawdown circuit breaker
- ⬜ Stress test triggers
- ⬜ Correlation risk (cluster-based)
- ⬜ Liquidity-adjusted sizing
- ⬜ Slippage budget
- ⬜ Tail hedging (long puts)
- ⬜ Pair trading risk overlay
- ⬜ Conditional drawdown control

---

## G. 거래비용 모델

- ⬜ KR 거래세 0.18% (KOSPI), 0.18% (KOSDAQ)
- ⬜ KR 거래수수료 (broker별 다름, 평균 0.015%)
- ⬜ 미국 SEC fee + FINRA fee
- ⬜ 미국 commission (보통 0, Alpaca 0)
- ⬜ Slippage model (linear / sqrt by volume)
- ⬜ Spread cost (bid-ask)
- ⬜ Borrow cost for shorts
- ⬜ FX conversion cost (KR <-> US)
- ⬜ Margin interest
- ⬜ Tax (KR 양도소득세 — 대주주만)
- ⬜ Wash sale rules (US)

---

## H. MLOps 인프라

- ✅ Model registry (var/models/runs/...)
- ✅ Ensemble spec (var/models/ensembles.json)
- ✅ Drift detector (KS-test + rolling IC)
- ✅ Scheduler integration (mlops.retrain.weekly, drift_check.daily)
- ⬜ Feature Store (Feast or custom)
- ⬜ Online inference service (FastAPI + Redis cache)
- ⬜ Prometheus metrics export
- ⬜ Grafana dashboards
- ⬜ Real-time data streaming (Kafka / Redis Streams)
- ⬜ Event sourcing (audit log)
- ⬜ A/B testing framework
- ⬜ Shadow mode (paper alongside live)
- ⬜ Champion-Challenger
- ⬜ Multi-armed bandit for live selection
- ⬜ Canary deployment
- ⬜ Rollback automation
- ⬜ Health checks
- ⬜ SLI/SLO monitoring
- ⬜ Alerting (Slack, email)
- ⬜ Model card documentation
- ⬜ Datasheets for datasets
- ⬜ Lineage tracking
- ⬜ Experiment tracking (**MLflow**)
- ⬜ Hyperparameter tracking
- ⬜ Confusion matrix / calibration plots
- ⬜ **SHAP** explanations
- ⬜ LIME explanations
- ⬜ **Permutation importance**
- ⬜ Partial dependence plots
- ⬜ Counterfactual explanations
- ⬜ Feature importance stability tracking
- ⬜ **Prediction interval / uncertainty quantification**
- ⬜ **Conformal prediction**

---

## I. 모니터링 / 운영

- ⬜ Real-time P&L
- ⬜ Daily P&L attribution
- ⬜ Sector P&L attribution
- ⬜ Model P&L attribution
- ⬜ Factor attribution (Fama-French 5)
- ⬜ Risk dashboard
- ⬜ Position dashboard
- ⬜ Trade log
- ⬜ Order rejection log
- ⬜ Slippage tracking
- ⬜ Spread tracking
- ⬜ Latency tracking
- ⬜ Feature drift heatmap
- ⬜ Concept drift detection
- ⬜ Performance decay tracking
- ⬜ Anomaly alerts
- ⬜ Risk limit breaches
- ⬜ Model performance reports (daily/weekly/monthly)
- ⬜ Tax accounting
- ⬜ Compliance reports
- ⬜ Operations runbook
- ⬜ Disaster recovery procedures

---

## J. Walk-forward Backtesting / Simulation

- ✅ Historical replay (basic, rescoring_runner.py)
- ⬜ Walk-forward backtest engine (proper)
- ⬜ Monte Carlo path simulation
- ⬜ Bootstrap backtest
- ⬜ Block bootstrap
- ⬜ Stationary bootstrap
- ⬜ Stress scenarios (2008, 2020, 2022 COVID, China property)
- ⬜ Worst-case scenarios
- ⬜ Latency simulation
- ⬜ Slippage simulation
- ⬜ Liquidity-limited execution simulation
- ⬜ Multi-asset portfolio simulation
- ⬜ Realistic costs (fees, taxes, borrow rate for shorts)
- ⬜ Cash account simulation
- ⬜ Margin simulation
- ⬜ Multi-currency support
- ⬜ Time zone handling
- ⬜ Holiday calendar awareness (KR + US)
- ⬜ Half-day trading
- ⬜ Multiple brokers simulation
- ⬜ Position reconciliation
- ⬜ Reporting (PnL, Sharpe, drawdown, MDD recovery)

---

## K. 클러스터 커버리지 (현재 ~10 / 22)

**A등급 production 후보** (이미 등록):
- ✅ US:ENERGY:LARGE (tuned LGBM IC +0.353)
- ✅ US:FIN:LARGE (default CatBoost +0.230)
- ✅ KR:FIN:LARGE (default CatBoost +0.262)
- ✅ US:UTILITIES:LARGE (등록 중)

**B등급 검토 중**:
- ✅ KR:OTHER:SMALL (default CatBoost +0.209)
- ✅ KR:OTHER:LARGE (tuned CatBoost +0.091, hit 69%)
- ✅ KR:FIN:MID (default CatBoost +0.103)
- ⬜ KR:FIN:SMALL (tuning 진행 중)

**아직 미처리**:
- ⬜ US:TECH:LARGE (default 저장 중)
- ⬜ US:INDUSTRIALS:LARGE (default 저장 중)
- ⬜ US:HEALTH:LARGE
- ⬜ US:CONS_DISC:LARGE
- ⬜ US:CONS_STAPLES:LARGE (음수 IC — ignore 후보)
- ⬜ US:MATERIALS:LARGE
- ⬜ US:REAL_ESTATE:LARGE
- ⬜ US:COMM:LARGE
- ⬜ US:REAL_ESTATE:MID
- ⬜ US:COMM:MID
- ⬜ US:FIN:MID
- ⬜ US:INDUSTRIALS:MID
- ⬜ US:CONS_STAPLES:MID

---

## L. 다중 horizon target (현재 21d 단일)

- ✅ ret_fwd_21d, rank_fwd_21d (현재 학습 중)
- ⬜ ret_fwd_5d 학습 + 등록
- ⬜ ret_fwd_63d 학습 + 등록
- ⬜ vol_adj_ret_21d (위험조정)
- ⬜ Multi-horizon signal combiner (방향 합치 가중)

---

## M. KR/US 통합 시도

- ⬜ Cross-market lead-lag (US 어제 → KR 오늘)
- ⬜ Cointegration pairs (삼성전자 vs 마이크론)
- ⬜ Currency-adjusted relative momentum
- ⬜ Cross-listed ADR analysis (KR ADR)

---

## N. 외부 데이터 — 미발급 키 / 차단

- ⛔ BIGKinds (유료 전환)
- ⛔ Reddit API (가입 실패)
- ⬜ Bloomberg Terminal (유료, 보류)
- ⬜ Refinitiv (유료)
- ⬜ FactSet (유료)
- ⬜ Seeking Alpha PRO (유료 transcript)
- ⬜ Trefis (유료 estimates)

---

## 우선순위 / 처리 순서

**Wave 1 — 즉시 시작 (무료, 기존 데이터 활용)**:
1. GDELT V2Tone/V2Themes 직접 추출 (이미 DB 있음)
2. Wikipedia pageviews, Google Trends, Naver DataLab
3. Piotroski / Altman / Beneish 구현
4. 8-K item code 본문 fetch + 파싱
5. FRED 추가 시리즈 일괄 추가
6. Microstructure daily proxies
7. Calendar 이벤트 flags
8. HMM regime detector
9. Multi-horizon labels (5d/21d/63d)
10. pandas-ta 100+ 미사용 지표
11. TA-Lib candlestick 60+ patterns
12. Fundamental sector-relative percentile
13. Statistical features (rolling beta, etc.)

**Wave 2 — 신모델 + 검증 인프라**:
14. CPCV (Combinatorial Purged CV) 구현
15. Deflated Sharpe Ratio, PBO 구현
16. Bootstrap CI 구현
17. Walk-forward proper engine
18. 거래비용 모델
19. HRP, Black-Litterman 포트폴리오
20. Kelly + vol targeting sizer
21. TFT, N-BEATS, PatchTST 학습
22. Chronos, TimesFM foundation 활용
23. Causal Forest
24. GP, BNN 베이지안
25. Self-supervised pretrain
26. FinBERT 뉴스 임베딩
27. Stacking meta-learner

**Wave 3 — Production + 운영**:
28. Feature Store
29. Online inference service (FastAPI)
30. SHAP/LIME 설명
31. Conformal prediction (예측 구간)
32. MLflow experiment tracking
33. Grafana dashboard
34. Shadow trading
35. Champion-challenger
36. Real-time streaming
37. Stress testing
38. Compliance / tax accounting

**Wave 4 — 알파 발견 자동화**:
39. AutoML / Genetic Programming
40. Meta-learning
41. Anomaly detection (VAE)
42. Reinforcement learning execution
43. GNN sector network
44. Multi-asset / cross-market signals

---

## O. 전략 / 알파 패밀리 (현재 단일 long-only forward return)

- ⬜ **Long-short strategy** (롱 top quintile / 숏 bottom quintile)
- ⬜ **Market-neutral** (베타 헤지)
- ⬜ **Sector-neutral** (섹터 노출 0)
- ⬜ **Factor-neutral** (Fama-French 5 헤지)
- ⬜ **Pair trading / Statistical Arbitrage**
- ⬜ **PEAD (Post-Earnings Announcement Drift)** 전용 전략
- ⬜ **Sentiment arbitrage** (뉴스 sentiment vs 실제 반응)
- ⬜ **Volatility timing** (별도 vol 예측 → 포지션 사이즈 조정)
- ⬜ **Volatility risk premium** harvesting
- ⬜ **Dividend yield strategy** (고배당 + quality)
- ⬜ **Low volatility anomaly** (BAB - Betting Against Beta)
- ⬜ **Quality minus junk (QMJ)**
- ⬜ **Profitability factor** (Novy-Marx)
- ⬜ **Investment factor** (CMA)
- ⬜ **Earnings revision strategy** (analyst 예상치 변경)
- ⬜ **Index inclusion/deletion** (Russell, S&P add/drop)
- ⬜ **Buyback announcement** post-event drift
- ⬜ **Spin-off** outperformance
- ⬜ **IPO 1-3 yr** underperformance
- ⬜ **Insider trading follow** (Form 4 cluster buy → 60d hold)
- ⬜ **13F follow** (smart money tracking)
- ⬜ **Activist investor follow** (13D 후 N일)
- ⬜ **Momentum crash** detection / avoidance
- ⬜ **Mean reversion** vs momentum regime switching

---

## P. 라벨 / Target Engineering (현재 단순 forward return)

- ✅ ret_fwd_5d/21d/63d (계산만)
- ✅ rank_fwd_5d/21d (cross-sectional rank)
- ✅ vol_adj_ret_21d
- ⬜ **Triple-barrier method** (López de Prado) — 익절/손절/시간만료
- ⬜ **Meta-labeling** (primary model → secondary 신뢰도 모델)
- ⬜ **Sample weighting** (시간 가중 / 변동성 가중)
- ⬜ **Path-dependent labels** (최대 DD 동안 손실 났는지)
- ⬜ **Multi-target** simultaneous (return + vol + drawdown)
- ⬜ **Quantile binning** (-2σ ~ +2σ를 quintile로)
- ⬜ **Sign-only** target (방향만)
- ⬜ **Probability of outperformance** (cross-sectional binary)

---

## Q. 피처 엔지니어링 기법 (현재 raw만)

- ⬜ **Polynomial features** (x², x³, interactions)
- ⬜ **Feature interactions** 명시 (ratios, differences, products)
- ⬜ **Target encoding** for categorical (cluster_id, market, sector_bucket, regime_label)
- ⬜ **Feature scaling** for neural models (StandardScaler, RobustScaler)
- ⬜ **PCA** for dimensionality reduction
- ⬜ **Kernel PCA** for nonlinear
- ⬜ **t-SNE / UMAP** for visualization (offline)
- ⬜ **Mutual information** feature selection
- ⬜ **Boruta** feature selection
- ⬜ **Recursive Feature Elimination (RFE)**
- ⬜ **Lasso path** for stable feature selection
- ⬜ **Feature importance stability** (cross-fold consistency)
- ⬜ **Synthetic Minority Oversampling (SMOTE)** — class imbalance 라벨 시

---

## R. 데이터 품질 / Bias

- ⬜ **Survivorship bias correction** — 상폐 종목 historical 포함
- ⬜ **Look-ahead bias 자동 검출** (point-in-time DB 검증)
- ⬜ **Data quality checks** (schema, range, null rate)
- ⬜ **Outlier detection per feature**
- ⬜ **Corporate actions handling** (splits adjustment 확인)
- ⬜ **Merger/acquisition handling**
- ⬜ **Delisting handling** (return = -100% in last day)
- ⬜ **Stock splits** (currently adj_close에 반영, 별도 검증 필요)
- ⬜ **Dividends** (return total return 인지 price return 인지)
- ⬜ **Currency conversion** (USD/KRW 시계열 일관성)
- ⬜ **Time zone consistency** (KR vs US 시간)
- ⬜ **Holiday calendar** (양 시장 휴장일)
- ⬜ **Half-day trading** — US: Black Friday(11월 4번째 금요일 후), Christmas Eve, July 3 (조기 1pm 마감). KR: 매년 거래소 발표 (보통 폐장일 13:30 마감)

---

## S. 실거래 인프라

- ✅ Paper broker
- ⬜ **KIS Developers API** 어댑터 (KR live)
- ⬜ **Alpaca API** 어댑터 (US live)
- ⬜ Pre-trade compliance check
- ⬜ Post-trade reconciliation
- ⬜ Fill quality analysis
- ⬜ Live position vs DB reconciliation
- ⬜ Settlement T+2 simulation
- ⬜ Margin call simulation
- ⬜ Pattern Day Trader rule (US <$25k)
- ⬜ Wash sale prevention (US tax)
- ⬜ Tax lot tracking (FIFO/LIFO/specific)
- ⬜ Borrow availability check (shorts)
- ⬜ Locate cost tracking
- ⬜ Real-time price feed
- ⬜ Real-time news feed
- ⬜ Real-time disclosure feed
- ⬜ Latency monitoring (broker round-trip)

---

## T. 문서 / 운영

- ⬜ API documentation (Swagger/OpenAPI)
- ⬜ Model cards (Google standard)
- ⬜ Datasheets for datasets
- ⬜ Decision documents (ADR — Architecture Decision Records)
- ⬜ Failure mode documentation
- ⬜ Disaster recovery procedures
- ⬜ Backup / restore procedures
- ⬜ Security audit
- ⬜ Encryption at rest / in transit
- ⬜ Access control (RBAC)
- ⬜ Audit log (who deployed what when)
- ⬜ CI/CD pipeline (GitHub Actions)
- ⬜ Pre-commit hooks (lint, type-check, test)
- ⬜ Code coverage tracking

---

## U. 학술 SOTA 참고 (추가 reading list)

- ⬜ Cochrane "Discount Rates" — long-term return predictability
- ⬜ Hou-Xue-Zhang 4-factor model
- ⬜ Lewellen "Cross-Sectional Returns" — predictability
- ⬜ Israel-Moskowitz "The Role of Shorting"
- ⬜ Asness "Quality Minus Junk"
- ⬜ Frazzini-Pedersen "Betting Against Beta"
- ⬜ Daniel-Moskowitz "Momentum Crashes"
- ⬜ DeMiguel-Garlappi-Uppal "1/N portfolio"
- ⬜ López de Prado "Advances in Financial ML" (전체 다 적용)
- ⬜ Gu-Kelly-Xiu "Empirical Asset Pricing via ML" (벤치마크)

---

## V-pre. 추가 발견 (3차 자가검토)

### 시계열 통계 / Econometrics
- ⬜ **Fractional Differencing** (López de Prado) — memory 유지하며 stationary
- ⬜ **ADF/KPSS stationarity test**
- ⬜ **Cointegration tests** (Engle-Granger, Johansen)
- ⬜ **Granger causality** test (US ETF → KR 종목)
- ⬜ **Vector Autoregression (VAR)** 다변량
- ⬜ **VECM** (Vector Error Correction Model)
- ⬜ **Kalman filter** (state space, dynamic linear models)
- ⬜ **Particle filter**
- ⬜ **Bayesian Structural Time Series** (Google CausalImpact)
- ⬜ **DCC-GARCH** (dynamic conditional correlation)

### 행동재무 / Behavioral Finance
- ⬜ **Prospect theory** weighting (loss aversion 2.5x)
- ⬜ **Mental accounting** category-specific risk
- ⬜ **Disposition effect** indicators (보유 기간 + 수익률)
- ⬜ **Overreaction / Underreaction** windows
- ⬜ **Anchoring** (52-week high 거리)
- ⬜ **Herding** indicators (volume + correlation spikes)

### 정보이론
- ⬜ **Shannon entropy** of returns
- ⬜ **Mutual information** between features
- ⬜ **Conditional entropy**
- ⬜ **Transfer entropy** (방향성 있는 information flow)

### 손실함수
- ⬜ **Asymmetric loss** (big loss > big gain importance)
- ⬜ **Quantile loss** (pinball)
- ⬜ **Sharpe-aware loss** (직접 Sharpe 최대화)
- ⬜ **IC-aware loss** (직접 IC 최대화)
- ⬜ **Tilted absolute loss**
- ⬜ **Huber loss** (outlier robust)

### Backtesting 라이브러리 활용
- ⬜ **vectorbt** (이미 설치, 본격 미사용)
- ⬜ **backtrader**
- ⬜ **zipline-reloaded**
- ⬜ **bt** (flexible portfolio backtesting)
- ⬜ **pyfolio** (성과 분석)
- ⬜ **quantstats** (Sharpe/Sortino/Calmar 자동 리포트, 설치됨)
- ⬜ **alphalens** (factor analysis)
- ⬜ **empyrical** (성과 metric)

### 주문 관리
- ⬜ **Order Management System (OMS)** 구조
- ⬜ **Position blotter** (실시간 포지션)
- ⬜ **Trade ticket** generation
- ⬜ **Order book reconstruction**
- ⬜ **FIX protocol** (향후 institutional 거래 시. 현재 개인 운영엔 불필요)

### Monitoring 추가
- ⬜ **Cohort analysis** (when trained vs current performance)
- ⬜ **Survival analysis** (model lifecycle, 언제 deprecate)
- ⬜ **Champion-Challenger 자동 승강**
- ⬜ **Statistical Process Control** (SPC) charts

### 한국 추가
- ⬜ **DART 정정공시** detection
- ⬜ **DART 자본금 변경**
- ⬜ **회계처리방법 변경**
- ⬜ **대주주 변경**
- ⬜ **자기주식 취득/처분**
- ⬜ **KOSDAQ vs KOSPI 시장이전**
- ⬜ **관리종목/투자위험 지정**

### 미국 추가
- ⬜ **SEC 13F-HR/13F-HR/A** parsing
- ⬜ **Form 144** (planned sales)
- ⬜ **Schedule 13D/G** (5% holders)
- ⬜ **Form 5** (annual insider summary)
- ⬜ **Beneficial Ownership Reports**
- ⬜ **DEF 14A** (proxy statement compensation)
- ⬜ **Russell rebalance** (June reconstitution)
- ⬜ **S&P committee** index changes
- ⬜ **MSCI** ESG ratings (free tier?)

### 신경망 추가 기법
- ⬜ **Curriculum learning** (easy → hard)
- ⬜ **Domain adaptation** (KR ↔ US transfer)
- ⬜ **Multi-task learning** (return + vol 동시)
- ⬜ **Attention mechanism** (시간 단계별 가중)
- ⬜ **Positional encoding** for time series
- ⬜ **Mixture of Experts (MoE)** per cluster routing

### 강건성 / 견고성
- ⬜ **Adversarial training** (feature 교란 robust)
- ⬜ **Robust regression** (Huber, M-estimator)
- ⬜ **Differential privacy** (개인정보 보호 — 향후 클라이언트 운영 시)
- ⬜ **Model card with fairness metrics**

### 학습 다양화
- ⬜ **Online learning** (incremental update)
- ⬜ **Federated learning** (multi-source)
- ⬜ **Active learning** (uncertainty query)
- ⬜ **Few-shot learning** (new ticker)

---

## W. 사용자 명시 지침 (절대 망각 금지)

### W.1 HFT / Tick data — 점진 도입
- **현재 scope**: 일봉만
- **점진 계획**: 일봉 → 4h → 1h → 30m → 15m → 5m → 1m → tick (단계별)
- 각 단계 도입 전 직전 단계 모델이 안정화돼야 진행
- ⬜ 4시간봉 인제스트 + 모델 (Wave 5)
- ⬜ 1시간봉 인제스트 + 모델 (Wave 6)
- ⬜ 30분/15분/5분/1분 (Wave 7)
- ⬜ Tick data (Wave 8 — 데이터 비용 검토 필요)
- ⬜ Order book level 2 (Wave 9)
- **데이터 소스 후보**:
  - US: Polygon.io (5분봉 무료), IEX Cloud, Alpaca historical (분봉)
  - KR: KIS Developers API (실시간), pykrx (intraday 일부)

### W.2 Crypto/DeFi — 명시적 SCOPE 외 (당분간)
- 결정: ⏭️ EXCLUDED (현 universe 집중)
- 사유: 현실적 효용 + 시스템 한정
- 만약 다시 검토 시점 오면 별도 워크플로우로 분리

### W.3 ESG signals — 가치 판단 후 시도
- ⬜ MSCI ESG 등급 (무료 tier 확인)
- ⬜ Refinitiv ESG (무료 access 가능?)
- ⬜ Sustainalytics (Yahoo Finance에 일부 노출)
- ⬜ SEC 13F-에 ESG 관련 보유 정보 추출
- ⬜ 한국 ESG 평가원 (KCGS)
- ⬜ 본문 분석: 10-K Item 1A에 "ESG", "climate", "carbon" 등 빈도
- ⬜ 효용 평가 후 production 통합 결정

### W.4 Tax Optimization — 실거래 필수 (구현 의무)
**중요**: 실거래에선 *알파보다 큰 영향* 가능. 기능 효용 판단 시 *항상 포함*.

- ⬜ **Tax Loss Harvesting**:
  - 손실 포지션을 31일 이전에 매도 → 손실 인식
  - Wash sale rule (US): 30일 내 동일/유사 종목 재매수 시 손실 인정 안 됨
  - 대체 종목 매수 (sector 비슷한 ETF 또는 peer)
- ⬜ **Tax Lot Selection**:
  - FIFO / LIFO / Highest-cost / Specific-lot 선택
  - 단기(<1년 US) vs 장기(>1년 US) 자본이득세 차이
- ⬜ **Holding Period Optimization**:
  - 1년 직전 매도 시 vs 1년 직후 매도 시 세후 수익률 차이
  - 모델 시그널이 약해도 1년 임박 시 보유 유리할 수 있음
- ⬜ **Dividend Tax Drag**:
  - 배당 시점 회피 (ex-dividend date 직전 매도)
  - Qualified vs non-qualified dividend
- ⬜ **KR 양도소득세** (대주주 기준):
  - 본질적으로 운영 규모가 대주주 기준 미달이면 무세
  - 대주주 기준 자동 추적
  - 연말 회피 매도 전략 (12월 말 매도 → 1월 재매수)
- ⬜ **State tax 고려** (US 거주지별 다름)
- ⬜ **Tax-efficient rebalancing**:
  - 차익 실현 최소화하는 리밸런싱 경로
  - 손실 실현은 최대화

### W.5 Macroeconomic Forecasting — 필요 시 별도 모델 구현
- ⬜ 현재: macro_series는 *input만* 사용
- ⬜ 별도 매크로 예측 모델:
  - CPI 다음 발표값 예측 (LSTM, ARIMA, VAR)
  - GDP 다음 분기 예측
  - 실업률 추세 예측
  - 금리 예측 (Fed Funds futures + 모델)
- ⬜ 효용 평가 — 매크로 예측이 종목 시그널보다 유의미한지 측정
- ⬜ Integration: 매크로 예측 결과를 종목 모델의 추가 feature로 투입

### W.6 Currency Hedging Strategy — 베스트 구현 + 대안 리스트
**현재 결정** (베스트로 판단되는 형태):
- ⬜ **Natural Hedge**: 자본 일부를 USD로 보유 (USDKRW 노출 분산)
- ⬜ **거래 시 통화 매칭**: KR 운영은 KRW 계좌, US는 USD 계좌
- ⬜ **포지션별 currency exposure 추적**

**대안 리스트** (나중에 검토):
- ⬜ FX forward 헤지 (KIS FX forward 제공 시)
- ⬜ Currency ETF hedge (UUP for USD long, FXE for EUR 등)
- ⬜ Dynamic hedge ratio (변동성 기반 hedge 비율 조정)
- ⬜ Cross-currency basis swap (institutional only)
- ⬜ DLR(달러), DXJ(엔 헤지 일본) 같은 통화 헤지 ETF
- ⬜ Options-based hedge (USD put options)

---

## W.7 Wave 4 — 매크로/Cross-asset 활용 깊이 (사용자 직접 제안, 2026-06-04)

**근거**: 사용자 지적 — "금값/유류값/환율/기준금리/체감금리/국채 등이 지수와 개별 주가 예측에 시장 readability를 높일 텐데, 우리가 충분히 활용하고 있나?"

**현재 상태**: 36개 macro/cross-asset features 보유 (vix/dxy/us10y/yield_curve/sector ETFs/gold/oil/...) 그러나 단순 join 만. 활용 깊이 부족.

**Wave 4 구현 항목 (Wave 3 Optuna+Ensemble 완료 후)**:

### W.7.1 Regime × Feature interaction (~16 features)
- HMM 5-state regime을 핵심 features와 cross-multiply
- 같은 RSI라도 risk_on에서는 follow-through / risk_off에서는 mean reversion
- 모듈: `regime/hmm_classifier.py` + `features_cross_section.py` interaction 패턴
- 우선순위: 매우 높음 (가장 큰 alpha 기여 추정)

### W.7.2 누락 매크로 10개 백필 + features
- Yield curve 3-factor decomposition (level/slope/curvature, Litterman-Scheinkman 1991)
- VIX percentile in trailing 252d (절대값 < 분위수)
- VIX term structure (VIX9D/VIX/VIX3M contango ratio)
- Volatility Risk Premium (VIX² − realized vol)
- Real yield momentum (TIPS DFII10 변화율)
- Credit spread momentum (HY OAS BAMLH0A0HYM2 5d/21d change)
- Funding stress proxy (DGS3MO − FEDFUNDS, TED 대체)
- Equity Risk Premium (Forward EY − 10Y yield) — yfinance forward_estimates 활용
- Global liquidity (RRPONTSYD Fed balance sheet)
- Inflation breakeven momentum (T10YIE 변화율)

### W.7.3 Sector × cross-asset interaction (~50 features)
Sector 별로 다른 cross-asset 의존성:
- Energy ↔ WTI_OIL 21d change (β_oil 강)
- Bank ↔ yield_curve_2_10 (steepening favorable)
- Utility ↔ us10y (rate duration)
- Gold miner ↔ GLD momentum
- Tech ↔ us10y_5d_chg (long-duration sensitivity)
- REIT ↔ TLT momentum
- 등 11 sector × 5 cross-asset = 55 interaction features

### W.7.4 Market-level meta-prediction
- Step 1: SPY/QQQ/KOSPI200 단독 macro-only 예측 모델
- Step 2: 시장 prediction → 개별 ticker 모델의 추가 feature
- Long/short 전략의 alpha-beta separation 핵심
- 학술: "Hierarchical multi-task forecasting" (Tsai et al. 2024)

### W.7.5 Lead-lag features
- Leading (선행 6-12개월): yield curve, ISM PMI, building permits
- Coincident: industrial production, employment
- Lagging (후행): CPI, GDP (revision 많음)
- 시계열별 lag 3개월/6개월 features 추가
- 학술: Stock & Watson (2002) leading indicators

**예상 alpha 영향**: +15-25% (Lopez de Prado quant 연구 평균치).

**진행 트리거**: Wave 3 (Optuna 100×top10 cluster + Wave 3 모델 + Ensemble re-opt) 완료 후 즉시.

---

## V. 회수율 / 자기점검 항목

이 GAPS.md 자체에 대한 체크 (작업 시작 시마다):

- [ ] 마지막 검토일로부터 *새로 시도해본 것* 있나? GAPS에 결과 반영했나?
- [ ] *EXCLUDED* 표시한 거 *이유* 명시했나?
- [ ] 한 카테고리만 deep-dive하고 다른 카테고리 무시하고 있지 않나?
- [ ] WORK_LOG와 GAPS.md 둘 다 갱신했나?
- [ ] 사용자가 발견하기 전에 누락 발굴했나?

---

## X. 알파 개선 기법 battery (2026-06-16~ 전수 테스트, 정직한 다regime 알파 기준)

> **평가 바**: 2018-2024 다regime walk-forward(재선택+다중시드 mean±std)에서 **mn_long 단독(+9.5%/년, KR)을 robust하게 넘는가**. cross-market(US) 확인 필수.
> **교훈**: 단일/단일시장 스파이크는 대부분 artifact(강세장+46/1회선택+22/regime+8/topk30+39/앙상블+16% 전부 가짜). **다중시드 비중첩만 진짜.**
> **열린 루프**: 미테스트 0이 돼도 새 기법/아이디어 떠오르면 즉시 복귀.

### A. 타깃/라벨
- ✅ mn(시장중립 잔차) — **유일 검증 승자 ~+10%/년** (프로덕션 반영됨)
- ⏭️ rank/raw(baseline ~0), sn(섹터중립 +0.6%, 죽임)
- 🟦 vadj(vol조정 Sharpe형) — Wave1 측정중
- ⬜ triple-barrier(López), meta-labeling, 베타중립 잔차(beta 회귀), 다호라이즌 블렌드, winsorized 타깃

### B. 피처 전처리 (거의 미탐색)
- ✅ **횡단면 per-date z-score(mn_norm) — 채택(2026-06-17)**. mn_rs 대비 4컷중 3컷 우세, step강건(8~15 vs mn_rs 1~9), **bear 양수**(US hi-vix +0.80 vs mn_rs −0.94), MDD −22 vs −34%, **IC 유지(0.025)=안정화지 팩터틸트 아님**. train_production/production_inference 양쪽 배선.
- ⏭️ **rank-transform 기각**(Wave2): US 수익20.5이나 IC 0.023<mn_norm 0.025, 고분산 — 틸트.
- ⏭️ **winsorize 기각**(Wave2, 근소): IC는 양시장↑(US 0.0305/KR 0.0168), US 전면우세지만 **KR top-decile 수익↓(음수)**. 교훈: *rank-IC(전체횡단면)≠top-decile 수익(실거래)*. 시장대칭 파이프라인엔 미채택(per-market 전처리 허용 시 US만 재고 여지).
- ⬜ 팩터중립화(beta/size/sector 잔차), PCA, 분위 binning — 잔존

### C. 모델 클래스 (트리만 썼었음)
- ✅ **LGBM 유지 (최적)** | ⏭️ 앙상블(±15)/Ridge(기각)/MLP·wide·ens(기각)
- ⏭️ **Wave2 기각(2026-06-17)**: XGBoost(US IC 0.019<mn_norm), CatBoost(US IC 0.030 좋으나 KR 분산±14 불안정), ExtraTrees(수익 21.7이나 IC 0.008/46%=선택능력0 함정), ENet/Lasso(선형 열세). GBDT+정규화가 모델클래스 최적 확정.
- ⬜ LSTM/TFT/PatchTST 시퀀스(models_v3 보유) — Wave4 잔존(GPU)

### D. 신규 피처/신호 ★★★ 최대 미답 영역 (알파의 원천, 2026-06-23 점검)
> **진단**: I축 뉴스 피처 40개 + macro/cross-asset 35개가 캐시에 있으나 **번들 top-50 선택 거의 0**. 모델·전처리는 소진했어도 *피처 공간은 거의 안 건드림.*
- 🔴 **Information(I)축 죽음**: 뉴스/센티먼트 40개 top-50 선택 0개(양시장). 2018-2024 뉴스 historical 거의 없음(~7개월) → **10년 백필(저장공간 대기) 후 테스트 가능**.
- ⏭️ **Wave-4 macro-deep 상호작용 기각(2026-06-23)**: factor×regime/vix/yield/credit 13종+subset ablation 전부 US IC 미달(swabs 0.0356 → md 0.0246, vix 0.0308, reg 0.0347, macro 0.0166). **명시 상호작용 3번째 실패**(regfeat·Wave5·macro-deep) — GBDT가 이미 regime split으로 포착, 수작업 상호작용은 과적합만 추가. "+15-25%" 추정 근거없음.
- ⬜ **학습 데이터 10년 확장**(현재 2018-2024 6년 → 2016-2026): 더 많은 데이터=진짜 미답 레버. ★최고우선(상호작용 죽었으니).
- ⬜ **seed-ensemble**(N시드 평균): ±5-9% 시드분산 제거, 싸다.
- ⬜ seed-ensemble(±5-9% 시드분산 제거), adopted 레버 스태킹(mn+tb).
- ⏭️ **Wave3 기각(2026-06-17)**: idio-vol(틸트, KR 집중↑·수익↓ 해로움) / 조잡 residual momentum(ret−β·시장; KR 우세하나 US IC↓). **ablation이 시장모순 폭로** — 어느 구성도 양시장 미승. 미채택.
- ✅ **정식 Blitz residual momentum 채택·활성화(2026-06-18)**: 일별 잔차 12-1m 누적/잔차vol 표준화. 양시장 IC↑(KR 0.0060/US 0.0286) + 집중↓(US 69) + MDD↓ + US 전regime↑. `features_advanced`+ALL_FEATURE_COLS 배선, `augment_blitz.py`로 캐시 병합(추론과 동일 SPY 프록시), 번들 재학습(blitz top-50 선택 KR33/US26위). KR WF IC 0.0387→0.0412↑, US 0.0289→0.0242(노이즈 범위) — KR명확/US중립.
- 🟦 **Wave5 진행(2026-06-18)**: 단기 잔차리버설(resid_rev 5/10/21d), 계절성(월 sin/cos, 월말), 상호작용(vol×mom, beta×mom) = mn_w5 — 테스트중
- ⬜ Wave-4 상호작용(regime×feat, yield 3-factor, sector×cross-asset, lead-lag — 미구현), 52주 고점근접, amihud 유동성

### E. 샘플 처리
- ✅ **|label| 가중(mn_swabs) 채택**(Wave5). ⏭️ recency 가중 기각(양시장 IC↓).
- ⏭️ **uniqueness/dispersion 기각(2026-06-20)**: uniq 단독≈무효(균일호라이즌 횡단면이라 concurrency 상수). uniqabs(uniq×abslabel)는 5seed서 IC↑(US 0.0389/KR 0.0171)이나 **conc5 양시장 악화(US65→79,KR93→119)+비원칙 메커니즘**(조잡구현이 2018-Q1 지속 up-weight) 기각. disp(날짜분산)=conc5 270~308% 망가짐.
- ⬜ 정식 regime-다양성 가중(제대로 구현), purged/combinatorial CV

### F. 포트폴리오/사이징
- ✅ 균등 top-decile | ⏭️ 롱숏(죽임), conviction 가중(KR conc5 1136%), **decile 5/20%(IC불변)**, **meta-labeling 기각(2026-06-20: 2차분류기 사이징 — US 수익 21.9→11.6 반토막+conc5 116, IC동일. conviction과 동일 실패)**. **등가중이 최적 확정.**
- vol-타게팅: 미테스트(저우선 — 사이징류 전부 실패).

### A 추가. 다호라이즌 라벨 — 기각(2026-06-19)
- ⏭️ **mnmh(5/21/63d mn 블렌드)**: 1차 245%/yr·IC 0.13 = **누수**(21d 임베고 < 63d 라벨). embargo=63 교정 후 **시장갈림**(US IC 0.0396→0.0242↓, KR 0.0146→0.0185↑) → 기각. 교훈: 임베고 ≥ 최대 라벨 호라이즌.

### A 추가2. triple-barrier 라벨 — US만 채택(2026-06-20)
- ✅/❌ **tb(경로인지 ±k·vol·sqrt(H) 첫터치 수익, mn중립)**: 라벨무관 번들 WF IC로 **US 0.0313→0.0407(100% 양수, 채택)** / **KR 0.0325<mn 0.0440(기각)**. per-market 라벨(번들 분리라 네이티브). train_production `--label`(기본 US=tb/KR=mn), `_tb_label`+`_close_panel` 재사용. **추론 불변**(라벨은 학습 타겟).
- 🔧 **방법론 수정**: 번들 WF IC 기준을 학습라벨→**라벨무관 실현 mn수익**으로 고정(라벨 바꿔도 비교가능). 이 수정이 tb의 alpha_lab↑/번들↓ 불일치를 드러냄.

### G. 튜닝
- ⏭️ **Optuna OOF-IC 기각(2026-06-20)**: US OOS-IC BEATS(0.0379→0.0403)였으나 **KR 분산 폭발(±8.13)·IC 개선0** = US 과적합, cross-market 일반화 실패. (opt_lab.py, --fast)

### 기각 확정 (이유)
regime-conditional / topk30 / 섹터중립 / 롱숏 / VIX게이팅 / 앙상블(LGBM+HGB) / regime-라우터·오버레이·소프트블렌딩·피처 — **전부 다중시드서 mn_long 미달**(노이즈 과적합/희석).

**딥러닝(2026-06-17)**: mn_mlp(KR 0.98±4.27) / mn_mlp_wide(8.37±5.80) / mn_mlp_ens(0.56±1.40) — GPU MLP 3변형 전부 GBDT(9.48) 미달. 횡단면 tabular는 GBDT 우위(문헌 일치). LSTM/TFT 시퀀스는 미테스트(잔존).

**Ridge 선형(2026-06-17) — 가짜 승리 적발, 방법론 교훈**:
- `mn_ridge_raw`: KR +21 / US +27%/yr로 화려했으나 **rcond≈1e-36 ill-conditioned** — raw 피처 스케일 차로 선형계가 수치 특이. 정규화하면 붕괴 → **스케일 artifact**. 기각.
- `mn_ridge`(정규화 선형): US +20.9(step42)로 GBDT 2배였으나 **호라이즌-일치(step21)서 +9.1로 추락 + OOS IC 음수(−0.009)**. 세 모델 IC가 똑같이 ~0.02인데 ridge만 step42 수익 2배 + top5 fold 84% 집중 → **저변동성/저베타 팩터 틸트**(지배피처 tracking_err/vol/beta/corr_spy)가 메가캡 지배기에 등가중 벤치 이긴 것, 종목선택 알파 아님. **mn_norm과의 차이**: mn_norm은 IC 유지+bear양수+step강건(진짜 안정화), ridge는 IC붕괴+step취약(가짜). 기각.
- **교훈(영구)**: alpha_lab가 *등가중 벤치 대비 수익*으로만 평가 → 팩터틸트=알파 혼동. **하니스에 OOS rank-IC 추가**(2026-06-17). 이제 모든 config는 IC(선택능력)와 벤치-상대 수익 둘 다로 판정. IC≈0인데 alpha 큰 건 팩터 베타지 알파 아님. **검증 승자 mn_rs는 IC 양수(+0.023~0.040, 83% 양수fold)로 진짜 선택 알파임이 재확인됨.**
- ⬜ 후속 lead: 저변동성은 실재 팩터 → 깨끗한 vol-factor를 *명시 피처/오버레이*로 mn_rs에 추가하면 보탬 되는지(IC 중립적으로) 테스트 — 단 IC 음수라 dollar-neutral 알파로는 회의적.
