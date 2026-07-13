# 다음 업무 백로그 (2026-07-10)

> 4개 소스(GAPS·WORK_LOG·코드 게이트·ALPHA_CAMPAIGN 프론티어) 병렬 스캔 + 종합. 31개 항목.
> 상세 근거는 [WORK_LOG.md](WORK_LOG.md) / [GAPS.md](GAPS.md), 알파 연대기는 [ALPHA_CAMPAIGN.md](ALPHA_CAMPAIGN.md).

## 전략 요약 (가장 중요)

**알파 엣지는 "진행 중"이 아니라 "닫혔다"** — 모델/전처리/사이징/앙상블 전 레버 검증 후 기각, US는 2025-26 선정 알파 감쇠(IC≈0, dispersion은 높음). ※이 "닫힘"은 **시장별 특화 프레임의 강건 재검증으로 획득된 것**(옛 교차-일반화 가정 아님): 2026-07-10 "시장갈림 기각" 레버 전수 재감사 결과 진짜 per-market 후보는 KR 2개(swabs_neu/mnmh)뿐이었고, 그마저 15폴드 walk-forward top-decile **excess**서 baseline에 패(swabs_neu는 rank-IC↑였으나 거래바스켓 excess는 −71bp = rank-IC 신기루). US쪽 후보 0. → 시장별로도 baseline(순수 lgbm) 유지. 따라서 **실매매 목표를 막는 건 알파가 아님.** 실제 관문 3가지:
1. **순수익 정직성 미구현** — 거래비용·KR세금·환전·생존편향 전부 미모델. 지금 페이퍼/백테스트 숫자는 optimistic-gross 허구라 돈 넣을 근거가 안 됨.
2. **실행 배관 부재** — `RUNTIME_MODE=live`는 no-op, KIS/Alpaca 어댑터 미작성, live-order 경로 미배선, daily_loss/consecutive_loss/spread 게이트가 하드코딩 placeholder(절대 안 걸림), 심볼 allowlist 비어있음(아무 종목이나 거래 가능).
3. **감쇠 안전망 OFF** — drift 모니터 비활성이라 감쇠 모델을 실자본으로 거래해도 자동감지 0.

**올바른 자세**: 알파 사냥 동결. 불가피한 페이퍼 축적 대기창 동안 **정직-비용 회계 + 실행 배관 + 안전 모니터 + 킬스위치**를 병렬로 구축. 데이터-게이트 프론티어(뉴스·옵션·PEAD)는 스케줄러가 알아서 익힘.

**열린 결정 (1개)**: **어느 시장을 먼저 라이브로? KR(KIS) vs US(Alpaca).** 이 하나가 P0 배관 순서·비용모델 특화·페이퍼 우선순위를 전부 결정. 각 시장의 알려진 약점을 의식적으로 수용해야 함 — KR=선정 알파(mn)는 있으나 방향/타이밍 모델 없음(KR=현금보유가 정직한 기본값)+수급/뉴스 구조적 차단 / US=방향게이팅·뉴스축 있으나 2025-26 선정 알파 감쇠. **권고: US-first (Alpaca 무료 페이퍼 엔드포인트 = 브로커 통합 리스크 0의 최고 충실도 검증 단계). 단 감쇠 때문에 drift 모니터를 첫날 반드시 동반.**

---

## P0 — 실매매 경로 해금 / 라이브 correctness·감쇠 리스크 (6)

| # | 테마 | 항목 | 규모 | 기대가치 |
|---|---|---|---|---|
| 1 | real-trading | **멀티월 페이퍼 실적 축적(core-kr/us) — go-live 관문**. integrated.daily+decisions.daily 유지, 월단위 실현 Sharpe/hit/DD 수집, 합격 임계 사전정의 | L | high (검증 관문 자체; 단 비용/세금 정직성 선행 필요) |
| 2 | real-trading | **거래비용·세금·슬리피지 모델**(KR 0.18%+수수료, US SEC/FINRA, spread, FX, 차입) — 페이퍼·백테스트 양쪽에 순비용 회계 적용 | M | high (모든 숫자를 gross→net 신뢰 가능으로; go-live 하드 전제) |
| 3 | US-decay | **drift 모니터링 활성화**(mlops.drift_check.daily, ~5분 KS+rolling-IC) + 알림 배선. retrain.weekly는 training.weekly와 중복이니 skip | S | high (near-zero 비용, 최대 알려진 리스크=US 감쇠 직접 커버) |
| 4 | real-trading | **KIS Developers 브로커 어댑터**(KR 라이브 실행): 토큰인증/주문/포지션/잔고 + KR 비용모델. 현재 kis.py 없음(brokers/base.py 계약스텁만) | L | high (KR 실주문 필수) · dep=시장선택 |
| 5 | real-trading | **Alpaca 브로커 어댑터**(US 라이브+페이퍼): REST 인증/주문/포지션. 페이퍼 엔드포인트=고충실도 검증단계 | M | high (US 실주문 필수 + 페이퍼 충실도↑) · dep=시장선택 |
| 6 | real-trading | **live-order 실행경로 배선**(현 RUNTIME_MODE=live는 no-op): is_live_enabled/RequireLiveUser 뒤에서 PaperBroker↔실어댑터 스왑, 스케줄 결정→실주문 라우팅 | M | high (없으면 어댑터 도달불가) · dep=어댑터 |

## P1 — 고가치 & 즉시 착수 가능 (8)

| # | 테마 | 항목 | 규모 | 기대가치 |
|---|---|---|---|---|
| 7 | real-trading | **inert 리스크 게이트 배선**(daily_loss/consecutive_loss/spread) + allowlist 채우기. 현재 placeholder라 발화 불가·아무종목 거래 가능 | M | high (실자본 전 자본보호 correctness) |
| 8 | real-trading | **tax-loss-harvesting+wash-sale 모듈 배선**(decision/tax_loss_harvesting.py, 현 standalone) → runner/integrated + KR 대주주 양도세 | M | medium (사용자 필수 지정; 순세후수익에 실질적) |
| 9 | real-trading | **환헤지 모듈 배선+A/B**(decision/currency_hedge.py, 현 미호출): US 결정/백테스트에 VWDH/static_50/mvhr/regime/carry/none | M | medium (US북 FX 노이즈↓; 크기 미지) |
| 10 | ops | **1버튼 비상 킬스위치**(주문취소+청산+라이브루프 중지) UI/엔드포인트 | S | high (실자본 전 운영 필수) |
| 11 | defense | **생존편향·상폐 처리**(replay에 상폐종목 포함, 상폐일 -100%, split/배당/기업행위, PIT 누수 자동감지) | M | high (go-live 숫자의 상방편향 제거) |
| 12 | defense | **정직-백본 검증**: CPCV + Deflated Sharpe + PBO(현 date-grouped TSS+21d embargo 위에) | M | medium (알파는 아니나 엣지 신뢰도 급상승 or 정직히 폐기) |
| 13 | real-trading | **배포시 3시드 rank-average**(±5-9% 시드분산 제거, 특히 US) — 강건성이지 알파 아님 | S | low (알파 없음; 배포시 할만함) |
| 14 | ops | **증분(오늘만 append) 피처캐시 빌드** — _fs_ab_{m}_365 전체재빌드→증분, 23:00 잡이 신선피처 확보 | M | medium (일일 라이브 케이던스 신뢰성) |

## P2 — 가치 있으나 의존성 게이트 (13, 그룹)

**알파 프론티어(데이터 대기)** — 대부분 백필이 2018-24 학습창에 들어와야 테스트 가능:
- **I(정보)축 뉴스/센티먼트 재테스트** — 6yr GDELT 백필 완료 후(~8-10월 자동). 유일하게 공정평가 못 한 축, 최대 불확실 상방. dep=백필. (EV=uncertain; 영어중심/US편향, KR뉴스 차단, 엄격 2025-26 holdout 통과 필요)
- **GDELT V2 tone/themes/persons/orgs 직접 추출**(현 요약만) → I축 강화. dep=I축.
- **PEAD/어닝서프라이즈** — 서프라이즈 히스토리 깊이 축적 후. dep=alt-data 백필.
- **US 옵션 implied**(put/call, IV skew, term slope) — 전방 스냅샷 축적 후(months-years). US전용.
- **KR 단기반전(resid_rev 5/10/21d) 단독 클린 1런** — 기각된 mn_w5 번들서만 테스트됐음. S/EV=low(약한 prior).
- **USD/KRW + KR 선행매크로**(수출/PMI/국채) raw 피처 — 기각된 macro-deep 상호작용과 별개. EV=uncertain.

**방어/사이징(페이퍼 확정 후)**:
- 노출 오버레이 **breadth항 캘리브레이션**(현 미캘리 휴리스틱). dep=페이퍼.
- **US 방향게이팅(concentrate) 페이퍼검증 후 활성화** — 백테스트 US Sharpe 0.62→0.72이나 in-sample·무비용. dep=페이퍼+긴 방향캐시.
- **basket hysteresis 기본ON 승격** — 페이퍼 확정 후(US 턴오버 54→37%, net Sharpe@40bps 0.53→0.59). dep=페이퍼+비용모델.

**데이터/피처(무료·미구현)**:
- **펀더멘털 합성점수**(Piotroski F/Altman Z/Beneish M) + 저하된 입력 수정(Beneish DEPI/SGAI, 현금흐름 concept 재백필→Sloan/FCF).
- **SEC 10-K LM 센티먼트 + Form 4 내부자** 실행·스케줄 배선(현 NaN). US전용.
- **KR 유니버스 실제 지수멤버십**(현 mcap 프록시=생존편향). dep=KRX 접근(로그인월).

**운영 경화**:
- **CI/회귀 자동화 + 운영 모니터링 + audit 보존잡**(현 CI 없음, decision_audit 무한증가, 라이브 P&L/포지션 대시보드 부재).

## P3 — 저EV / 파킹랏 (4, 압축)

- **US 앙상블 = shadow 유지**(재litigate 금지; 2024-25 실제 드로다운 폴드 축적 후에만 재평가).
- **저EV 마이크로 알파 배치테스트**(KR amihud×size, 52주고가근접, beta-neutral 라벨, US 섹터중립 재확인) — no-complacency 완결용, 과투자 금지.
- **기계적 피처 확장**(GK/Parkinson/YZ 변동성, 캔들패턴, pandas-ta, rolling beta/Sortino/VaR) — 트리가 이미 포착, 저한계가치.
- **이연 로드맵**(주기적 리밸런싱, LIMIT주문, LLM failover, TimescaleDB hypertable, ESG, HFT/인트라데이) — 투기적/시기상조.

---

## 다음 세션 착수 권고

1. **시장선택 결정**(US-first 권고) → 2. **비용/세금 모델(P0#2)** + **drift 모니터 켜기(P0#3)** 먼저(둘 다 페이퍼 불필요, 즉시 착수) → 3. 선택시장 **어댑터+live 배선(P0#4/5/6)** + **리스크게이트/킬스위치(P1#7/10)** → 4. **페이퍼 축적 시작(P0#1)**은 비용모델 얹은 뒤. 알파 프론티어는 백필이 익을 때까지 건드리지 않음.
