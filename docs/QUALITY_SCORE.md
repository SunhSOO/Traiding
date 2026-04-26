# 품질 점수

제품 도메인과 아키텍처 레이어별 품질을 추적합니다.

| 도메인 | Types | Config | Repo | Service | Runtime | UI | 메모 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| red-green-strategy | 필요 | 필요 | 필요 | 필요 | 필요 | 필요 | 메인 자동매매 전략. 구현 전 문서화 완료, 코드 필요. |
| trading | 일부 | 일부 | 일부 | 부족 | 부족 | 일부 | MT5Bridge와 주문 API는 있으나 전략 주문 정책과 SL 수정이 필요합니다. |
| strategy | 부족 | 부족 | 부족 | 부족 | 부족 | 일부 | 현재 mock API 중심. Red-Green 실행 상태 관리가 필요합니다. |
| history | 일부 | 일부 | 일부 | 부족 | 부족 | 일부 | 체결 이력은 있으나 전략 이벤트 로그가 필요합니다. |
| portfolio | unknown | unknown | unknown | unknown | unknown | unknown | 초기 점수화가 필요합니다. |
| settings | unknown | unknown | unknown | unknown | unknown | 일부 | live 모드와 리스크 설정 UI가 필요합니다. |
| analytics | unknown | unknown | unknown | unknown | unknown | 일부 | 전략별 성과 분석이 필요합니다. |
