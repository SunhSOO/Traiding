# 품질 점수

woonam 자동매매 시스템의 도메인 × 아키텍처 레이어별 품질을 추적합니다. "필요"는 미구현, "일부"는 부분 구현, "완료"는 테스트 포함 완성.

| 도메인 | Types | Config | Repo | Service | Runtime | UI | 메모 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| core (DB/auth/risk/audit/paper/as_of) | 필요 | 필요 | 필요 | 필요 | n/a | n/a | Phase 0 전체. |
| markets.kr (calendar/tax/ticker/currency) | 필요 | 필요 | 필요 | 필요 | n/a | n/a | Phase 0 후반. |
| markets.us (calendar/tax/ticker/currency) | 필요 | 필요 | 필요 | 필요 | n/a | n/a | Phase 0 후반. |
| brokers.mt5 | 완료 | 완료 | 완료 | n/a | n/a | n/a | FX/금만 사용. 주식엔 미사용. |
| brokers.paper | 필요 | 필요 | 필요 | 필요 | n/a | n/a | Phase 0.7. |
| data.price | 필요 | 필요 | 필요 | n/a | n/a | n/a | Phase 1. |
| data.fundamental | 필요 | 필요 | 필요 | n/a | n/a | n/a | Phase 1. |
| data.disclosures | 필요 | 필요 | 필요 | n/a | n/a | n/a | Phase 1. DART/KIND/EDGAR. |
| data.news | 필요 | 필요 | 필요 | n/a | n/a | n/a | Phase 1. BIGKinds/GDELT/Wayback. |
| data.macro | 필요 | 필요 | 필요 | n/a | n/a | n/a | Phase 1. FRED/ECOS. |
| fundamental (분석) | 필요 | 필요 | 일부 | 필요 | 필요 | 필요 | Phase 2. |
| technical (분석) | 일부 | 일부 | 일부 | 일부 | 일부 | 일부 | Red-Green 1개 시그널만. 나머지 Phase 2. |
| technical.signals.red_green | 완료 | 완료 | 완료 | 완료 | 완료 | 일부 | 기존 구현 유지. |
| information (분석) | 필요 | 필요 | 필요 | 필요 | 필요 | 필요 | Phase 2. LLM 의존. |
| decision (복합 엔진) | 필요 | 필요 | n/a | 필요 | 필요 | 필요 | Phase 4. |
| training (학습/백테스트) | 필요 | 필요 | 필요 | 필요 | n/a | 필요 | Phase 3. |
| runtime (스케줄러/봉마감) | 일부 | 필요 | 필요 | n/a | 일부 | n/a | Phase 0 후~Phase 4. |
| frontend (KR/US 탭) | 일부 | 일부 | 필요 | 일부 | n/a | 일부 | Phase 5. 7페이지 기존 + 분석/감사/백테스트 추가. |
| settings | unknown | unknown | 필요 | 필요 | n/a | 일부 | live 모드 + 리스크 설정 UI 필요. |
