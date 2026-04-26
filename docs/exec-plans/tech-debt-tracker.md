# 기술 부채 추적기

에이전트가 외부 컨텍스트에 의존하지 않고 기술 부채를 찾고, 우선순위를 정하고, 정리할 수 있도록 알려진 기술 부채를 기록합니다.

| 항목 | 도메인 | 레이어 | 상태 | 메모 |
| --- | --- | --- | --- | --- |
| Red-Green 전략 엔진 부재 | red-green-strategy | Service | open | Pine Script 전략을 서버 상태 머신으로 구현해야 합니다. |
| MT5 SL 수정 기능 부재 | trading | Repo | open | TP1 이후 본절 이동과 트레일링 SL 갱신에 필요합니다. |
| 전략 런타임 부재 | strategy | Runtime | open | 봉 마감 감지, 중복 처리 방지, 재시작 복구가 필요합니다. |
| 구조적 아키텍처 검사 부재 | repo | tooling | open | `ARCHITECTURE.md`의 레이어 방향을 검증하는 테스트 또는 린트가 필요합니다. |
| 전략 이벤트 로그 부재 | history | Repo | open | 신호, 주문 의도, 체결, 청산, 실패 사유를 저장해야 합니다. |
