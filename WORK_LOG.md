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

---

## 현재 상태 스냅샷 (2026-06-01 기준)

| 도메인 | 커버리지 | 상태 |
|---|---|---|
| KR 종목 | 350 (KOSPI200+KOSDAQ150 marcap 프록시) | ✅ 라이브 |
| KR 일봉 | 122,927행 | ✅ 라이브 |
| US 종목 | 503 (SP500) | ✅ 라이브 |
| US 일봉 | 183,717행 | ✅ 라이브 |
| US CIK 보강 | 501/503 | ✅ 라이브 |
| US financial_facts | 100 종목 (114,408행) | ✅ 라이브 (100/501 SP500 = 20%) |
| 뉴스 기사 (RSS) | 582 | ✅ 라이브 |
| 기사 분류 | 582 (Ollama qwen2.5:14b) | ✅ 라이브 |
| 종목 mention | 41 | 🟡 낮음 (NER 패스가 high-confidence mention 적게 생성) |
| macro_series | 5 시리즈 × ~500행 | ✅ 라이브 (5/6 — 2Y 없음) |
| market_regime | 양 시장 NEUTRAL/0.0 | ✅ 라이브 (시그널 약함 — 정직) |
| module_scores Technical | 849 (KR 348 + US 501) | ✅ 라이브 |
| module_scores Fundamental | US 105, KR 0 | 🟡 US 21% 커버; KR은 DART 차단 |
| module_scores Information | 37 (KR 13 + US 24) | 🟡 뉴스 depth가 천장 |
| decision_audit | 1,006+ 행 | ✅ 라이브 |
| paper_positions | 20건 보유 (default-us) | ✅ 라이브 |
| paper_accounts | default-kr KRW 1억 + default-us USD 10만 → 100,235.87 | ✅ 라이브 |
| 공시 (DART/EDGAR) | 0 | ❌ 미실행 (DART 키 부재; EDGAR 8-K 파서 미연결) |
| financial_facts KR (DART) | 0 | ❌ DART_API_KEY 차단 |
| Phase 3 학습 (클러스터링 / 튜닝) | 0 | ⚪ 제안 (cluster_weights, ticker_clusters, backtest_runs 비어있음) |
| Phase 5 UI 브라우저 검증 | 미상 | ⚪ 이번 세션에서 미실시 |
| Phase 6 페이퍼 3개월 운영 | 미시작 | ⚪ 제안 |

---

## 알려진 한계 (감춰선 안 됨)

1. **KR 유니버스는 시가총액 프록시** — 실제 KOSPI200/KOSDAQ150 명단 아님. source = `fdr_marcap_proxy`. survivorship-bias-free 히스토리는 KRX CSV 업그레이드 필요.
2. **BF.B / BRK.B** US에서 누락 (FDR 클래스 주식 포맷). 수동 ticker 정규화 필요.
3. **2년 국채 수익률** 부재 (FRED 키 없고, Yahoo 무료에 미노출). regime 수익률 곡선 voter가 None으로 degrade.
4. **Information 스코어 커버리지 4%** — RSS 582 / 853 종목 비율이 자연 한도. BIGKINDS / NAVER / GDELT 백필로만 상승.
5. **Fundamental 스코어 KR = 0** — DART_API_KEY 설정 또는 대체 어댑터 작성 필요.
6. **Phase 3 학습 산출물 0** — 클러스터링 / walk-forward 백테스트 파이프라인은 골격만 있고 한 번도 실행 안 됨.
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
