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
| **triple-barrier 라벨** | ✅ **US만 활성화 (per-market, 2026-06-20)** |
| 그 외 전부(ridge/MLP/LSTM/xgb/cat/et/rank/winsor/conv/regime류/조잡잔차/w5/recency/decile/팩터중립화/다호라이즌/Optuna튜닝/LTR·lambdarank/**US lgbm+ridge 앙상블**) | ❌ 기각 |

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
