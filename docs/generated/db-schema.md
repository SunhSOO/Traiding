# 데이터베이스 스키마

생성된 데이터베이스 스키마 문서는 이곳에 둡니다.

Red-Green 자동매매 구현 시 최소한 다음 기록 모델이 필요합니다.

## 필요 테이블 후보

- `strategy_instances`: 전략 인스턴스 설정과 실행 상태
- `strategy_events`: 신호, 상태 전이, TP1, SL, Trail 이벤트
- `order_intents`: 전략이 생성한 주문 의도
- `trade_executions`: MT5 주문 결과와 체결 정보
- `risk_snapshots`: 주문 전 리스크 검사 결과

권위 있는 스키마가 생기면 이 파일은 실제 스키마에서 다시 생성합니다.
