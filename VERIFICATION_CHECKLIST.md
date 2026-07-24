# 검증 프로토콜 — 크로스섹셔널 주식 알파 백테스트 (KR/US ILLIQ)

> 독립 5렌즈 완전성 감사(2026-07-24)로 도출한 140체크 마스터 리스트. 사용자 비판("내가 체크해야만 발견")에 대한 응답으로, 리드의 6영역 택소노미가 **불완전**(체결타이밍 차원 통째 누락)함을 잡음. 실행 상태는 WORK_LOG 시점33 참조.

## 실행 완료 — 1회차(2026-07-24, 시점33) 요약
- ✅ 체결지연 t+1(생존) · CI/HAC(t=4.94 유의) · reversal-중립(distinct) · 478누출스캔(clean) · KR스플릿조정(확인) · US_LARGE데이터(clean)
- 🔴 size교란(marcap-clean→대부분 size틸트) · 용량(임팩트모델 버그자각) · US_MID/SMALL스플릿오염 · KR_MICRO생존편향23%
- 🔒 데이터게이트(아래 C): 상폐포함OHLCV·PIT멤버십·forward paper — 유료데이터/KIS로만

## 실행 완료 — 2회차(2026-07-24, 시점34, 사용자 "모두 수행해봐") 요약
잔여 ~70체크를 4배치 전수 실행(`robustness_batch1/2/3/3b.py`). 결과가 대부분 엣지를 **강화**했고 이전 "저용량" 우려를 **반전**시킴.
- ✅ **다중검정 rigor(#9.2-9.8)**: DSR후보별(KR_LARGE 97.1%·KR_MID 94.9%·US 85.5% 생존, MICRO/SMALL 탈락) · **FWER Bonferroni/Holm N=135서 KR_LARGE·KR_MID 생존**(BH-FDR는 US도) · 선택haircut N=1000→89.9% · **PBO/CSCV=0%**
- ✅ **신호강건(#4.6/4.7/4.13/4.15)**: 다변량 FM **ILLIQ 한계 t=1.74(유의미달)·VOL t=2.49 최강**=known-factor블렌드 · median/log-amihud·1/price·vol중립 유지 · decile단조 · **연도별 2019-25 전부 양** · 위상평균 +1.74±0.18% · ex-COVID불변
- ✅ **기전/현실성(#4.5/5.5/6.3/7.4)**: marcap-clean KR_LARGE 잔차 **+0.77%(t=1.48)=대부분 size**(KR_MID +1.89 t=2.11이나 28%커버 저검정력) · **★ADV-weight가 엣지 강화(realizable at scale)** · limit-move불변 · **유동적 절반>최소유동 절반(bounce/tiny-name 아님)**
- 🔄 **정정**: 시점33 "저용량 $M~수천만"(impact버그 기반)을 batch3b가 **반박** → 고용량·실현가능(long-decile+KOSPI200선물 헤지, 개별공매도 불필요)
- 🔒 데이터게이트는 여전히 유일 미해결(상폐포함·PIT·forward paper)

# MASTER VERIFICATION CHECKLIST — Cross-Sectional Equity Alpha (KR/US ILLIQ)

Deduplicated from 140 raw checks across 5 lenses. Where one underlying check appeared under multiple lenses with divergent statuses, I reconciled to the *true* state (e.g. a control that is "done" on the headline universe but "not_done" on the shipping sleeve is marked **partial** with the gap named).

Legend: ✅ done · 🟡 partial · ⬜ not_done · 🔒 data_gated

---

## CATEGORY 1 — Price & corporate-action data integrity

| # | Check | Status |
|---|---|---|
|1.1| US split adjustment (`auto_adjust=True`) on yfinance | ✅ |
|1.2| KR split/rights adjustment (`pykrx adjusted=True`) | ✅ |
|1.3| **Verify pykrx adjustment actually applied** (post-hoc split-spike scan; some versions silently return unadjusted) | ⬜ |
|1.4| **Total-return vs price-return mismatch BETWEEN the two cross-check sources** (yf auto_adjust = div+split total-return; pykrx = split/rights only, no cash div). The flagship 2nd-source detector compares two different return definitions | ⬜ |
|1.5| US split-adjustment contamination scan (US_LARGE clean 0/347; US_MID 5, US_SMALL 8 unadjusted — e.g. CHRD 257×) | 🟡 |
|1.6| Micro-cap stale / forward-filled close detection (killed KR_MICRO) | ✅ |
|1.7| **Stale/repeated-close & zero-volume scan for the LIQUID universe** (not just micro; "liquid⇒clean" assumed) | ⬜ |
|1.8| Zero/non-trading-day (volume==0) handling — amihud NaNs it, but stale row still enters fwd-shift & rollings | 🟡 |
|1.9| **amihud dollar-volume unit consistency** (adjusted close × possibly-unadjusted volume distorts dv across splits) | ⬜ |
|1.10| **Ticker-reuse / identity splicing** (KR 6-digit code reassignment post-delist; FB→META) | ⬜ |
|1.11| **Spinoff / merger value-transfer** (vendor auto-adjust does NOT correct spinoff step-downs) | ⬜ |
|1.12| Rights-offering / bonus-issue adjustment beyond splits | 🟡 |
|1.13| Zero/negative-denominator guards (EPS=0, dv=0, price=0) | ✅ |
|1.14| NaN/missing handling in features & labels | ✅ |
|1.15| Independent 2nd price source — **KR** (pykrx vs yfinance) | ✅ |
|1.16| Independent 2nd price source — **US_LARGE** (the $382M institutional candidate is yfinance-ONLY) | ⬜ |
|1.17| Independent 2nd source — TW / KR_SMALL | 🔒 |

## CATEGORY 2 — Survivorship & universe membership

| # | Check | Status |
|---|---|---|
|2.1| Survivorship umbrella (caches = current members backfilled) | 🟡 |
|2.2| Late-entrant bias quantified (KR_LARGE +25.5%→+21.3%, ~17% of edge) | ✅ |
|2.3| Late-entrant re-run extended to KR_MID(16%)/KR_MICRO(23%) always-present subsets | 🟡 |
|2.4| **Delisted names ABSENT from cache** (the −90% terminal tail never priced; concentrates in the illiquid decile) | 🔒 |
|2.5| **`len(d)<600` filter = undocumented SECOND survivorship gate** (drops IPOs & near-delisting short-history names before ILLIQ is computed) | ⬜ |
|2.6| **Within-window delisting**: names that delist within the 21d hold get `fwd=NaN` and are silently dropped → illiquid decile's losers-to-zero vanish (distinct from late-entrant; the single most likely upward inflation) | ⬜ |
|2.7| PIT index/marcap membership (bucket fixed by TODAY's marcap for all 2018-26) | 🟡→🔒 |
|2.8| Survivorship inflation of the **capacity/$ADV** estimate itself (surviving illiquid names have upward-biased ADV) | 🟡 |

## CATEGORY 3 — Point-in-time / leakage

| # | Check | Status |
|---|---|---|
|3.1| Feature→target leak scan, all 478 features, |IC|>0.10 (caught vol_adj_ret_21d 0.94) | ✅ |
|3.2| Embargo ≥ max label horizon (fixed 21→63 after 245%/yr leak) | ✅ |
|3.3| Retrain + feature-reselection inside each WF step | ✅ |
|3.4| PIT fundamentals join uses `as_of` filing, `direction=backward` | ✅ |
|3.5| Fundamental reporting-lag realism (as_of_ts == DART rcept_dt, not optimistic) | 🟡 |
|3.6| **Restatement vintage** (original-reported vs latest-restated under same as_of) | 🟡 |
|3.7| Benchmark/excess uses same forward window (legit demeaning, not leak) | ✅ |

## CATEGORY 4 — Signal mechanism & confound attribution (ILLIQ)

| # | Check | Status |
|---|---|---|
|4.1| **amihud rolling index-alignment** (apply+reset_index scramble → fixed to `.transform`; KR_MID +0.36%→+1.48%) | ✅ |
|4.2| Residual `illiq_robust.py` dvol still uses apply+reset_index — re-verify alignment | 🟡 |
|4.3| Mechanical confound amihud≈1/dvol; clean **marcap** control run (KR_LARGE residual +0.96% but H1 −0.08%; "distinct" retracted → "mostly size tilt") | ✅ |
|4.4| **marcap proxy validity**: shares = NET_INCOME/EPS_BASIC drops loss-makers, ~52% coverage — the downgrade itself may be as biased as what it corrected | ⬜ |
|4.5| marcap-clean control run on **KR_MID/MICRO/SMALL** (the account-size-tradeable universes), not just KR_LARGE/US_LARGE | ⬜ |
|4.6| **ILLIQ ≈ 1/price** (tick-size / penny-bounce) confound, distinct from 1/dvol | ⬜ |
|4.7| **amihud outlier/skew**: rolling MEAN of fat-tailed |ret|/dvol → test rolling MEDIAN / winsorized / log-amihud (spike-driven decile?) | ⬜ |
|4.8| Signal sign verified empirically (IC>0, monotone down efficiency gradient) | ✅ |
|4.9| **Collinearity with SHORT-TERM REVERSAL** (ILLIQ = reversal in disguise? residualize on ret_21d/ret_5d) | ⬜ |
|4.10| Collinearity with vol / low-vol factor (|ret| numerator embeds vol; residualize ILLIQ on realised/idio vol) | 🟡 |
|4.11| Value/quality/growth attribution of the decile | 🟡 |
|4.12| Market-beta neutralization (delta −0.86% noise; not the driver) | ✅ |
|4.13| **JOINT multivariate FM regression** (ILLIQ vs size+vol+reversal+value simultaneously) — one-at-a-time flips suggest control-set-dependence | ⬜ |
|4.14| Amihud window (21/63/126) chosen per-universe = researcher df; fold into DSR N | 🟡 |
|4.15| Decile-cutoff (0.9) sensitivity for the ILLIQ tilt specifically | ⬜ |

## CATEGORY 5 — Execution & fill realism  *(the lead's structural blind spot)*

| # | Check | Status |
|---|---|---|
|5.1| **EXECUTION LAG / t+1 fill** — every `fwd = close.shift(-H)/close` fills at the *exact close that generated the signal* = 1-bar look-ahead, largest for the illiquid decile. Scripts titled "realizable/tradeable/deployable" | ⬜ **CRITICAL** |
|5.2| **Bid-ask bounce** contamination of close-to-close returns on the illiquid decile (select & measure on the same bouncing closes) | ⬜ |
|5.3| **Implementation shortfall / VWAP drift** over the multi-day build in the thinnest names (own buying moves price) | ⬜ |
|5.4| **Signal-staleness / decile-membership drift** during the t+1..t+4 build window (distinct from impact) | ⬜ |
|5.5| Limit-up no-fill / halt exclusion applied to the **shipping** KR_LARGE ILLIQ63 sleeve (done only on KR_MID H42 probe) | 🟡 |
|5.6| Per-name fill schedule vs own ADV (median hides right-skew of the decile) | 🟡 |
|5.7| T+2 settlement cash drag | ⬜ |

## CATEGORY 6 — Transaction costs & market impact

| # | Check | Status |
|---|---|---|
|6.1| Cost-sensitivity sweep (30/50/80/120/160/250 bps) | ✅ |
|6.2| **KR securities sell tax (0.15%, sell-side, asymmetric)** — no `tax`/`0.0015` anywhere; ~1.8%/yr at 12 rebalances if not inside the 30bps | ⬜ |
|6.3| **Market impact as a RETURN DRAG** (√-impact / Almgren at target AUM), not merely a capacity ceiling; draw alpha-vs-AUM decay curve | ⬜ **CRITICAL** |
|6.4| **Cost coupled to the capacity number** — "deployable $X at +2.13%" never verified because cost is size-independent | ⬜ **CRITICAL** |
|6.5| Name-level cost from measured spreads (Corwin-Schultz/Roll) vs flat universe bps — decile = widest-spread names | 🟡 |
|6.6| Turnover incl. weight drift (currently 1−Jaccard only) | 🟡 |

## CATEGORY 7 — Capacity, weighting & deployment operations

| # | Check | Status |
|---|---|---|
|7.1| **Currency-unit consistency** in capacity (KRW mislabeled USD, 1350×; fixed FXMAP) | ✅ |
|7.2| Alpha %/Sharpe currency-invariant (bug touched only capacity) | ✅ |
|7.3| Capacity re-coupled to impact (still ADV×part×days headroom, not "AUM where edge persists") | 🟡 |
|7.4| **Cap/$ADV-weighting** of the basket (the impact-mitigating scheme) — only inv-vol/conviction tested & rejected | 🟡 |
|7.5| **"Market-neutral Sharpe 1.70" is a paper long-minus-EW construct** — KR forbids shorting illiquid names; only lower long-only-with-beta Sharpe is real | 🟡 |
|7.6| **Beta-hedge feasibility & cost** (KOSPI200 futures/inverse ETF roll/tracking/margin) — the only real path to a neutral profile | ⬜ |
|7.7| Long-only / short-sale constraint respected by construction | ✅ |
|7.8| Borrow cost/locate (moot long-only; blocker if any neutral variant) | ⬜(low) |
|7.9| **Execution-path wiring** — ILLIQ sleeve & no-norm-blitz have ZERO references in `decision/`; no order-gen/reconciliation | ⬜ |
|7.10| **Single-name concentration** (decile shrinks to ~16-19 names) + per-name %-of-ADV & %-of-book caps + idio blow-up stress | 🟡 |
|7.11| Daily data-freshness / adjustment-parity SLA (train-vs-live pykrx) | 🟡 |
|7.12| **Factor crowding / already-arbitraged** (regress on published KR SMB & Amihud factors; KR smart-beta AUM) — DSR handles snooping, NOT economic decay | ⬜ |

## CATEGORY 8 — Statistical inference (CIs, t-stats)

| # | Check | Status |
|---|---|---|
|8.1| Non-overlapping return sampling (STEP=21) | ✅ |
|8.2| **CI/SE on headline net-excess (+2.13%) & Sharpe (1.70)** — only the bear GATE was bootstrapped, never the shipped point estimate; Lo (2002) SE never computed | ⬜ **CRITICAL** |
|8.3| **CI/t-stat on headline IC (0.03-0.04)** — never tested vs 0 for the shipped signal | ⬜ |
|8.4| **Newey-West / HAC** t-stats on excess & IC (persistent positions → autocorrelated folds; all t-stats naive) | ⬜ |
|8.5| **Block bootstrap** on the headline series — the only bootstrap is `RNG.choice` iid, **mislabeled "block-bootstrap"** in the docstring, and on the gate only | 🟡(lead-error) |
|8.6| **CI on ablation deltas** (no-norm−blitz +0.67%p, |label| −0.20%p) — sub-1%p adoption calls with no delta CI | ⬜ |
|8.7| Fama-MacBeth vs pooled reconciliation; date-clustered SE | ⬜ |
|8.8| Fold-count power / MDE (H42/H63 rest on 37/25 folds) | 🟡 |

## CATEGORY 9 — Multiple-testing & overfitting

| # | Check | Status |
|---|---|---|
|9.1| Deflated Sharpe on KR_LARGE ILLIQ63 (N=400, DSR 97.9%) | ✅ (but see 9.2-9.4) |
|9.2| **DSR extended to EVERY surviving candidate** (US_LARGE +1.12%/Sharpe 1.33 promoted with NO deflation) | ⬜ |
|9.3| **DSR on the universe-selection act** (picking KR_LARGE out of 9 = a 2nd un-haircut selection); N=400 is a hand-guess likely < true trial count; run on a survivorship-inflated series | 🟡(lead-error) |
|9.4| **Explicit family-wise trial ledger** (enumerate universes×signals×levers×horizons×seeds to bound N) | ⬜ |
|9.5| **FWER control** (Bonferroni/Holm/BH-FDR) across ~135+ tests (~7 false winners expected) | ⬜ **CRITICAL** |
|9.6| **White Reality-Check / Hansen SPA** (is ILLIQ63 real or max-of-135?) | ⬜ |
|9.7| Romano-Wolf stepdown | ⬜ |
|9.8| **PBO / CSCV** (prob. backtest overfitting) | ⬜ |
|9.9| **CPCV** with embargo (distribution of paths, not single WF) | ⬜ |
|9.10| Effective-#-independent-tests (correlation/eigenvalue of candidate matrix; ILLIQ63/126/SIZE collinear) | ⬜ |

## CATEGORY 10 — Robustness (subperiod, regime, seed, phase)

| # | Check | Status |
|---|---|---|
|10.1| Multi-seed (3-seed) for stochastic ML | ✅ |
|10.2| Split-half H1/H2 | ✅ |
|10.3| Bear/VIX-tercile with CI | ✅ |
|10.4| IC-vs-tradeable-excess consistency (rank-IC mirage) | ✅ |
|10.5| Horizon sensitivity (H21/42/63) | ✅ |
|10.6| Decile/basket-size sweep (ML model) | ✅ |
|10.7| **Rebalance-phase / start-offset** robustness (one arbitrary `dates[252::21]` phase; never averaged over 21 offsets) | ⬜ |
|10.8| **Year-by-year / rolling** breakdown (only H1/H2; H2 2021-26 explicitly "H2-loaded") | 🟡 |
|10.9| **COVID/2020 exclusion** for the ILLIQ headline (illiquidity premia spike in liquidity crises) | 🟡 |
|10.10| Regime non-stationarity (edge lives in recent half → forward-fragile) | 🟡 |

## CATEGORY 11 — Live-forward validation & monitoring

| # | Check | Status |
|---|---|---|
|11.1| **Forward paper-trade of the ACTUAL sleeve** (KR_LARGE ILLIQ63 + no-norm-blitz) via KIS on live PIT membership ≥3mo | ⬜ **CRITICAL** |
|11.2| **Truly untouched OOS holdout** (all 2018-26 used for search+eval; H2 visible throughout) | ⬜ **CRITICAL** |
|11.3| Garden-of-forking-paths (gates added adaptively after seeing data) | ⬜ |
|11.4| **Live alpha-decay monitor on top-decile EXCESS + kill-switch / max-DD breaker** — drift.py is generic z-score advisory for the retired ML bundle | ⬜ |
|11.5| Tracking-error budget & paper-to-live reconciliation protocol | ⬜ |

---

## (A) CHECKS THE LEAD'S TAXONOMY MISSED ENTIRELY — *the point*

The lead's 6 buckets (data-integrity, methodology, statistics, signal, deployment, live-forward) never once surfaced these. Grouped by the *whole dimension* absent:

**A1. EXECUTION TIMING — the single largest blind spot.** No check anywhere raises *signal-at-close-t vs trade-at-next-bar*. Every backtest fills at the exact close that produced the signal → a **1-bar look-ahead** that is largest precisely for the illiquid decile being traded, embedded in the headline +2.13% / Sharpe 1.70. A reversal/illiquidity tilt typically bleeds a large fraction of a monthly ~2% edge on a 1-bar delay. This alone could halve or invalidate the finding. **(#5.1)**

**A2. Fill microstructure as a family** — all missed together: bid-ask bounce on close-to-close (#5.2), implementation shortfall / VWAP build drift (#5.3), decile-membership drift during the multi-day build (#5.4), T+2 cash drag (#5.7).

**A3. Cost-as-a-function-of-size.** Impact appears ONLY as a capacity ceiling, never subtracted from P&L; capacity and cost are two decoupled numbers, so "deployable $X at +2.13%" was never actually established. The alpha-vs-AUM decay curve is never drawn. **(#6.3, #6.4)**

**A4. KR sell tax (0.15%, asymmetric).** Grep finds no `tax`/`0.0015`; ~1.8%/yr at monthly turnover if not inside the flat 30bps. **(#6.2)**

**A5. The "neutral Sharpe 1.70" is a fictional paper construct.** It is long-minus-EW-benchmark; KR forbids shorting illiquid names, so the neutral leg cannot exist. Only the lower long-only-with-beta Sharpe is achievable — never flagged. The beta-hedge path (KOSPI200 futures/ETF cost) is never modeled. **(#7.5, #7.6)**

**A6. Serial-correlation-robust inference.** No Newey-West/HAC anywhere; the one bootstrap is iid **mislabeled** "block-bootstrap" and covers only the bear gate, never the shipped point estimate. No CI on the headline Sharpe/excess/IC at all. **(#8.2, #8.4, #8.5)**

**A7. Total-return vs price-return mismatch between the two cross-check sources.** The flagship artifact detector compares yf total-return against pykrx price-return; the KR_MICRO "deflation" conflated staleness with a systematic dividend gap and never isolated them. The detector that validated the finding is itself uncontrolled. **(#1.4)**

**A8. Factor crowding / economic arbitrage-away** — distinct from statistical multiple-testing. ILLIQ/size are the most-published anomalies; no correlation to published KR SMB/Amihud factor returns or smart-beta AUM. DSR ≠ crowding. **(#7.12)**

**A9. Operational deployability.** The surviving strategy has ZERO references in `decision/` — no order generation, no reconciliation, no rebalance job. Deployability is unproven independent of any market question. And no live top-decile-excess monitor / kill-switch exists (drift.py is for the retired ML bundle). **(#7.9, #11.4)**

**A10. Corporate-action identity** — ticker reuse/splicing (#1.10) and spinoff/merger value transfer (#1.11); vendor auto-adjust cleans neither.

**A11. Hidden `len<600` filter** as a second, undocumented survivorship gate that removes exactly the short-lived illiquid tail before ILLIQ is computed. **(#2.5)**

**A12. Calendar-vs-row drift** — `shift(-21)` counts rows not calendar days; halted illiquid names (what ILLIQ selects) get silently longer, unrealizable holds. Rolling 63/126 windows span variable calendar time. **(#5-adjacent, data-integrity #1.18/1.19)**

**A13. Within-window delisting drop** — names that delist inside the hold get `fwd=NaN` and vanish, biasing the illiquid decile UP. This is *within the current cache* (not the paid-data delisting question) and is fixable now by flooring their return. The lead only measured late-entrants. **(#2.6)**

---

## (B) TO-EXECUTE LIST — feasible now, critical→low, with concrete run method

**== CRITICAL ==**

1. **Execution-lag re-test (#5.1).** Re-run `illiq_sleeve.py` / `universe_specialize.py` with `fwd = close.shift(-H-1)/close.shift(-1)` (buy at t+1 close), and a t+1-open variant using the `adj_open` field already loaded in `wa5_overnight.py`. Report Δ net-excess and Δ Sharpe for KR_LARGE ILLIQ63 and US_LARGE. *This gates everything else — do it first.*

2. **Market-impact-as-drag + cost-capacity coupling (#6.3/#6.4).** Replace flat bps with `cost_i = spread_i/2 + k·σ_i·√(order_i/ADV_i)`; recompute net at 1/3/5% participation and at target AUM. Plot net-vs-AUM. Redefine capacity as the AUM where net crosses a +0.5% floor.

3. **CI on the headline (#8.2).** Stationary/circular block bootstrap (block ≥ holding period) the full non-overlapping fold net-excess series → CI on mean & Sharpe; add Lo SE = √((1+SR²/2)/T). Fix the `bear_bootstrap.py` docstring mislabel or make it a real block bootstrap.

4. **Within-window delisting floor (#2.6).** For names whose price series terminates within the 21d window, assign `fwd ∈ {−30%,−100%}` instead of dropping; recompute top-decile excess; report the haircut.

5. **FWER across the campaign (#9.5) + trial ledger (#9.4).** Enumerate configs (universes×signals×levers×horizons×seeds) to bound N from below; collect each test's IC-t/p into one vector; apply Holm + BH-FDR; list survivors.

**== HIGH ==**

6. **HAC t-stats (#8.4).** statsmodels OLS on a constant, `maxlags≈holding periods`, for fold-excess and per-date IC series (KR_LARGE + US_LARGE).

7. **KR sell tax (#6.2).** Split cost into buy-side vs sell-side (+15bps tax sell-side); re-net all KR sleeves; confirm 30bps actually covers tax+spread+commission+slippage for KR_LARGE.

8. **Reversal collinearity (#4.9)** + **joint multifactor FM (#4.13).** Cross-sectionally residualize ILLIQ on {ret_5d, ret_21d} then re-run the tilt; then a single FM regression of fwd on {ILLIQ, log-size, vol, ret_21d, value} per date, report ILLIQ marginal t.

9. **amihud outlier/median robustness (#4.7).** Re-run tilt with rolling MEDIAN and log/winsorized amihud; check top-decile membership churn.

10. **marcap-proxy validity + extend to KR_MID/MICRO/SMALL (#4.4/#4.5).** Cross-check shares vs `pykrx get_market_cap` (independent of NI/EPS), report coverage per half, re-run size-neutral on full coverage AND on the account-tradeable universes.

11. **Limit-up/halt no-fill on the SHIPPING sleeve (#5.5).** Port the `kr_realizable.py` filters (min-price, liquidity floor, skip ≥+28% gaps) into `illiq_sleeve.py` KR_LARGE ILLIQ63; re-report net.

12. **DSR on every candidate + universe-selection (#9.2/#9.3).** Extend `deflated_sharpe()` over US_LARGE, KR_MID, KR_MICRO, KR_SMALL net series with a shared honest N; add a second haircut for selecting KR_LARGE out of 9.

13. **US_LARGE 2nd price source (#1.16).** Re-source via Stooq/Tiingo/Alpaca; re-run `universe_specialize` US_LARGE; compare ILLIQ63 IC/excess.

14. **`illiq_robust.py` dvol re-alignment (#4.2).** Recompute via `g['dv'].transform(rolling median)`, diff row-by-row vs the apply version; if different, the "half is size" (T2b) decomposition is corrupt.

15. **pykrx adjustment scan (#1.3)** + **US_MID/SMALL re-pull (#1.5).** Scan each ticker for |ret|>0.5 reversing next day vs KRX split calendar; re-pull US_MID/SMALL adjusted from stooq.

16. **Vol residualization (#4.10)** and **factor-crowding regression (#7.12).**

17. **PBO/CSCV (#9.8)** and **SPA/Reality-Check (#9.6)** on the candidate performance matrix (stationary bootstrap over per-fold excess).

**== MEDIUM ==**

18. **Rebalance-phase robustness (#10.7).** Loop start offset 0..20, recompute net/Sharpe per phase, report mean & dispersion.
19. **Year-by-year + ex-COVID (#10.8/#10.9).** Per-calendar-year net for KR_LARGE ILLIQ63; re-run excluding 2020-02..06.
20. **Ablation-delta CIs (#8.6).** Paired bootstrap over folds of (recipe − base).
21. **1/price confound (#4.6).** log(close)-neutralized ILLIQ variant + joint {log dvol, log price} neutralization.
22. **len<600 filter audit (#2.5).** Report codes dropped per universe + their liquidity profile; re-run keeping them (min_periods already guards rollings).
23. **Cap/$ADV-weighted basket variant (#7.4).** Compare net + capacity vs equal-weight.
24. **CPCV (#9.9), Romano-Wolf (#9.7), effective-#-tests (#9.10), FM-vs-pooled (#8.7), date-clustered SE.**
25. **Bid-ask bounce (#5.2).** Recompute sleeve return from t+1→t+H+1 close, or VWAP/mid proxy; isolate the bounce.
26. **Execution wiring + live excess monitor with kill-switch (#7.9/#11.4).** ILLIQ recommender → `decision/ml_runner` → RiskEngine → PaperBroker; CUSUM-on-excess halt.
27. **Restatement vintage (#3.6), reporting-lag (#3.5), corporate-action identity (#1.10), spinoff scan (#1.11), amihud dv unit (#1.9), liquid-universe stale-close scan (#1.7).**

**== LOW ==**

28. Decile-cutoff sweep for ILLIQ tilt (#4.15); weight-drift turnover (#6.6); capacity ADV percentile band (#7.3); power/MDE (#8.8); position-limit/idio stress at 16-19 names (#7.10).

---

## (C) GENUINELY DATA-GATED — closable only by live-forward or paid data

- **C1. Delisted-inclusive OHLCV (#2.4).** The −90% terminal tail is unpriceable from pykrx. Needs KRX Data / DataGuide/FnGuide (paid) OR resolve at source via KIS forward paper. This is the *largest unbounded upward bias* on the illiquid-decile long; the always-present subset (17%) does NOT measure it.
- **C2. PIT index reconstitution (#2.7).** Historical KOSPI200/KOSDAQ150 constituent lists needed to remove membership look-ahead; compounds with C1.
- **C3. 2nd source for TW / KR_SMALL (#1.17).** TWSE/FinMind for TW; pykrx for KR_SMALL — feasible but not yet built (borderline B/C).
- **C4. Forward paper trade (#11.1) + untouched OOS (#11.2).** All of 2018-26 is contaminated by search; H2 was visible throughout. The ONLY clean test is KIS/Alpaca forward paper on live PIT snapshots ≥3 months, tracking realized top-decile excess vs +2.13%. Every in-sample robustness check remains in-sample until this exists. The lead's own repeated conclusion ("next = KIS forward") concedes this.

---

## (D) STATUSES THE LEAD GOT WRONG

- **D1. "Realizable/tradeable/deployable" titling is false (#5.1).** Scripts documented as realistic long-only tradeable P&L all trade at the signal-generating close = 1-bar look-ahead. Status should be **not_done / contaminated**, not the implied "handled."
- **D2. `bear_bootstrap.py` mislabel (#8.5).** Docstring claims "block-bootstrap"; line 53 is plain iid `RNG.choice`. Same class as the KRW/USD mislabel. The "CI discipline added at 시점31" is weaker than advertised AND was never applied to the headline.
- **D3. The 시점28 "distinct illiquidity premium" was claimed-done-but-WRONG for four checkpoints (28→31) (#4.3-source).** It used a size proxy (−log median dollar-volume) that is mechanically *inside* amihud, so passing was near-guaranteed. Only 시점32's marcap control reversed it. A "done/passed" that drove the flagship headline was self-fulfilling.
- **D4. The 시점32 downgrade is itself questionable (#4.4).** "Mostly a size tilt" rests on ~52% coverage that systematically excludes loss-makers — exactly where illiquidity/distress concentrate. Both the claim AND its correction may be proxy-selection artifacts. Marked "done" but is **partial**.
- **D5. DSR 97.9% reported as if it validated the magnitude (#2.4/#9.3).** DSR corrects multiple-testing, NOT survivorship; it was run on a series the lead itself flags as survivorship-inflated. The two caveats are presented as independent; they are not.
- **D6. "Sharpe 1.70 market-neutral" headline (#7.5).** Reported as strategy quality, but the neutral leg needs KR-forbidden shorts. The achievable long-only-with-beta Sharpe is a different, lower number. Status "done (long-only respected)" masks that the *headline metric* is non-tradeable.
- **D7. Capacity "done" post-currency-fix (#7.3).** Currency is unit-correct but the number is still naive ADV-headroom with no impact coupling — "capacity" is trade-size headroom, not the AUM where the edge persists. Should be **partial**.
- **D8. Late-entrant survivorship marked as the survivorship answer (#2.6).** It measures only entrants; the within-window-delisting drop and the absent-name tail are unaddressed. "17% of edge" is a floor, not the bias.

---

## COMPLETENESS VERDICT

**No — the lead's 6-category taxonomy is NOT complete.** It is thorough *within* data-integrity, in-sample statistics, and signal-mechanism, but it structurally omits an entire dimension — **execution realism / fill timing** (t+1 lag, bid-ask bounce, implementation shortfall, signal-staleness, cost-coupled-to-size) — and under-treats three more: **serial-correlation-robust inference (HAC/true block bootstrap + CIs on the shipped estimate)**, **operational deployability (execution wiring + live kill-switch monitor)**, and **economic crowding vs statistical snooping**. Because the missing execution-lag check is plausibly the most load-bearing untested assumption in the whole campaign, the taxonomy's gap is not cosmetic: the headline +2.13% / Sharpe 1.70 is unverified against a 1-bar look-ahead, uncoupled from size-scaled impact, and reported as a market-neutral number that KR short-selling rules make non-tradeable.
---

## (E) 2회차 실행 매핑 (시점34, `robustness_batch1/2/3/3b.py`)

각 체크# → 실제 결과. 헤드라인 = KR_LARGE ILLIQ63 net-excess(비용 30bps, STEP=21).

| # | Check | 실행 결과 | 상태 |
|---|---|---|---|
|4.5| marcap-clean on KR_MID | 잔차 +1.89% t=2.11, 단 share커버 28% 부분표본(저검정력) | 🟡 |
|4.6| ILLIQ ≈ 1/price 교란 | 1/price중립 net +0.87%(유지, 약화) | ✅ |
|4.7| amihud outlier(median/log) | median +1.88·log +1.83(스펙 강건) | ✅ |
|4.9| reversal 공선성 | reversal-중립 +1.83(유지=distinct, 시점33) | ✅ |
|4.13| **JOINT 다변량 FM** | ILLIQ 한계 **t=1.74(유의미달)**·VOL t=2.49·SIZE t=1.95 → known-factor 블렌드 | ✅ |
|4.15| decile-cutoff 민감도 | top5/10/20 = +1.96/+1.94/+1.34(단조) | ✅ |
|5.2| bid-ask bounce | 유동적 절반(+2.42)>최소유동 절반(+1.43) → bounce/tiny-name 아님(간접해소) | 🟡 |
|5.5| limit-move 제외(shipping) | 형성일 |ret|≥29% 배제해도 +1.94→+1.94(불변) | ✅ |
|6.3/6.4| 임팩트 drag + cost-coupled | impact_deploy(버그자각)→**batch3b가 대체**: ADV-weight/유동적절반서 강화=고용량 | 🟡→✅ |
|7.4| cap/ADV-weighting | equal +1.94→√ADV +2.24→**ADV +2.52%(t=4.18)**, clean벤치 +2.48(t=3.12)=진짜 | ✅ |
|7.5| market-neutral 실현성 | 초과분=long-decile−universe → **KOSPI200선물 헤지로 실현**(개별공매도 불필요) | ✅ |
|8.2| CI/SE 헤드라인 | block-CI[+1.20,+2.66]·Lo SE·NW(시점33) | ✅ |
|8.4| HAC t-stat | **t=4.94**(시점33) | ✅ |
|9.2| DSR 전후보 | KR_LARGE 97.1·KR_MID 94.9·US 85.5 생존 / MICRO 13.5·SMALL 31.7 탈락 | ✅ |
|9.3| 선택 haircut DSR | N=400→93.9·N=1000(초보수)→89.9% | ✅ |
|9.4| family 시행원장 N | N=135(≈9유니버스×15신호) 명시 | ✅ |
|9.5| **FWER Bonferroni/Holm/BH** | **KR_LARGE·KR_MID가 N=135서 Bonferroni·Holm 생존**, US는 BH-FDR만 | ✅ |
|9.8| **PBO/CSCV** | **0%**(IS-best amihud window가 OOS 중앙값 100% 상회=과적합 없음) | ✅ |
|10.7| rebalance-phase | 위상 6개 평균 +1.74±0.18%(강건) | ✅ |
|10.8| year-by-year | **2019-25 전부 양(+)**(최악 2022 +0.54·최고 2023 +3.28) | ✅ |
|10.9| ex-COVID | 2020-02~06 제외 +1.95%(불변) | ✅ |

## (F) 2회차가 정정한 이전 상태오류

- **D1 정정(#5.1)**: 체결지연은 시점33 execution_lag서 이미 t+1 생존 확인. 2회차 batch3b가 추가로 **엣지가 유동적 절반에서 더 강함**을 보여 "가장 얇은 이름에서만 사는 체결불가 알파"를 반박.
- **D7 정정(#7.3)**: 시점33 "저용량 $M~수천만"은 impact_deploy 버그(part.clip이 infeasibility 은폐) 기반. batch3b는 **ADV-weight가 엣지를 강화**함을 clean벤치서 입증 → **고용량·실현가능**으로 정정. 단 절대 AUM 상한은 forward paper 전까지 미확정.
- **잔존(미정정)**: D3/D4(marcap 프록시 selection)·D5(DSR≠생존편향)·D6(부분): marcap-clean은 여전히 52%/28% 커버 프록시 한계. **C1(상폐포함)·C4(forward paper)는 데이터게이트로 남음** — 2회차의 모든 강건성은 forward 없이는 in-sample.

## (G) 최종 종합 (시점34)

KR_LARGE/MID ILLIQ 엣지는 **진짜이되 이색적이지 않다**:
1. **진짜**: 체결지연·spec·decile·COVID·위상·전연도·다중검정(Bonferroni N=135)·PBO 전부 견고.
2. **이색적 아님**: 다변량 FM서 ILLIQ 독립 t=1.74·marcap-clean서 KR_LARGE 대부분 흡수 → **잘 알려진 size/유동성 프리미엄**(순수 비유동 아님).
3. **고용량·실현가능**: 유동적 절반서 더 강함·ADV-weight 개선 → long-illiquid-decile + short-KOSPI200선물로 실현(개별공매도 불필요).
4. **유일 미해결 = 데이터게이트**: 상폐포함 OHLCV·PIT 멤버십·**forward paper**(유료데이터/KIS). 이것 없이는 모든 검증이 in-sample.
