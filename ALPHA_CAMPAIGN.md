# 알파 개선 캠페인 — 검증 연대기

> 목적: "수익 숫자"가 아니라 **검증 기준을 추가할 때마다 랭킹이 어떻게 뒤집혔는지**를 시간순으로 남긴다.
> 핵심 교훈: 매 시점 *수익 1위는 전부 가짜*였고, IC·bear·집중도·step·cross-market을 기준에 더할 때마다
> 가짜가 탈락해 결국 **mn(라벨) + norm(정규화)** 두 개만 살아남았다.
>
> 관련: [WORK_LOG.md](WORK_LOG.md)(일자별 상세) · [GAPS.md](GAPS.md) §X(미테스트 battery) · `backend/scripts/alpha_lab.py`(하니스)

---

## 1. 검증 방식 (무엇으로 판정하는가)

| 방식 | 무엇을 잡아내나 | 기준 |
|---|---|---|
| **no-look-ahead walk-forward** | 미래정보 누수 | 확장창 + 21d embargo, 매 step 재학습 |
| **다중시드 (mean±std)** | seed 노이즈(±9%/yr 가짜) | 시드 42/1/7, 비중첩이어야 진짜 |
| **cross-market (KR↔US)** | 단일시장 과적합 | 양 시장 모두 양수여야 채택 |
| **out-of-regime (bear)** | 약세장 붕괴 | vix lo/mid/hi 분해, hi(bear) 양수 |
| **step 강건성 (21 vs 42)** | rebalance 주기 민감도 | 주기 바꿔도 안정해야 |
| **OOS rank-IC** | 선택능력 (벤치수익 ≠ 알파) | IC>0, 팩터틸트 vs 진짜 알파 구분 |
| **IC+% (양수 fold)** | 일관성 | 50%(동전) 초과, 높을수록 robust |
| **conc5 (top5 fold 집중도)** | 소수 운fold artifact | ~100%↑면 몇 fold가 전부 = 가짜 |
| **재선택 (reselect)** | test-period 선택편향 | 매 창 과거데이터로 피처선택 |
| **메커니즘 진단** | leak / 수치붕괴 정체 | 지배피처 · 조건수(rcond) |
| **비용 반영** | 거래비용 후 생존 | KR 30bps / US 10bps |

## 2. 테스트 대상 용어

| 대상 | 의미 |
|---|---|
| **mn** | 시장중립 잔차 라벨(수익−당일평균). 시장 추종분 빼고 종목 고유 초과수익 예측 |
| **rs** | reselect — 매 rebalance 과거데이터로 피처 재선택(선택편향 제거) |
| **norm** | per-date 횡단면 z-score 정규화 |
| **ridge_raw** | 미정규화 raw 피처 + Ridge 선형 |
| **ridge** | 정규화 + Ridge 선형 |
| **vadj** | vol조정 라벨(Sharpe형) |
| **mlp / wide / ens** | GPU 신경망(기본 / 넓은 / 3시드 앙상블) |
| **xgb · cat · et** | XGBoost · CatBoost · ExtraTrees |
| **rank / winsor** | 전처리 변형(순위변환 / z를 ±3 clip) |
| **conv** | conviction 가중(점수비례 비중) |
| **w3** | Wave3 잔차신호(idio-vol, residual momentum) 추가 |

## 3. 시간순 랭킹 (검증 기준을 추가해가며)

> 각 시점의 날짜+시간은 그 시점을 *정의한 실험의 완료 시각*(백그라운드 작업 로그 기준).

### 시점 0 — 세션 전 (~2026-06-16) (기준: 수익만, KR step42)
| 순위 | 대상 | KR alpha/yr |
|---|---|---|
| 1 | **mn_rs** ✅ 검증 승자 | 9.48±5.47 |
| — | rank/ret 라벨 | ~0 (기각) |
| — | regime/topk30/sn/ls/ensemble/gate | 전부 기각 |

### 시점 1 — Wave1 · 2026-06-16 23:55 (기준: 수익, KR step42) · 미테스트 레버 5종
| 순위 | 대상 | KR alpha/yr | 비고 |
|---|---|---|---|
| 🥇 1 | **mn_ridge_raw** | **21.03** | ⚠️ 너무 화려 |
| 2 | mn_rs | 9.48 | 기준 |
| 3 | mn_norm | 8.79 | |
| 4 | mn_ridge | 5.62 | |
| 5 | vadj_rs | 4.97 | |
> 수익만 보면 ridge_raw 압도적 1위. 여기서 멈췄으면 가짜 채택.

### 시점 2 — Wave4 MLP 추가 (GPU) · 2026-06-17 00:59
| 순위 | 대상 | KR alpha/yr |
|---|---|---|
| 1 | mn_ridge_raw | 21.03 |
| 2 | mn_rs | 9.48 |
| 3 | mn_norm / mlp_wide | 8.79 / 8.37 |
| 하위 | mn_mlp 0.98, mlp_ens 0.56 | 딥 전부 하위 |

### 시점 3 — US cross-market · 2026-06-17 01:22 (기준: 수익, 양시장)
| 순위 | 대상 | KR | US |
|---|---|---|---|
| 1 | mn_ridge_raw | 21.03 | 27.62 |
| 2 | mn_ridge | 5.62 | 20.89 |
| 3 | mn_norm | 8.79 | 15.43 |
| 4 | mn_rs | 9.48 | 9.99 |
> 양시장 모두 ridge류가 수익 1·2위. 의심 증폭.

### 시점 4 — 🔑 OOS IC + 메커니즘 진단 도입 · 2026-06-17 09:23~10:21 (랭킹 대격변)
| 대상 | 수익(US) | **IC** | 판정 |
|---|---|---|---|
| mn_ridge_raw | 27.62 | — | ❌ rcond 1e-36 수치붕괴(스케일 artifact) |
| mn_ridge | 20.89 | **−0.009**(step21) | ❌ IC음수 = 저변동 팩터틸트 |
| **mn_norm** | 15.43 | **+0.0254** | ✅ IC양수 |
| **mn_rs** | 9.41 | **+0.0236** | ✅ IC양수 |
> IC를 넣자 수익 1·2위(ridge)가 추락. "수익 ≠ 알파" 적발. 지배피처=tracking_err/vol/beta/corr → 저변동 틸트.

### 시점 5 — step강건성 + conc5 + bear 도입 · 2026-06-17 11:51~14:15 (mn_norm 대관식)
| 순위 | 대상 | step강건 | bear(US hi-vix) | MDD | 판정 |
|---|---|---|---|---|---|
| 🥇 1 | **mn_norm** | 8~15 안정 | **+0.80** | -22% | ✅ **채택(프로덕션 배선)** |
| 2 | mn_rs | 1~9 취약(KR 9.48→1.11) | −0.94 | -34% | 유지(기준) |
| 기각 | mn_ridge | step취약(US42 21→21서 9.1) | — | — | IC붕괴 |
> mn_norm: IC 유지(저변동 틸트면 IC 떨어졌을 것) + bear 양수 + step 강건 + MDD 개선 = 진짜 안정화.

### 시점 6 — Wave2 도전자 7종 · 2026-06-17 17:05~17:48 (기준: IC + 분산 + 집중 + 시장일관)
| 대상 | US수익 | US IC | 탈락 사유 |
|---|---|---|---|
| 🥇 **mn_norm** (방어) | 15.43 | 0.0254 | ✅ 유지 |
| mn_et | **21.70** | 0.0077 | ❌ IC≈0, fold 46%(동전이하) = 선택능력 0 |
| mn_rank | 20.51 | 0.0233 | ❌ IC<norm + 고분산 |
| mn_winsor | 19.82 | **0.0287** | ❌ KR top-decile 수익↓ (rank-IC ≠ 실거래수익) |
| mn_cat | 14.92 | **0.0297** | ❌ KR 분산 ±14.04 |
| mn_xgb | 14.17 | 0.0193 | ❌ norm 미달 |
| mn_conv | — | — | ❌ KR 집중 1136% |
> 또 수익 1위(et 21.7)는 IC 0.008 함정. 도전자 전원 한 기준씩에서 탈락. mn_norm 방어 성공.

### 시점 7 — Wave3 잔차신호 · 2026-06-17 19:08~22:xx (ablation 후 **미채택**)
idio-vol + 다호라이즌 residual momentum를 mn_norm 위에 추가. 2-seed/단일시드 bear는 유망했으나
**3-seed ablation이 시장 간 모순을 폭로**:
| 3-seed | KR alpha | KR IC | KR conc5 | US alpha | US IC | US IC+% |
|---|---|---|---|---|---|---|
| mn_norm | 8.79±7.84 | 0.0049 | 105% | 13.73±3.06 | 0.0249 | 57% |
| mn_w3 (둘 다) | 8.43±4.32 | 0.0066 | **375%** | 17.16±5.21 | 0.0259 | 60% |
| mn_w3_rm (resid_mom만) | **18.66±3.34** | 0.0053 | 111% | 14.06±4.25 | **0.0207** | **46%** |
| mn_w3_iv (idio_vol만) | 12.82±4.86 | 0.0070 | 211% | 11.77±1.33 | 0.0247 | 51% |
> **모순**: KR은 resid_mom 단독이 최고(idio_vol 해로움), US는 resid_mom 단독이 IC↓(0.0207<norm)·IC+% 46%.
> 어느 구성도 양시장 깨끗이 이기지 못함 + mn_w3 KR conc5 375%(집중). 단일 config면 "양시장 IC↑"로 채택할
> 뻔했으나 ablation이 *시장마다 다른 피처가 우연히 맞은 것*임을 폭로. **미채택, mn_norm 유지.**
> 후속: 정식 Blitz residual momentum(잔차수익경로/잔차vol)는 조잡근사 아닌 제대로 구성 시 재시도 후보.

---

### 시점 8 — Wave4 GPU 시퀀스 + 정식 Blitz · 2026-06-18
**LSTM 기각 / Blitz residual momentum 채택.**
| 3-seed | 대상 | KR alpha | KR IC | US alpha | US IC | US conc5 |
|---|---|---|---|---|---|---|
| 기준 | mn_norm | 8.79 | 0.0049 | 13.73 | 0.0249 | 101% |
| ❌ | LSTM(시퀀스) | +1.15±10.79 | 0.0028 | +18.30±1.09 | **0.0003** | — |
| ✅ | **mn_blitz** | 9.19±4.78 | **0.0060** | **22.83** | **0.0286** | **69%** |
> **LSTM**: US +18%/KR ±11%의 화려/불안정 수익이 **IC≈0**(US 0.0003 = norm의 1/80) + regime(bear) 틸트.
> ridge_raw·et와 동일 가짜. 딥러닝(MLP+LSTM) 트랙 소진 — 신경망은 횡단면 알파서 GBDT 불가.
> **Blitz**(일별 잔차 12-1m 누적/잔차vol 표준화, 정식): IC 양시장↑ + 집중↓(US 69) + MDD↓ + bear(US 전regime↑).
> 조잡근사(시점7)는 US IC↓로 실패했으나 정식 구성이 해결 → **채택**(캠페인 첫 검증된 신규 피처).

### 시점 9 — Wave5/6 (샘플가중·신규피처·전처리·튜닝) · 2026-06-18~19
**mn_swabs(|label| 샘플가중) 채택 / 나머지 기각.**
| 3-seed | 대상 | US IC | KR IC | 판정 |
|---|---|---|---|---|
| 기준 | mn_norm(+blitz) | 0.0278 | 0.0071 | — |
| ✅ | **mn_swabs** (\|label\| 가중) | **0.0356** | **0.0109** | 양시장 IC↑, bear통과 → **채택** |
| ❌ | mn_w5(리버설+계절성+상호작용) | 0.0281 | 0.0022 | KR IC 붕괴 |
| ❌ | mn_swrec(recency 가중) | 0.0202 | 0.0054 | 양시장 IC↓ |
| ❌ | mn_dec05/20(decile 변형) | 0.0278 | 0.0071 | IC 불변(포트 집중일 뿐) |
| ❌ | mn_neutral(팩터중립화) | 0.0176 | 0.0057 | 단독 IC↓ |
| ❌ | mn_swabs_neu(중립+가중) | 0.0266 | 0.0158 | **시장갈림**(US↓/KR↑) |
> **mn_swabs**: 큰 변동 종목(|잔차수익|)에 학습 가중 → 선택능력 sharpen. mn_norm 이후 최대 IC 레버(US +0.0078).
> 배선: train_production sample_weight=|mn label|(피처/캐시 변경 불필요). **번들 WF IC 양시장 최고치 갱신**: KR
> 0.0412→0.0440, **US 0.0242→0.0313**(blitz 하락분 상쇄+개선). 추론 smoke 정상.
> 팩터중립화는 ridge·winsor처럼 시장갈림으로 기각.

### 시점 10 — Wave7 다호라이즌 라벨 · 2026-06-19 (누수 적발 → 기각)
다호라이즌 mn(5/21/63d 블렌드) 첫 결과 **KR 245%/yr·IC 0.13** = 임팩트 불가능 → 즉시 누수 의심.
**원인: 21d 임베고가 63d 라벨에 불충분**(학습 컷 근처 63d 라벨이 R+42까지 새어듦). embargo=63로 교정 후:
| embargo=63 | US IC | KR IC | 판정 |
|---|---|---|---|
| mn_swabs_emb(기준) | **0.0396** | 0.0146 | — |
| mn_mh_emb | 0.0242↓ | 0.0185↑ | ❌ 시장갈림(US↓), 기각 |
> 교훈(영구): **임베고 ≥ 최대 라벨 호라이즌**. 화려한 숫자(245%)는 IC로 즉시 검증→누수 규명→교정.

## 4. 한 줄 결론
매 시점 **수익 1위는 전부 가짜**(ridge_raw 21→ridge 27→et 21.7→w3 시장모순). IC·bear·집중도·step·
cross-market·**ablation**을 기준에 더할 때마다 랭킹이 뒤집혀, 화려한 숫자가 차례로 탈락하고 **mn 라벨 +
per-date 정규화** 두 레버만 살아남아 프로덕션에 반영됨. *숫자 크기가 아니라 검증 기준의 두께가 승자를 결정한다.*

## 5. 최종 채택 현황
| 레버 | 상태 |
|---|---|
| **mn (시장중립 잔차 라벨)** | ✅ 프로덕션 |
| **per-date 횡단면 정규화** | ✅ 프로덕션 |
| **Blitz residual momentum (신규 피처)** | ✅ **활성화 완료 (2026-06-18)** |
| **\|label\| 샘플가중 (mn_swabs)** | ✅ **활성화 완료 (2026-06-19)** |
| 그 외 전부(ridge/MLP/LSTM/xgb/cat/et/rank/winsor/conv/regime류/조잡잔차/w5/recency/decile/팩터중립화) | ❌ 기각 |

> **4개 채택**, 나머지 전부 기각. 누적 효과 — 번들 WF IC: KR 0.0387→**0.0440**, US 0.0289→**0.0313**.
> 잔존 미테스트: triple-barrier, meta-labeling, 다호라이즌 블렌드, uniqueness 가중, Optuna(진행중) — [GAPS.md](GAPS.md) §X.

### Blitz 활성화 기록 (2026-06-18)
- 배선: `features_advanced.compute_stat_features`에 resid_mom_blitz_12m/6m 추가 + ALL_FEATURE_COLS 등록(추론 자동 보유). 15h 전체 재빌드 대신 `augment_blitz.py`로 **동일 함수·동일 SPY 프록시** 사용해 캐시에 blitz만 병합(train/infer 일관).
- 재검증(파이프라인판): mn_norm IC **US 0.0249→0.0278, KR 0.0049→0.0071**(둘 다↑), 집중↓.
- 번들 재학습: blitz가 importance로 top-50 선택(KR 33/34위, US 26위). **KR WF IC 0.0387→0.0412(↑)**.
  ⚠️ 정직히: **US 번들 WF IC 0.0289→0.0242(↓, 단 std 0.026 내 노이즈)** — 지표 간 엇갈림. KR 명확↑/US 중립.
- 추론 smoke: 양시장 blitz 피처 존재+예측 정상(US BUY52/KR BUY32, NaN 0). 활성화 확인.
