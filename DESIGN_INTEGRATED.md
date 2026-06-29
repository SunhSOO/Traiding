# 통합 시스템 설계 — 선정·시황(알파) → 단일종목 실행(기술적)

> 상태: **설계안 (리뷰 대기)** · 2026-06-29 · 구현 전 확정용
> 원칙(원 제작자 합의): **기술적 요소(Red/Green 포함) = 단일 종목 매매(실행)** / **횡단면 알파 = 지수·시황 파악 + 투자할 종목 선정**

---

## 1. 목표와 역할 분담

| 계층 | 담당(=무엇) | 사용 엔진 |
|---|---|---|
| **선정·시황** | 시황 파악 + **어떤 종목을 살까**(바스켓) + 시장 노출 | 횡단면 알파 ML(5레버) + regime 분류기 |
| **단일종목 실행** | 선정된 각 종목의 **언제 진입/청산**(타이밍) | 기술적 시그널(trend/momentum/mean-rev/Red-Green) |

핵심: 알파 = **WHAT**(선정), 기술적 = **WHEN/HOW**(실행). 현재 둘은 병렬·독립 → **파이프라인으로 통합**.

---

## 2. 파이프라인 아키텍처

```
매일 (데이터 적재 + regime 분류 후):

[A. 시황 READ]  regime 분류기 + 알파 시장관점(breadth·평균 conviction)
                 → 시장 노출 타깃(risk_on 100% / risk_off 50% / crisis 0%)
                          │
[B. 선정]       ProductionRecommender(횡단면 알파)
                 → 랭킹 바스켓(top-decile) + 종목별 conviction·target_price·밴드
                          │  ── 핸드오프: selection_basket 테이블에 "보유 의도" 영속 ──
                          ▼
[C. 실행 타이밍] 바스켓 각 종목에 기술적 시그널(일봉)
                 → 진입: 바스켓에 있고 기술적 타이밍 OK일 때만 실제 매수
                 → 청산: 기술적 붕괴 OR 바스켓 이탈 시 매도
                          │
[D. 리스크·체결·감사] RiskEngine → PaperBroker(통합 계정) → DecisionAudit(2단계 사유)
```

**2단계 의사결정**: ① 왜 이 종목이 바스켓에 들었나(알파 선정 사유) ② 왜 지금 진입/청산하나(기술적 타이밍 사유) — 둘 다 감사에 기록.

---

## 3. 데이터 모델

### 3.1 신규 테이블: `selection_basket` (선정 계층 출력 = 보유 의도)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| market, as_of_date (PK) | str, date | 시장·기준일 |
| ticker (PK) | str | 종목 |
| rank_pct | float | 횡단면 백분위(0~1) |
| target_weight | float | 목표 비중(균등 또는 conviction 틸트) |
| pred_ret_21d, target_price, band_low, band_high | float | 알파 예측 |
| in_basket | bool | top-decile 여부(BUY 후보) |
| regime, market_exposure | str, float | 그날 시황·노출 타깃 |
| as_of_ts | datetime | PIT 무결 |

### 3.2 신규 테이블: `market_read` (시황 계층 출력)
| 컬럼 | 설명 |
|---|---|
| market, as_of_date (PK) | 시장·기준일 |
| regime, regime_conf | regime 분류기 결과 |
| breadth | 알파 예측 양(+) 종목 비율(시장 폭) |
| avg_conviction | 바스켓 평균 \|rank_pct−0.5\| |
| target_exposure | 권고 시장 노출(0~1) |
| inputs (JSONB) | VIX·yield·index trend 등 근거 |

### 3.3 기존 재사용/확장
- `decision_audit` 확장: `stage`(selection/execution), `selection_reason`, `timing_reason` 필드 추가(또는 inputs JSONB에 포함).
- `paper_accounts/positions/trades`, `market_regime`, `securities/universe_membership`, `module_scores`(T 점수) — 재사용.

---

## 4. 선정 계층 (B) — 우리 알파

- 입력: 라이브 피처(당일 횡단면) — **신규 과업**: 매일 피처 리빌드 잡(현재는 오프라인 캐시만; live 리빌드 미구현 = 갭).
- 처리: `ProductionRecommender.recommend()` → rank_pct, action(top/bottom decile), target_price, 밴드.
- 출력: `selection_basket` 영속(top-decile = in_basket=True).
- 시장별 모델: US=triple-barrier 라벨, KR=mn 라벨(검증된 per-market). 정규화·blitz·|label|가중 포함.

## 5. 시황 계층 (A) — 우리 알파 + regime

- regime: 기존 voting 분류기(`market_regime`) 재사용.
- 알파 시장관점(신규): breadth(예측 양수 비율), 평균 conviction → 시장 폭/강도.
- **target_exposure**: regime + breadth 매핑 (예: risk_on&고breadth=100%, neutral=70%, risk_off=40%, crisis=0%). 이게 "지수투자/시황" 노출 오버레이.
- *지수투자(ETF) 주의*: 현재 ETF 인스트루먼트 없음. v1은 **"바스켓 자체가 우리의 능동적 지수"**로 보고 target_exposure로 노출 조절. 실제 지수 ETF 매수는 인스트루먼트 추가 시 별도(향후).

## 6. 실행 계층 (C) — 기술적 단일종목 타이밍

- 대상: `selection_basket`의 in_basket 종목.
- 기술적 시그널(일봉): trend(EMA정렬)/momentum(RSI·MACD)/mean-rev/Red-Green-adapted → 종목별 타이밍 점수.
- **진입 규칙**: 바스켓 ∈ 종목 AND 기술적 타이밍 비-약세(예: trend≥0 & RSI 과매수 아님) → 매수(노출=target_exposure×target_weight).
- **청산 규칙**: 기술적 붕괴(trend 약세 전환) OR 바스켓 이탈 → 매도. (crisis면 전량 청산 = 기존 방어 로직 계승)
- **결정 사항 D1**: 기술적이 바스켓 종목을 *완전 거부(veto)* 가능? 또는 *타이밍만 지연*? → 권고: **타이밍 지연 + 약세 청산**(거부는 안 함; 알파가 선정 주도). 리뷰 확인.
- Red-Green 상태머신(XAUUSD M15)은 **그대로 FX/금 단일전략 유지**; 주식엔 그 시그널 로직을 일봉 타이밍 게이트로 *차용*(별도 인스턴스).

## 7. 계정·포트폴리오 모델

- **결정 사항 D2**: 통합 전략 계정 — 신규 `alpha-kr/us` 생성 vs 기존 `ml-kr/us` 재활용. 권고: **신규 `core-kr/us`**(통합 전략 명확 구분), 기존 ml-/default- 계정은 비교·레거시로 잔존.
- 리스크: 기존 RiskEngine(6한도) 재사용. 체결: PaperBroker. 통화매칭 가드 계승.

## 8. 기존 컴포넌트 재사용 맵
재사용: PaperBroker · RiskEngine · DecisionAudit(확장) · regime 분류기 · universe 쿼리 · ProductionRecommender · technical/signals · APScheduler.
신규: `decision/integrated_runner.py`(A→B→C→D), `selection_basket`·`market_read` 테이블·모델, 시장-read/노출 로직, 기술적 타이밍 게이트, 스케줄러 잡, 라이브 피처 리빌드 잡, 신규 UX 페이지·라우트.

---

## 9. UX 설계 (새 방향 전용 — 기존 화면 참고만, UX 재설계)

기존 결정엔진(F/T/I 합성) 내러티브 대신 **"시황 → 선정 → 실행"** 내러티브로 재구성. 신규 4화면:

### 9.1 시황 (Market) — "현재 증시 현황"
```
┌ 시황 대시보드 ───────────────────────────────┐
│ [KR] regime: RISK_ON ●  conf 0.72   노출타깃 100%│
│ [US] regime: NEUTRAL ◐  conf 0.55   노출타깃  70%│
│ breadth ▓▓▓▓▓▓░░ 63%(예측 양수)  평균conviction 0.34│
│ 지수추세: KOSPI ↑200d  S&P ↑200d   VIX 14(저)     │
│ [노출 추이 차트]  [regime 리본 180d]              │
└──────────────────────────────────────────────┘
```
의도: 한눈에 "지금 시장 들어갈 때인가, 얼마나" 판단.

### 9.2 선정 (Basket) — "투자할 종목"
```
┌ 오늘의 바스켓 (KR, n=35) ───── 변동: +4 신규 / -3 이탈 ┐
│ #  종목   conviction  목표가   밴드        어제대비   │
│ 1  삼성전자  0.97     82,000  78~86k     ▲신규       │
│ 2  ...      0.95     ...                 = 유지       │
│ [conviction 분포]  [신규/이탈 종목 리스트]            │
│ 종목 클릭 → 선정 사유(주요 피처 기여) + 실행 상태      │
└──────────────────────────────────────────────────┘
```
의도: 알파가 *왜* 이 바스켓을 골랐는지 + 변화 추적.

### 9.3 실행 (Execution) — "단일 종목 매매 타이밍"
```
┌ 실행 현황 (선정 ∩ 기술적 타이밍) ──────────────────┐
│ 종목   바스켓  기술적타이밍   상태        포지션      │
│ 삼성    ✓      진입OK ●      ▶ 보유중    +120주      │
│ SK하닉  ✓      과매수 대기 ◌  ⏸ 진입대기  -          │
│ LG화학  ✗      약세전환      ◼ 청산중    매도주문     │
│ 종목 클릭 → 기술적 시그널 상세(trend/RSI/Red-Green)   │
└──────────────────────────────────────────────────┘
```
의도: "선정됐지만 아직 안 산 것 / 산 것 / 파는 것"을 타이밍 사유와 함께.

### 9.4 통합 포트폴리오·감사
- 통합 계정 보유/P&L/equity curve + **2단계 감사**(선정 사유 → 실행 타이밍 사유)를 한 흐름으로.
- 기존 portfolio/decision_audit 페이지를 이 내러티브로 재설계(복사 X).

라우트(신규): `/api/market-read`, `/api/basket`, `/api/execution`, 확장 `/api/decision`(2단계).

---

## 10. 스케줄러/런타임 흐름 (일간)
```
06:30 macro.daily → 06:45 regime.daily
(장마감 후) prices → 21:30 technical.score → 신규 features.rebuild(라이브)
 → 22:30 integrated.daily: A 시황read → B 선정 → C 기술적타이밍 → D 체결/감사
```
기존 ml_decisions.daily / decisions.daily는 비교·레거시로 잔존(또는 단계적 정리).

---

## 11. 단계별 과업 (Phase)
- **P0 설계 확정**(이 문서) — 리뷰.
- **P1 선정·시황 백엔드**: `market_read`·`selection_basket` 모델+마이그레이션, 라이브 피처 리빌드, 시황-read/노출 로직, 바스켓 영속.
- **P2 실행 백엔드**: 기술적 일봉 타이밍 게이트, `integrated_runner`(A→D), 통합 계정, 리스크/체결/2단계 감사.
- **P3 스케줄러+테스트**: integrated.daily 잡, 단위/통합 테스트, paper 드라이런 검증.
- **P4 UX**: 시황/선정/실행/포트폴리오 신규 페이지 + 라우트.
- **P5 검증·문서**: paper 운영 검증, README/ARCHITECTURE 갱신.

---

## 12. 확정 필요 결정 (리뷰)
- **D1** 기술적의 권한: 타이밍 지연+약세청산만(권고) vs 바스켓 종목 완전 veto 가능.
- **D2** 계정: 신규 `core-kr/us`(권고) vs 기존 `ml-*` 재활용.
- **D3** 기존 composite F/T/I 경로: 레거시 잔존(권고) vs 즉시 정리.
- **D4** 지수투자: v1은 바스켓=능동지수+노출오버레이(권고) vs 지수 ETF 인스트루먼트 추가(향후).
- **D5** target_weight: 균등(권고, 검증됨) vs conviction 틸트.
