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

### 시점 11 — 잔존(triple-barrier / Optuna) · 2026-06-20
- ❌ **Optuna 튜닝 기각**: US OOS-IC 탐색서 BEATS(0.0379→0.0403)였으나 **KR 분산 폭발(±1.32→±8.13)**·IC 개선 없음 = US 과적합, cross-market 일반화 실패.
- ⚠️ **하니스 함정 적발+수정**: 번들 WF IC가 `pred vs 학습라벨`을 재서 라벨 바꾸면 비교 불가였음 → **라벨무관 기준(실현 mn수익)으로 고정**. 이 수정이 tb의 거품을 드러냄(alpha_lab↑ vs 번들↓).
- ✅/❌ **triple-barrier = per-market 채택**: 라벨무관 번들 WF IC로 보면 **US tb 0.0313→0.0407(100% 양수fold, 채택)** / **KR tb 0.0325 < mn 0.0440(기각, mn 유지)**. 시장갈림이나 번들이 시장별 분리라 per-market 라벨 네이티브 지원. US만 경로인지 라벨 채택.

### 시점 12 — 샘플처리 + meta-labeling · 2026-06-20 (전부 기각, battery 소진)
- ⏭️ **uniqueness 가중**: 단독≈무효(균일호라이즌 횡단면=concurrency 상수). uniqabs(×abslabel)는 5seed IC↑(US 0.0389/KR 0.0171)이나 **conc5 양시장 악화+비원칙 메커니즘**(2018-Q1 지속 up-weight) 기각.
- ⏭️ **dispersion 가중**: conc5 270~308% 망가짐.
- ⏭️ **meta-labeling**: 2차분류기로 롱 사이징 → US 수익 21.9→11.6 반토막+conc5 116, IC동일(선택불변). conviction과 동일 실패. **등가중 top-decile 최적 확정.**
> ⚠️ **소진된 건 얕은 레버(모델·전처리·사이징·샘플가중·튜닝)뿐.** 알파의 원천인 **신규 신호(피처) 공간은 거의 미답** — §6 참고.

## 6. 미답 영역 — 피처/신호 frontier (2026-06-23 점검)
모델·전처리·사이징은 소진했으나 **피처는 거의 안 건드렸다.** 진단 결과:
- 🔴 **Information(I)축 죽어있음**: 뉴스/센티먼트 피처 40개가 캐시에 있으나 **번들 top-50 선택 0개**(양시장). 원인=2018-2024 학습기간 뉴스 historical 거의 없음(~7개월치뿐). → **10년 뉴스 백필(저장공간 대기) 후에야 테스트 가능** [[News history plan]].
- 🟡 **macro/cross-asset 35개도 거의 미선택**(top-50에 2~7개): vix·yield-curve raw는 **상호작용 엔지니어링 없이 무용**.
- ⬜ **Wave-4 macro-deep 상호작용피처**(regime×feat, yield-curve 3-factor, sector×cross-asset, lead-lag) — 메모리 추정 **+15-25% 알파**, 조잡 idio-vol/resid-mom만 했고 **본격 미구현**. **최고 우선순위.**
- ⬜ **학습 데이터 10년 확장**(현재 2018-2024 6년 → F/T 10년 보유, 2016-2026 풀캐시): 더 많은 데이터=종종 최대 레버. 미테스트.
- ⬜ **seed-ensemble**(N시드 평균 → 측정된 ±5-9% 시드분산 제거): 싸고 안정성 확정 개선. ⬜ **adopted 레버 스태킹**(mn+tb 블렌드).
- ⬜ TFT/PatchTST(저우선, 딥 실패).

**갱신(2026-06-23)**:
- ❌ **macro-deep 상호작용 기각** — factor×regime/vix/yield/credit 전부 IC 미달(명시 상호작용 3연속 실패, 트리가 이미 포착). "기존 데이터 재가공" 길 막힘.
- 🔍 **"다 찾았나?" 재점검 발굴**: 🟡learning-to-rank(L2회귀만→랭킹 목적함수 정합, 검증중 smoke US IC 0.041) / ⬜cross-market pooled / ⬜short-volume 피처(미사용 데이터) / ⬜PEAD. → 진짜 미답은 *새 데이터+목적함수*, 상호작용 아님.

### 시점 13 — 조합 검증 A/B/C · 2026-06-24 ("모든 조합 다 봤나?" push)
탐욕적 단일레버 검증의 빈틈(시너지·순서·하이퍼) 점검:
- **A) drop-one-out**: 최종 스택서 각 레버 제거 → **모두 기여 확인**(잉여 없음). 단 **normalize는 양시장 평균IC 미세음수, 순수 안정화 레버**(MDD/분산/집중 대폭↓). blitz도 KR선 안정화. → 스택 국소최적.
- **B) 기각레버를 최종 스택 위에서 재시험(시너지)**: **순서-의존성 실재** — winsor/macrodeep이 초기 baseline선 기각이나 **KR 최종 스택 위선 IC↑**(US선 여전히↓, 시장분리). KR winsor alpha_lab IC 0.011→0.023(2배). **단 bundle WF IC는 wash(0.0405<0.0440) — harness특정, 미채택**(tb교훈 재현). macrodeep은 5seed서 이득 증발(기각).
- ⇒ **시너지 후보를 발굴·엄밀검증했으나 bundle서 다 탈락 = 현 스택이 명백한 조합이득을 놓치지 않음 확인.** "전수는 불가하나 충분검증" 도달.
- **C) 하이퍼파라미터 스윕**: topk(US peak@50: 30/75/100 전부↓; KR 50이 robust, 타값은 분산/집중 폭증). tb배리어 k(US peak@1.5: 1.0/2.0/2.5 전부↓). **전부 최적, 튜닝이득 0.**
- ⇒ **A/B/C 종합: 최종 스택은 국소최적·시너지강건·하이퍼최적.** 전수조합(수백만)은 불가하나 표적검증서 개선 0 = "전수는 아니나 충분검증" 도달. (CLI --topk/--tbk 추가)

### 시점 14 — seed-ensemble + 데이터-양 · 2026-06-24
- 🟡 **seed-ensemble**(3시드 rank-평균): 양시장 IC↑(US 0.0339→0.0373, KR 0.0106→0.0132)+US분산↓. 알파보다 **시드-강건성** = production-hardening 레버(실배포 시).
- ⏭️ **10년 데이터 확장 기각**: 2016-2024 빌드 후 *공정* 테스트(동일 2018+ 평가, 학습만 2016~) → US IC 0.0396→0.0227, KR 0.0118→0.0056 **둘 다 큰폭↓**. 2016-18 다른regime이 stale-noise. **데이터-양 가설 falsified**(pooling+10년 삼중확인). 최근 regime 데이터로 충분.

### 시점 15 — US 앙상블(lgbm+ridge) 도전 · 2026-07-10 (단일 split 승자 → 16폴드 walk-forward + 적대검증 기각)
캠페인의 **시그니처 패턴 재현**: 단일 split의 화려한 승자가 엄격 검증에서 탈락.
- **선(先)정정 — 시장별 특화가 정답(방법론)**: 초과수익(excess=상위decile−유니버스평균, 베타제거=순수 선정가치)으로 판정하니 **동일 ridge가 US=최고·KR=최악**. 프로덕션 번들은 이미 시장별 분리(라벨 US=tb/KR=mn, per-date z, top-50, |label|가중)라 *구조는 옳았고*, "한 시장 승자를 반대 시장에 대입"하는 *검증기준*만 잘못이었음(cross-market은 각 시장 excess로 판정해야).
- 구현: `EnsembleRankModel`(횡단면 rank-average, `training/ensemble_model.py`) + `train_production.py --ensemble` 옵트인. 재학습·추론 통과(US 515종목).

| 검증 단계 | ens vs lgbm | 판정 |
|---|---|---|
| 단일 split 2022-23(excess) | ridge/ens 압도(+1.39/+0.85% vs lgbm +0.27%) | 🟡 유망(구현 착수) |
| 16폴드 walk-forward 2020-23 (`var/_analysis/wf_deep_US.csv`) | 평균초과 **+0.0010(무의미)** · **중앙값 −0.0071** · 폴드승률 **44%(7/16)** | ❌ wash |
| 레짐 분해 | 양(+)갭의 **74%가 상승반등 3폴드**(제거 시 평균 −0.76pp 반전) | ❌ 레짐운빨 |
| bear(하락 2폴드) | ridge 레그 IC **−0.24 반전** → ens −1.73% vs lgbm +1.41%(0/2승) | ❌ 베어-독성 |
| 변동성/꼬리 축소(앙상블 유일 근거) | **2026 2개월 sweep에만 존재, walk-forward선 반전**(ens std↑·최악폴드↓·IC_IR<lgbm) | ❌ 비재현 |
| 비용 | 진짜 앙상블 유리(턴오버 0.888<0.908) — 단 무의미한 gross갭 증폭일 뿐 | ⚠️ 기각사유 아님 |

> **또 단일 split 승자(ridge/ens)가 함정.** 16폴드 walk-forward + 6-에이전트 적대검증이 엣지를 **레짐운빨·위험조정 비재현·베어 꼬리리스크**로 폭로. ridge 단독은 최약(과거 "ridge 가짜승리"와 동종). **판정(confidence=medium): US 프로덕션 = pure LightGBM 유지(양시장).** "해롭다"가 아니라 *작동모델 교체 입증책임 미달 + 베어 꼬리비용* → **저후회 기각**. 번들 롤백(`production_US.joblib.pre_ensemble` 복원, WF IC 0.246 pure lgbm, recommend 515행 정상). 앙상블 코드는 옵트인 **shadow 툴**로 보존(`--ensemble` 기본 OFF 양시장), 2024-25+실제 드로다운 폴드 축적 후 재판정.

### 시점 16 — "시장갈림 기각" 레버 per-market 재감사 · 2026-07-10 (rank-IC 신기루 재적발)
시장별 특화가 정답이므로, *"한쪽에선 진짜 이겼는데 다른 쪽 때문에 교차기각된"* 레버가 있는지 전수감사(~30레버). **진짜 승리(IC/excess) vs 가짜(raw수익/베타틸트) 구분** 결과 후보는 **딱 2개, 둘 다 KR**(US쪽 0). 나머지는 per-market 프레임서도 정당히 닫힘(가짜승리/레짐운빨/기채택흡수).
| KR 후보 | 단일-pass rank-IC | **walk-forward top-decile EXCESS** (15폴드, ensemble급 바) | 판정 |
|---|---|---|---|
| baseline(mn+z) | 0.0152 | **+1.52%** · ExcSharpe **1.29** · Exc>0% **73%** | ✅ 유지 |
| **swabs_neu**(β중립+\|label\|) | **0.0229(↑!)** | +0.80% · 0.66 · 60% · winVsBase **27%**(4/15) · **−71bp** | ❌ 기각 |
| **mnmh**(5/21/63d 라벨) | 0.0099 | +0.93% · 0.60 · 60% · win 33% · −58bp | ❌ 기각 |
> **swabs_neu = winsor·ensemble과 동종 함정**: rank-IC는 진짜 올랐으나(0.0152→0.0229) **실제 롱하는 top-decile의 excess는 오히려 악화**(−71bp, 15폴드 중 4폴드만 baseline 승). IC 상승이 *전체 횡단면 순위*엔 있지만 *거래 바스켓*엔 없음. **⚠️ 번들 자체의 WF-IC(rank-IC) 지표로 판정했다면 오채택했을 것 — 채택 판정은 반드시 top-decile excess로.** mnmh는 IC조차 하락. **∴ 둘 다 기각, KR도 baseline(순수 lgbm) 유지.** 재검(harness `var/_analysis/wf_kr_levers.py`).
> **의의**: "알파 닫힘"이 이제 *옛 교차-일반화 가정*이 아니라 *시장별 강건 excess 재검증*으로 획득됨. 시장갈림 기각의 대부분은 애초 가짜승리였고, 진짜 IC 상승(swabs_neu)조차 거래지표선 무효.

### 시점 17 — 알파 성장 로드맵(4렌즈+적대가지치기) + 첫 강건 생존자 발견 · 2026-07-10
"모델 공간은 소진, 알파의 원천은 데이터/구조"라는 §6 전제 하에 4렌즈 발산생성→적대 가지치기. **C4(US 감쇠 해부)**: 상/하위 decile + 시장중립 스프레드 분해 → US "감쇠"는 **미결론**(2025-26 top-decile excess 겉보기 +1.02%이나 2026-02~04 꼬리집중, IC≈0, 스테일 6yr모델). ⇒ 감쇠-대응 모델 미정당, 진짜 병목 = **2024-25 데이터갭**(연속 재학습 백테스트 불가). **B5(순비용 rank 평활)** = 세션 **첫 강건 생존자**:
| B5: EWMA(rank) 평활 | grossExc | 턴오버 | netExc | winVsRaw | 판정 |
|---|---|---|---|---|---|
| US raw / ewma0.3 | +1.14% → +0.66% | 0.81→0.54 | +0.99% → +0.56% | **38%** | ❌ US는 평활이 신호 stale화(모멘텀-clean) |
| **KR raw / ewma0.5** | +1.18% → **+2.21%** | 0.86→0.62 | +0.70% → **+1.86%** | **69%** | ✅ **KR 채택후보**(gross+net 동시↑) |
> **✅ KR EWMA rank 평활(α≈0.5) = 첫 강건 생존자.** 16폴드 중 **11폴드서 raw 초과**(gross·net 모두), **best-2 폴드 제외해도 +0.45%**(C4·ensemble이 죽은 바로 그 집중검사 통과), **top-decile excess로 판정(신기루 아님)**. 기전=KR 신호가 노이지·반전성↑ → 이름별 rank를 시점간 EWMA로 **temporal 앙상블**하면 de-noise(gross↑)+턴오버↓(net↑). **US는 반대(평활이 해로움)** = 하네스가 무분별 채택 아님을 증명 + 시장별특화 재확인. 정직 유보: in-sample(2020-23), 특히 gross de-noise 효과는 라이브 확인 필요. 채택엔 **stateful 프로덕션 배선**(recommender가 사이클간 per-ticker EWMA rank 유지) 필요. harness `var/_analysis/{decay_decomp_us,wf_b5_smoothing}.py`.
> **로드맵 나머지**: 최대 상방=뉴스 I축(데이터게이트~8-10월)+2024-25갭 채우기; 구조베팅(레짐/규모 분할모델)은 C4가 감쇠 측정불가로 판정해 보류.
>
> **⚠️ 정정(같은 날, 배선 전 재검증): KR EWMA는 강건 생존자가 아니었음 — 미채택.** 위 "첫 강건 생존자"는 **단일 seed** 결과였고, 배선 전 **3-seed × α-스윕 재검증**(`var/_analysis/wf_b5_kr_robust.py`)서 무너짐: 위의 raw net +0.70%가 **약한 seed 뽑기**였고, 3-seed 평균 raw net은 **+1.43%**(≈ewma). α=0.5 net +1.63% vs raw +1.43% = **미미**, **winVsRaw 전 α서 ≤52%**(동전), **seed 불일치**(α0.5서 seed1은 raw보다 손해 +0.92 vs +1.39), H2선 raw가 우위. 채택바(대부분 α서 net>raw·win>50%·양 half·전 seed 동의) **전부 실패**. **∴ 미채택, 배선 취소.** 턴오버 감소(0.86→0.5-0.6)는 실재하나 순효과는 seed노이즈 내. **교훈: 단일-run "승자"도 seed 아티팩트일 수 있다 — 배선 전 seed/α/기간 재검증이 그것을 잡았다**(swabs_neu의 rank-IC 신기루 다음가는 함정 유형).
>
> **B1(레짐분할 KR모델)도 동일하게 미채택 — 또 단일-seed 아티팩트.** 단일run routed +2.10% vs pooled +1.18%(stress 5/5)로 유망했으나 **3-seed 재검증**(`var/_analysis/wf_b1_kr_robust.py`)서 **routed-pooled가 seed별 +0.00/−0.99/−0.52% = 2/3 seed서 손해**(winVsPool 56/25/44%). 그 pooled +1.18%도 약한-seed였고 3-seed pooled ~+1.91%. stress이득은 3-seed 모두 양수(+1.79/+1.10/+0.56%)로 방향성은 실재하나 **3/5 폴드·1폴드 집중(best 제외 +0.51%)**이고 calm폴드 손해(학습데이터 반감 분산)가 상쇄. → 미채택.
>
> **✅ C4v2 — 데이터갭 채운 뒤 진짜 감쇠답(`var/_analysis/decay_v2_us.py`, US 2018-2026 연속·매폴드 재학습).** C4는 스테일 6yr모델이라 못 갈랐음. 연속 재학습하니: **PRE-2024 top +1.44%/spread +1.29% ↔ 2024-26 top +1.71%/spread +1.88%/80% 폴드양수**(분기별 8/10 양수, 집중 아님). **단 IC는 여전히 ≈0**(2024-26 −0.018). **∴ US "감쇠"=스테일+rank-IC 메트릭 착시. 신선모델의 top-decile(거래대상)은 안 죽음** — 주간재학습(ON)이 US를 살림. US 감쇠-대응 모델 **불필요**. 드리프트모니터는 rank-IC 아닌 **top-decile excess** 추적할 것. (swabs_neu는 IC↑/excess↓, US감쇠는 IC↓/excess유지 — **둘 다 IC≠거래excess 교훈**.)
>
> **세션 순결론(정직)**: 시도한 6개 모델/실험 레버(swabs_neu·ensemble·mnmh·US평활·KR평활·B1) **전부 강건검증서 탈락** — 모델공간 알파는 소진(이제 가정 아닌 반증된 결과). 이 세션 진짜 소득 2개: ①**US 미붕괴 재확인(C4v2)** ②**US+KR 2018-2026 연속캐시 완성**(→최근창 재검증·뉴스 I축 즉시테스트 가능). **알파 성장 유일 경로=데이터 프론티어(뉴스 I축~8-10월+연속커버리지), 모델 영리함 아님.**

### 시점 18 — 유니버스 확장: **캠페인 첫 강건 긍정 — US 소형주에 진짜 IC** · 2026-07-14
"알파 천장은 신호가 아니라 유니버스가 정한다"(대형주=가장 arbitraged) 가설을 **동일 린 가격모델(20피처)+동일 바(walk-forward top-decile excess 3-seed)를 캡 스펙트럼에 적용**해 검증(`var/_analysis/{get_universes,universe_alpha_test}.py`, yfinance, 2018-2026). 각 ~350종목.
| 유니버스 | meanIC | **IC H1/H2(시기안정)** | net@cost | 판정 |
|---|---|---|---|---|
| US_LARGE(S&P500) | +0.0005 | **−0.017/+0.018(부호반전)** | +0.91%@20bp | ❌ IC≈0, excess=베타틸트 |
| US_MID(S&P400) | +0.012 | −0.016/+0.040 | +0.27%@35bp | ⚠️ H2한정 |
| **US_SMALL(S&P600)** | **+0.040** | **+0.018/+0.061(양half+)** | +0.31%@50bp | ✅ **강건 실스킬** |
| KR_LARGE(top200) | +0.042 | +0.001/+0.081 | +2.59%@20bp | ⚠️ 최근집중 |
| **KR_MID(200-500)** | +0.040 | **+0.025/+0.054(양half+)** | +0.66%@35bp | ✅ 강건 |
| KR_SMALL(500-1000) | +0.035 | +0.003/+0.065 | +0.59%@50bp | ⚠️ 최근집중 |
> **✅ 가설 확증(US): 선정 IC가 캡 사다리 내려갈수록 ~80배 상승**(대형 0.0005→소형 0.040). **US_SMALL만 두 하위기간 모두 IC 양수** = 유일한 강건 US 스킬. US 대형주 +1.0% excess는 IC≈0/부호반전 = **베타틸트(스킬 아님)** — 우리 US "감쇠"의 진짜 원인은 **가장 arbitraged된 유니버스 선택**이었음. **소형주로 내려가면 진짜 선정 스킬 존재.** KR은 전 캡 비효율(IC 0.035-0.042), KR_MID 최안정 — KR은 소형화 불필요.
> **정직 유보(배포 전 해결)**: ①**생존편향**(현재 S&P600 멤버십=과거탈락 제외→소형 수익·IC 위로 편향. 단 IC는 순위스킬이라 수익레벨보다 덜 민감—IC 발견이 더 신뢰). ②**비용**(순 +0.31%는 modest, 실 소형주 스프레드/임팩트 더 클 수). ③**린 20피처**(풀 484피처면 다를 수). **∴ 방향은 강건 확증, 배포 매그니튜드는 풀파이프라인+point-in-time 멤버십 필요.**
> **의의**: 6모델레버 전부 실패 후 첫 (겉보기) 강건 긍정 — 단 아래 정정 참조.
>
> **⚠️ 정정(같은 캠페인, 광범위 교차검증): US_SMALL은 큐레이션/생존편향 아티팩트 — 미검증.** 배포검증차 (1)리치피처(`universe_rich.py`, 42피처): 소형주 IC **미개선**(0.040→0.031, 가격피처 포화; **대형주는 −0.019로 과적합**). ⇒ 풀파이프라인 가치는 가격 아닌 **펀더멘털/정보축**(DB인제스트 필요). (2)**큐레이션 없는 광역 US(`US_BROAD`, 비지수 251종목): IC −0.018**(양 하위기간 음), 유동성플로어 50%서도 **≈0**(+0.0025). **∴ S&P600 IC 0.040은 지수 committee 선정(수익성·유동성·상장기간)+생존편향이 만든 구조지, "소형주 알파"의 일반성질 아님.** US는 큐레이션 밖에선 전 캡 효율적. **US 소형주 확장 = 미검증 알파경로(사실상 기각).**
>
> **✅ 대신 진짜 신호 = KR.** KR_MID는 **큐레이션 없는 순수 marcap 슬라이스**인데도 IC **0.042**(양 하위기간+ / 리치피처서도 +0.042 유지) — US_SMALL과 달리 큐레이션 비의존이라 신뢰도 높음. KR 전 캡 비효율(0.035-0.042)=문헌상 리테일주도 구조적 비효율. **알파 홈=KR 중/소형(홈마켓+데이터접근 우위).** 유보: KR도 생존편향(현재 FDR 리스팅)—PIT 확인 필요하나 uncurated 견고성이 US_SMALL보다 강함. **∴ 실행 우선순위 = KIS(KR), Alpaca(US) 아님.** harness `var/_analysis/{get_universes,universe_alpha_test,universe_rich}.py`.
> **campaign 순결론**: US 소형주도 아티팩트(패턴 재현). 유일 강건 신호=**KR 중형(uncurated)**. 알파 성장 실경로 = KR 중/소형 유니버스(PIT검증 후) + 뉴스 I축. 모델·US소형 아님.

### 시점 19 — **KR 소형주 알파 확정: 세션 유일 완전-강건 긍정** · 2026-07-14
user "A로 진행"(KR 굳히기). US를 죽인 **uncurated 딥테일 교차검증의 KR판** + 비용 스윕:
| KR 버킷 | IC | **IC H1/H2** | gross/기간 | net@100bp | net@250bp |
|---|---|---|---|---|---|
| **KR_MICRO(marcap1000-1600)** | **0.046** | **+0.047/+0.045** | +2.61% | +1.73% | **+0.40%** |
| KR_SMALL(500-1000) | 0.035 | +0.003/+0.065 | +1.47% | +0.59% | −0.73% |
| KR_MID(200-500) | 0.040 | +0.025/+0.054 | +1.27% | +0.40% | −0.04% |
| KR_LARGE(top200) | 0.042 | +0.001/+0.081 | +2.92% | +2.09% | +0.84% |
> **✅ KR_MICRO = 캠페인 최강·최안정.** US uncurated 딥테일은 IC **−0.018로 붕괴**했으나 **KR 딥테일(가장 uncurated junk-tier)은 IC 0.046, 두 하위기간 거의 동일(0.047/0.045)=전체 최강 시기안정성**, 250bp 왕복비용서도 net +0.40%(gross 2.61%가 커서 흡수). **∴ KR은 구조적 비효율 확정(큐레이션 아님) — US를 죽인 바로 그 검증을 통과.** KR 소형주 알파는 **3-seed·양 하위기간·리치피처·uncurated딥테일·비용스윕 전부 통과 = 세션 유일 완전-강건 긍정.**
> **소액 적합성**: micro/small-cap 알파는 기관 용량제약으로 못 건드림 → **소액(10-30만원)이 온전히 접근 가능**(사용자 목표에 최적 fit).
> **정직 유보**: ①생존편향(micro일수록↑, KRX 상폐데이터 login-wall→무료 완전보정 불가) ②실 KR 소형주 비용/유동성/±30%상한·거래정지(백테스트 미모델). **둘의 결정적 해소=KIS로 라이브 유니버스 forward 페이퍼트레이드(생존편향 원천제거+실비용실측).** harness `var/_analysis/universe_alpha_test.py`(KR_MICRO/cost sweep).
> **∴ 실행경로: KIS 어댑터→KR 소형주 페이퍼(survivorship-free 확증)→소액 라이브.** 배포모델=풀 484피처 KR 소형(DB인제스트 후) or 검증된 린 모델.

### 시점 20 — 완전성 감사 + kill-or-confirm 게이트: **KR micro는 데이터아티팩트, 진짜 엣지는 liquid KR(mid/large)** · 2026-07-20
user "성능 불만족, 모든 가능성 닫았나 전체 체크". **완전성 감사(5-critic 워크플로)**: 시점18-19 피벗이 이전 ~30레버 기각을 "틀린 유니버스"서 한 것으로 만들어 **올바른 유니버스 기준 실탐색 ~25%뿐, ~75% 열림**. 그리고 KR_MICRO는 US승자 죽인 적대검증을 안 거침 지적. → **kill-or-confirm 게이트 실행:**
- **게이트1 적대배터리**(캐시, best-2제외·conc5·bear): KR_MICRO **통과**(best-2 +1.90%·conc5 55%·bear +0.85%) — 캠페인 유일 완전통과. **그러나 같은 yfinance 데이터라 데이터아티팩트 못 잡음.**
- **게이트2 2차 가격소스(pykrx, KRX직접, `get_pykrx_prices.py`)**: **KR_MICRO IC 0.046→0.017·excess +2.68%→+0.48% 대폭 디플레이션(~2/3가 yfinance 마이크로캡 stale-price 아티팩트).** winrate 68%→52%. **∴ KR micro "확정 엣지"는 대부분 데이터아티팩트 — micro 기각.**
| 버킷 | yf IC | **pykrx IC** | pykrx excess | pykrx best-2 | pykrx bear |
|---|---|---|---|---|---|
| KR_MICRO | 0.046 | **0.017** ✗ | +0.48% | +0.12% | +0.11% |
| **KR_MID** | 0.040 | **0.037** ✓ | +1.53% | +0.85% | +0.81% |
| **KR_LARGE** | 0.042 | **0.030** ✓ | +2.15% | +1.46% | +1.26% |
> **✅ 디플레이션은 micro-특이(데이터품질): liquid KR(mid/large)은 pykrx서 재현** — KR_MID IC 0.037/excess +1.53%(더 높음)·best-2 +0.85%·bear +0.81%, KR_LARGE IC 0.030/excess +2.15%. **진짜 데이터소스-강건 KR 엣지=liquid mid/large**(더 유동적→비용생존·거래가능). **단 clean data선 H2-loaded**(2021-26 강, 2018-21≈0)=최근레짐 편향. **그리고 IC 0.03-0.04는 대략 기존 프로덕션 수준** — "spectacular"(US큐레이션·KR micro yfinance)은 전부 아티팩트였음.
> **완전성 정직판정**: 모델영리함 소진 확정, KR 방향 확인, 단 매그니튜드는 기존과 유사(modest). **미탐색 실개선 프론티어(liquid KR 대상)**: DART 펀더멘털+PEAD(무료·가능), 단기호라이즌(5/10d), 턴오버제어 — 전부 genuine 미테스트. **수급(foreign/institution net)=최고 mechanism prior이나 KRX-walled 재확인(DB컬럼 100% NULL, pykrx 수급 endpoint 빈값).** 남은 게이트: 생존편향(상폐 PIT)·거래가능 유니버스. harness `var/_analysis/{wf_kr_adversarial,get_pykrx_prices}.py`.

### 시점 21 — "모든 업무" 개선레버 스윕(clean liquid KR): **✅ H42(42일 호라이즌)=유일 강건 개선** · 2026-07-20
user "리스트업 가능한 모든 업무 수행". clean pykrx KR_MID/LARGE에 전 개선레버 스윕+적대검증(`var/_analysis/{kr_liquid_sweep,kr_model_levers,kr_verify}.py`):
| 레버 | 결과 |
|---|---|
| **H42(42일 라벨/스텝)** | ✅ **채택**: IC 거의2배(KR_MID 0.034→0.040/KR_LARGE 0.023→0.044), net ~2배(+0.77→+2.67% / +1.96→+3.41%), **best-2 +2.14/+2.89%·conc5 46-49%(diffuse)·양시장 통과.** 단기(H5)는 실패(mid/large=모멘텀). 트레이드오프=**bear 약함**(방어 오버레이로 커버). |
| invvol/invamihud 가중 | ❌ **집중 아티팩트**: conc5 89-92%, invamihud −2909% 블로업. 동일IC=순수가중효과=1/작은분모 집중. |
| 모델레버(ens_ridge/regime/ewma) | ❌ 올바른 유니버스서도 약함: ens_ridge win 44%, regime=rank-IC신기루(IC↑excess↓), ewma=시장의존 marginal. **model소진 재확인.** |
| 집중도(topN/DEC0.95) | ~ 혼조(엣지 diffuse=집중 손해). 크로스에셋베타=plumbing실패(보류). |
> **∴ 세션 유일 강건 개선=H42.** 이것도 spectacular 아님(modest 기반의 ~2배)이나 적대검증(best-2·conc5·bear·양시장) 통과한 진짜 이득. **배포권장: 라벨/호라이즌 21d→42d + TREND×VOL 방어(bear 커버).** 남은 미실행(feasible): 거래가능유니버스·생존편향 게이트, DART펀더멘털+PEAD, 타시장(대만). 데이터게이트: 수급·뉴스I축.

### 시점 22 — "미실행 업무 모두 수행": 잔여 전 게이트·프론티어 소진 · 2026-07-20
user "미실행 업무 모두 수행+추가검토+전체 커밋". 잔여 전부 실행(`var/_analysis/{kr_realizable,kr_survivorship,kr_fundamentals,kr_betas}.py`, universe_alpha_test TW):
| 업무 | 결과 |
|---|---|
| **거래가능 유니버스(H42+필터)** | ✅ **배포가능 확인·개선**: minPx≥1000+유동성floor40%+상한가no-fill → net +2.67%→**+4.05%**, best-2 +3.38%, pos 65%(단 decile 27→16명 집중). 거래불가 junk 제거가 오히려 net↑. |
| 생존편향(FDR 상폐 PIT) | ⚪ 929상폐/2018-26(~4%/년), **pykrx가 상폐종목 OHLCV 미제공→full PIT 차단**. 상폐는 junk tail 집중→liquid mid/large는 덜 노출(잔여편향 ~4%/년 상한). |
| 타시장 전이(대만) | 🟡 **TW 소형 IC +0.048(KR급)** = 리테일-비효율 thesis 지지. 단 yfinance(마이크로 아티팩트 caveat 재현 위험)·H2-loaded·103종목뿐→2차소스 확인 전 미배포. |
| **DART 펀더멘털(최대 미개척축)** | ⚪ **KR_LARGE(financial_facts 177/200)에 ROE/ROA/margin/gross-prof/accruals/leverage/growth PIT조인**: delta −0.22% excess/win 51% = **무효**(IC 0.045→0.046 미미, H1/H2 더 균형이나 총이득 없음). 대형주 펀더는 이미 arbitraged(프로덕션도 사용). 소형주 펀더 프론티어는 DART 인제스트 필요(미검). |
| 크로스에셋 베타 | ❌ macro merge 0% 커버리지+delta 음수 = 비결과. 섹터중립=섹터맵 plumbing 미해결(보류). |
| 데이터게이트(재확인 차단) | ⛔ 수급(KRX-walled), pykrx 펀더/PIT티커리스트(KRX-walled), KR뉴스 I축(백필대기). |
> **추가검토 결론: feasible 리서치 공간 소진.** 유일 실개선=H42(realizable +4.05%). 다음 실질 진전 3택: ①**H42를 프로덕션 반영**(라벨 21d→42d 재학습, 배포단계) ②KIS 페이퍼로 forward 검증(생존편향 원천제거) ③데이터게이트 해소(KRX 로그인=수급/PIT, DART 소형주 인제스트, 뉴스백필). **정직 총평: modest한 liquid-KR 엣지(IC~0.03-0.04)에 H42로 net~2배·realizable +4.05%가 이 캠페인의 실질 최선. 모델·포트폴리오·피처 영리함은 완전 소진.**

### 시점 23 — H42 프로덕션 검증 → **미전이(train_production 무변경)** · 2026-07-20
"진행해봐"(H42 프로덕션 반영). 배포 전 **풀 478피처 프로덕션 모델·프로덕션 유니버스(KOSPI200+KOSDAQ150)·연속 2018-2026 캐시**서 21d vs 42d vs 63d 검증(`var/_analysis/wf_prod_horizon_kr.py`, 42d는 pykrx close 조인 91% 커버):
| 호라이즌 | folds | ic | net_ANN | best-2 | bear |
|---|---|---|---|---|---|
| **H21(프로덕션 base)** | 75 | 0.0089 | +12.3% | +1.09% | **+0.35%** |
| H42 | 37 | 0.0166 | +7.5% | +0.64% | −0.65% |
| H63 | 25 | 0.0083 | +15.1% | +2.38% | −0.83% |
> **❌ H42(및 장기호라이즌)는 프로덕션서 21d를 강건하게 못 이김.** (1)신호 상충: IC는 H42최고이나 net_ANN은 H21이 H42보다 높음, H63는 net높으나 IC=base. (2)**장기호라이즌은 bear 손실**(H42/H63 음수 vs H21 +0.35%). (3)**레짐 불안정**: 2018-23선 H63 bear +2.72%였으나 2024-26 추가하니 −0.83%로 반전. **∴ 프로덕션 라벨 무변경.** 기전=린 20피처 모델은 긴 호라이즌으로 sparse함을 보완했으나, 풀 478피처 프로덕션은 21d서 이미 신호추출 충분→호라이즌 무익. **H42는 린-모델/marcap-슬라이스 특정 개선이지 범용 아님.** 검증-우선이 또 premature 배포를 막음(세션 패턴).
> **∴ 최종 정직 현주소**: 프로덕션 모델은 이미 achievable 지평 근처. **un-shipped 강건 개선 없음.** 진짜 다음 진전=리서치/모델 아닌 **①KIS 페이퍼 forward 검증 ②데이터게이트 해소(수급·뉴스·소형주 DART=KRX로그인/백필/인제스트).**

### 시점 24 — 🔴 **전수 재감사: 인큐번트를 자기 바에 세우다 → 프로덕션 레시피 부분실패 + KR 개선레시피 도출** · 2026-07-22
"모든 항목 전수 재시도"(H1 구멍 = *채택된 레시피는 정작 late-gate 적대배터리를 한 번도 안 거쳤다*). 통일 프로토콜: clean 연속 2018-2026 · top-decile 순초과수익(net-of-cost) 1차 · 3-seed+best-2+conc5+bear+split-half. **인큐번트 각 레버를 ablation**(빼서 손해나면 정당, 아니면 무익). `var/_analysis/wf_incumbent_kr.py {KR|US}`.

**A/B — 인큐번트 ablation (제거 시 net 변화, 풀 478피처):**
| 레버 | KR | US | 재판정 |
|---|---|---|---|
| **per-date 정규화** | **−1.07%p (빼면 개선)** | −0.29%p (빼면 손해) | ⚠️ **시장-분리**: US 정당·**KR 역효과**(전역채택이 KR엔 오류) |
| **\|label\| 가중** | −0.20%p (손해) | −0.14%p (손해) | ✅ **정당(양시장)** — 유일하게 명백 |
| **Blitz 피처** | +0.23%p (빼면 개선) | +0.10%p (빼면 개선) | 🟡 **미정당(양시장 중립~해로움)** |
| **top-50 선택** | +0.04%p (≈all-478) | −0.07%p (중립) | ⚪ **중립** |
| mn vs rank | rank net +0.90 | rank net +0.65(bear −2.15%) | ~ mn=bear보호(US), rank=높은 net |
> **5개 레버 중 명백히 정당한 건 `|label|` 가중 하나뿐.** 정규화는 KR서 역효과, Blitz·top-50은 양시장서 제 몫 못 함. **우리가 운용하는 레시피가 우리가 죽인 챌린저보다 덜 검증됐었다** — H1이 예측한 그대로 실측 확인.

**Payoff — KR 레시피 재도출**(`reaudit_kr_recipe.py`, split-half+50bps 게이트, adopt iff net@30·net@50·best-2·**양반기** 모두 base 초과):
| recipe | net@30 | net@50 | best-2 | bear | H1(18~22) | H2(22~26) | 판정 |
|---|---|---|---|---|---|---|---|
| base mn+norm+blitz | +2.17% | +1.83% | +1.59% | +1.80% | −0.31% | +4.46% | BASE |
| no-norm 단독 | +2.57% | +2.23% | +1.94% | +0.85% | **−0.80%** | +5.69% | ✗ H1 탈락(레짐취약) |
| no-norm +rank | +2.24% | +1.91% | +1.62% | +0.37% | −0.75% | +5.01% | ✗ |
| **no-norm −blitz** | **+2.84%** | **+2.50%** | **+2.41%** | +1.37% | **+0.07%** | +5.40% | ✅ **ADOPT** |
| no-norm +rank −blitz | +2.53% | +2.19% | +1.88% | +0.41% | −0.83% | +5.62% | ✗ |
> **`no-norm −blitz`(정규화+Blitz 동시제거)가 유일하게 전 게이트 통과** — net +2.17→**+2.84%**, 두 반기 모두↑, 50bps 생존, best-2↑. **split-half가 결정적**: no-norm *단독*은 전체샘플만 이기고 H1(2018-22)서 짐(레짐취약); Blitz까지 빼야 강건(두 레버 상호작용). **rank는 전부 H1 탈락** → ablation 헤드라인("rank +0.90")이 split-half서 해체, 엄격게이트가 제대로 걸러냄. 정직: ①bear 소폭악화(+1.37 vs +1.80, 양수유지, 게이트조건 아님) ②경량하네스(분기·40k서브) 결과라 **배포 전 풀피델리티(step21·전행) 확인이 ship-gate**(맨끝 백그라운드 배치).

**E — 기각모델 재확인**(`wf_rejections_kr.py`, clean KR 전배터리): ridge/ExtraTrees/LTR/lgbm+ridge앙상블 **전부 여전히 기각**. lgbm이 IC(0.0185)·bear(+1.48) 최고 = **learner 선택은 정당**. ExtraTrees는 raw net만 높으나(+2.31) best-2·IC·bear↓·conc5 71% = 집중아티팩트를 배터리가 잡음. → **인큐번트의 learner(lgbm)는 정당·recipe 레버(정규화KR·Blitz)는 부당**, 재감사가 둘을 정확히 분리.

**C/D/F — 잔여 재감사:** C(생존자) liquid KR full battery = A base로 **강건 재확인**(best-2·bear·50bps 생존); US소형=universe-curation 아티팩트 결론 유지(2차소스 무관하게 forward-curated 유니버스라 미배포). D(방어 TREND×VOL)=위험/방어 레버라 알파축과 직교, 시점9 상태 유지. F=베타(0%커버 비결과)·DART소형(인제스트 필요)·대만(yfinance아티팩트 caveat)=데이터게이트/비결과로 재시도 불가.

> **시점 24 결론:** 재감사는 (1)인큐번트 recipe가 자기 바 부분통과 실패를 **확정**하고, (2)그 부산물로 **실측 더 나은 KR 레시피(no-norm −blitz, net +0.67%p, 강건)** 를 도출함. learner(lgbm)·`|label|`·US정규화는 정당 재확인. **다음 실질 진전 = no-norm−blitz 풀피델리티 확인 통과 시 KR train_production 반영**(정규화 off + Blitz drop, per-market). 검증-우선 규율이 이번엔 *인큐번트 자신*을 걸러냈다.

### 시점 25 — 🔵 **패러다임 전환: "범용 알파"는 틀렸다 + 기각도 Type II 통제 → 효율성-구배·행동알파 재검토** · 2026-07-22
사용자 2대 논점: ①"지표 나쁘다고 배제 금지 — 배제도 철저 검증해야"(gauntlet이 Type I만 통제·Type II 방치: 게이트 7개×15% 오기각 → 진짜알파 68% 사망). ②"여러 유니버스 범용 알파는 애초에 말이 안 됐다." 둘 다 **옳음.** 옳은 축 = cross-universe 일관성(❌) → **유니버스-특정 메커니즘 + 유니버스내 시간축 강건성 + 효율성-구배 단조성(위조 어려운 양성검증)**.
- **T1 효율성-구배**(`efficiency_gradient.py`, 7유니버스 US대형→KR마이크로→TW, gross rank-IC): **thesis 확인.** LOTTO(복권회피)가 **부호 전환**(US대형 −0.040 → KR마이크로 +0.122), ILLIQ(+0.019→+0.145)·REV_1M(+0.002→+0.069) **구배 단조↑**. **모멘텀은 어디서도 죽고 KR마이크로선 음수(−0.031)** = 인큐번트 "Blitz 미정당"의 메커니즘 설명(KR은 반전지배). **KR_MICRO가 clean pykrx서 신호 최대** → 시점20 "데이터아티팩트" 기각은 *ML복합*엔 맞았으나 *행동신호*엔 틀림(사용자 point1 실증).
- **T2 비용후 거래가능성**(`behavioral_tradeable.py`, 유니버스별 현실비용): **ILLIQ만 생존.** KR_MID net **+1.48%**(gross 1.77, 회전37%)·best-2 +1.53·bear +2.81 전부+. REV/LOTTO는 gross 컸으나 **회전 93/75%로 비용 전멸**(gross-IC 신기루를 비용이 도살 — 규율이 신규발견에도 적용). 순진 COMPOSITE도 ILLIQ 단독만 못함.
- **T2b ILLIQ 강건성**(`illiq_robust.py`): KR_MID **100bps까지 net+**(+1.03%)·**양반기+**(H1 +1.44/H2 +1.21, no-norm−blitz가 실패한 H1 통과). 단 **size-중립화 시 반토막**(+0.96%)·H1 −0.17% → 엣지 ~절반이 소형주 프리미엄, 순수비유동 잔차는 marginal.
- **T4 slow-char**(`behavioral2_slowchar.py`, 저변동/저이디오변동/저베타/52주고점): **대체로 말람.** 저변동계열=raw알파 아님(bear+만=방어재료). HI52=KR_MID서만 겨우+(+0.40%·회전72%). **ILLIQ standout 유지, 과대발견 안 함.**
- **R4 cross-asset 베타 재검토**(`kr_betas.py`, ffill 버그수정): 커버리지 **0%→94%**(사용자 지적대로 원래 버그). 공정 재테스트 → base IC 0.034→base+beta 0.026, **delta −0.86%**(노이즈 추가). **∴ 버그 아닌 진짜 근거로 재기각** — "배제도 철저 검증 후 배제" 준수.
**T3 통합/직교성**(`orthogonality_lite.py`+`integration_illiq.py`): ILLIQ가 **프로덕션 유니버스(KOSPI200+KOSDAQ150)서도 강함** — amihud raw 틸트 net **+2.53%**(회전20%·양반기+·bear+1.58). *결정적 메커니즘 발견*: **per-date 정규화가 비유동성 프리미엄을 파괴** — AMIHUD-ONLY 모델이 정규화 시 +0.25%(raw 2.53%의 **90% 소멸**, amihud_illiq_z가 이미 z인데 이중정규화), no-norm 시 +1.23% 회복. 모델 안 정규화된 amihud는 노이즈(FULL-norm +1.61 < NO-AMIHUD-norm +2.45). no-norm이 FULL 개선(+1.61→+2.42)·H1 극적(0.13→2.16). **⚠️ 정직 walk-back**: 잘설정(no-norm) 가격모델 +2.42% ≈ amihud틸트 +2.53% → **ILLIQ는 모델압도 단독알파 아님**(step21/63 교란 잔존). **∴ 진짜 통합레버 = "KR 정규화 제거"** = 지배적 실제프리미엄(비유동성/반전) 복원. **⚠️ 방법론 교훈**: 첫 통합테스트서 net +30% 나옴 → `vol_adj_ret_21d`(IC 0.94 w/fwd=누출컬럼, 프로덕션 ALL_FEATURE_COLS엔 없음)를 내가 오적재. "불가능한 숫자=아티팩트" 규율로 즉시 적발·제거.
> **시점 25 결론:** 사용자의 두 방법론 비판 **모두 실측 확인**. (1)유니버스-특정·효율성구배 프레임이 옳음(범용알파 폐기). (2)KR_MICRO는 잘못 기각했었음(행동신호 real). **비유동성(amihud) 프리미엄이 KR 지배적 실제알파**(틸트 +2.53%, 방어적)이나 — **핵심은 프로덕션 정규화가 이걸 90% 파괴**한다는 것 → **시점24 no-norm−blitz에 메커니즘 부여**: 정규화 제거 = 비유동성/반전 복원(틸트/모델 무관 동일방향). ILLIQ 단독은 모델과 대등(마법아님). REV/LOTTO=회전비용사망(피처후보), 저변동=방어재료, 베타=공정기각. **다음: no-norm 풀피델리티 확증(배치중) + rank ex-COVID(R2) + N2/N3.**

### 시점 26 — ✅ **재감사의 프로덕션 반영: KR = no-norm − blitz (per-market) 코드화** · 2026-07-23
시점24-25 결론을 실제 학습코드에 반영. 절차:
- **ship-gate(step21 풀피델리티, `reaudit_kr_recipe.py lean`)**: no-norm−blitz가 base를 net@30(+1.75→**+2.19%**)·net@50·best-2(+1.94→+2.34)·**양 반기**·**bear(+0.64→+0.77)** 전부 초과 → ADOPT. (step63 재도출이 step21서 확증, bear 우려도 해소.)
- **실 파이프라인 검증**(`train_production.py`, top-50 재선택+WF, temp출력·라이브 무터치): OLD(norm+blitz) vs NEW(no-norm−blitz), 동일 2018-24 캐시 →
  | metric | OLD | NEW |
  |---|---|---|
  | rank-IC (WF) | +0.0335 | +0.0223 (std↓ 0.037→0.026) |
  | **decile long-short (WF)** | +0.0218 | **+0.0507 (2.3배↑)** |
  | band coverage | 0.837 | 0.813 |
  > **IC 신기루의 역상**: IC는 하락하나 **거래하는 decile long-short는 2.3배**. 시스템은 top-decile을 거래하므로(inference rank_pct≥0.9→BUY) 거래-우선 도리(decile>IC, 다시점 확립)상 NEW 우수. best-2·양반기·bear+로 concentration 아티팩트 아님 확인. **정직 caveat**: 번들 headline rank_ic가 떨어져 IC 모니터링 시 오독 위험(단 캠페인이 기각한 metric).
- **코드 변경**(`train_production.py`): `normalize` per-market 기본값(**KR off·US on**) + **KR Blitz drop**(선택 전 후보풀서 제거) + `--out`(안전 검증). `--normalize`/`--keep-blitz`로 구 동작 복원 가능(가역). 추론(`production_inference.py`)은 `normalize=None`/feature_cols를 이미 올바르게 처리 → **추론 코드 무변경**. 구 "정규화 validated ON" 주석을 재감사 결과(시장분리·비유동성 파괴 메커니즘)로 갱신.
> **시점 26 상태**: 코드 반영 완료(가역). **라이브 배포(production_KR.joblib 리트레인 교체)는 미실행** — 실매매 모델 교체라 사용자 승인 대기. US는 무변경(정규화 유지).

### 시점 27 — 🔵 **유니버스 특화: ILLIQ(비유동성)이 전 KR 유니버스 지배 거래가능 신호** · 2026-07-23
"거래가능 유니버스 내 신호 특화 + 각 gauntlet·비용·용량 검증"(사용자). 전 방법 리스트업(A신호15+펀더13·B결합·C라벨·D전처리·E검증·F유니버스별) 후 9유니버스 전수(`universe_specialize.py`, full gauntlet+비용+용량+2차소스).
| 유니버스 | 특화신호 | net | 용량$ADV | 2차소스 | 판정 |
|---|---|---|---|---|---|
| **KR_LARGE** | **ILLIQ63** | **+2.13%** | **$1.4B** | ✅재현 | ✅**최적(알파×용량×방어)** |
| KR_MID | ILLIQ126 | +1.88% | $236M | ✅재현 | ✅강함·bear+3.1 |
| KR_MICRO | ILLIQ126 | +1.64%@120bps | $52M | ✅재현 | ✅real·극소용량 |
| KR_SMALL | SIZE/ILLIQ | +1.48% | $56M | ⚠️yf만 | 🟡2차소스대기 |
| US_LARGE | (478 밸류/퀄/센티) | ILLIQ +1.12%(bear음수) | 거대 | — | 🟡엣지=기존478 |
| US_SMALL | ILLIQ | +1.92% | $2.7M | ⚠️큐레이션 | 🟡용량극소 |
| US_MID | 없음 | best-2 +0.35% | — | — | 🔴밈집중(GME/CELH/ARWR) |
| US_BROAD | 없음 | — | ~$0 | — | 🔴제로거래량 |
| TW_SMALL | HI52 | +0.64% | $22M | ⚠️yf만 | 🟡약함·미확인 |
> **버그수정**: ILLIQ가 `g.apply().reset_index(drop)`로 인덱스 misalign→`transform`으로 교정(smoke서 KR_MID +0.36%→+1.48% 복원, 이전 검증치 일치). smoke+과거대조가 버그 적발.
> **핵심**: **ILLIQ(장기 63/126d window)이 전 KR 유니버스서 지배·2소스 재현·full gauntlet 통과.** 특화=유니버스 용량에 맞춘 사이징(KR_LARGE $1.4B sweet-spot→micro $52M). 장기window가 회전↓net↑. **lgbm 결합은 회전80%로 비용전멸** → 저회전 단일틸트가 net 승. **2차소스 재현이 KR_MICRO 최종해소**: ML복합은 아티팩트였으나 mechanism ILLIQ는 양소스 real(오기각 인정 확정). **펀더멘털(DART)**: liquid KR서 밸류/퀄=arbitraged, 성장/LEV mild(ILLIQ 미달)이나 대용량 다변화 sleeve.
> **시점27 결론**: 유니버스 특화의 답=**ILLIQ를 용량에 사이징**. US는 price-ILLIQ 약함→기존 478모델이 엣지. **다중검정 haircut**: ILLIQ는 다유니버스+2소스+gauntlet 동시통과라 위양성 불가(강건), 산발단일통과(TW HI52 등)=미확인. caveat: ILLIQ 절반 size·비유동성위험 보상·backtest뿐.

## 4. 한 줄 결론
매 시점 **수익 1위는 전부 가짜**(ridge_raw 21→ridge 27→et 21.7→w3 시장모순). IC·bear·집중도·step·
cross-market·**ablation**을 기준에 더할 때마다 랭킹이 뒤집혀, 화려한 숫자가 차례로 탈락하고 **mn 라벨 +
per-date 정규화** 두 레버만 살아남아 프로덕션에 반영됨. *숫자 크기가 아니라 검증 기준의 두께가 승자를 결정한다.*
> ⚠️ **시점 24 갱신:** 그 "생존 2레버"조차 late-gate 재감사선 갈림 — **`|label|`·US정규화는 정당하나 KR정규화는 역효과, Blitz는 양시장 미정당.** KR 개선레시피=**no-norm −blitz**(풀피델리티 확인 대기). 재감사 규율은 인큐번트도 예외로 두지 않는다.

## 5. 최종 채택 현황
| 레버 | 상태 | 시점24 재감사 |
|---|---|---|
| **mn (시장중립 잔차 라벨)** | ✅ 프로덕션 | ~ US=bear보호로 정당·KR=rank가 높은net(단 rank는 split-half 탈락→mn 유지) |
| **per-date 횡단면 정규화** | ✅ 프로덕션(양시장) | ⚠️ **US 정당·KR 역효과** — KR은 off 권고(풀피델리티 확인 대기) |
| **Blitz residual momentum (신규 피처)** | ✅ 활성화 (2026-06-18) | 🟡 **양시장 미정당** — KR은 drop 권고(no-norm과 동시) |
| **\|label\| 샘플가중 (mn_swabs)** | ✅ 활성화 (2026-06-19) | ✅ **정당 재확인(양시장, 제거 시 손해)** |
| **triple-barrier 라벨** | ✅ US만 활성화 (per-market, 2026-06-20) | (재감사 미포함) |
| **learner=LightGBM** | ✅ 프로덕션 | ✅ **정당 재확인** — ridge/ET/LTR/앙상블 clean 전배터리서 전부 재기각, lgbm IC·bear 최고 |
| **★ KR: no-norm −blitz** | ✅ **코드 반영(시점26, 가역)** — step21 ship-gate PASS·실파이프라인 decile-LS 2.3배↑ | `train_production.py` per-market 기본값. **라이브 리트레인/배포는 사용자 승인 대기.** US 무변경 |
| 그 외 전부(ridge/MLP/LSTM/xgb/cat/et/rank/winsor/conv/regime류/조잡잔차/w5/recency/decile/팩터중립화/다호라이즌/Optuna튜닝/LTR·lambdarank/**US lgbm+ridge 앙상블**) | ❌ 기각 | ridge/ET/LTR/앙상블 시점24 clean 재기각 확인 |

> **공통 4개 + US 전용 1개(tb)** 채택, 나머지 전부 기각. 번들 WF IC(라벨무관 실현수익 기준):
> KR 0.0387→**0.0440**, US 0.0289→**0.0407**(100% 양수fold). 잔존 미테스트: meta-labeling(사이징,
> IC외 eval), uniqueness 가중 — [GAPS.md](GAPS.md) §X.
>
> **시장별 특화 = 정답(2026-07-10 확정, excess로 증명)**: cross-market은 "양시장 동일승자"가 아니라 각 시장 **초과수익**(베타제거 선정가치)으로 판정 — 동일 ridge가 US-최고/KR-최악이었음. 번들의 per-market 분리(US=tb/KR=mn) 구조는 원래 옳았음. US 프로덕션은 **pure LightGBM**(앙상블 기각, 시점 15).
>
> **별도 축 — 방어 오버레이(TREND×VOL-SPIKE, 2026-07-09 채택)**: exposure=regime×TREND_mult×VOL_mult로 drawdown 반감(buy&hold −44%→−18%, KR Calmar 0.92/US 0.80). 이는 **선택(알파)이 아니라 위험/방어 레버**라 본 채택현황(알파)과 별개(정직: EW시장 오버레이 기준·거래비용 미모델).

### Blitz 활성화 기록 (2026-06-18)
- 배선: `features_advanced.compute_stat_features`에 resid_mom_blitz_12m/6m 추가 + ALL_FEATURE_COLS 등록(추론 자동 보유). 15h 전체 재빌드 대신 `augment_blitz.py`로 **동일 함수·동일 SPY 프록시** 사용해 캐시에 blitz만 병합(train/infer 일관).
- 재검증(파이프라인판): mn_norm IC **US 0.0249→0.0278, KR 0.0049→0.0071**(둘 다↑), 집중↓.
- 번들 재학습: blitz가 importance로 top-50 선택(KR 33/34위, US 26위). **KR WF IC 0.0387→0.0412(↑)**.
  ⚠️ 정직히: **US 번들 WF IC 0.0289→0.0242(↓, 단 std 0.026 내 노이즈)** — 지표 간 엇갈림. KR 명확↑/US 중립.
- 추론 smoke: 양시장 blitz 피처 존재+예측 정상(US BUY52/KR BUY32, NaN 0). 활성화 확인.
