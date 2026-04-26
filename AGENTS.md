# 에이전트 맵

이 저장소는 SUPERRICH MT5 자동매매 시스템을 에이전트가 안전하게 확장할 수 있도록 구성합니다. 이 파일은 길게 설명하지 않고, 권위 있는 문서로 안내하는 목차 역할만 합니다.

## 먼저 볼 문서

- [ARCHITECTURE.md](ARCHITECTURE.md): 자동매매 도메인의 레이어 구조와 의존성 방향
- [docs/product-specs/red-green-auto-trading.md](docs/product-specs/red-green-auto-trading.md): 메인 자동매매 전략 명세
- [docs/exec-plans/active/red-green-auto-trading-implementation.md](docs/exec-plans/active/red-green-auto-trading-implementation.md): 구현 실행 계획
- [docs/FRONTEND.md](docs/FRONTEND.md): 전략 제어 화면과 UI 규칙
- [docs/RELIABILITY.md](docs/RELIABILITY.md): 실거래 안정성, 장애 대응, 관측 기준
- [docs/SECURITY.md](docs/SECURITY.md): 계정, 주문, 비밀값, 권한 경계
- [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md): 레이어별 품질 현황

## 지식 베이스

- `docs/design-docs/`: 설계 결정과 핵심 원칙
- `docs/exec-plans/active/`: 진행 중인 실행 계획
- `docs/exec-plans/completed/`: 완료된 실행 계획
- `docs/exec-plans/tech-debt-tracker.md`: 알려진 기술 부채
- `docs/generated/`: 생성된 스키마, API 목록, 지표 산출물
- `docs/product-specs/`: 제품 동작 명세
- `docs/references/`: 긴 레퍼런스와 원본 전략 해설

## 작업 규칙

- 자동매매 변경은 반드시 `Types -> Config -> Repo -> Service -> Runtime -> UI` 흐름에 맞춥니다.
- Pine Script의 차트 표시 로직과 서버 주문 로직을 섞지 않습니다.
- 주문 실행 전에는 항상 심볼, 타임프레임, 계좌 상태, 포지션 상태, 리스크 한도를 확인합니다.
- 전략 조건이 바뀌면 제품 명세와 실행 계획을 함께 갱신합니다.
- 실거래 주문을 추가하거나 바꾸는 변경은 데모 모드, 백테스트, 페이퍼 실행 기록 없이 바로 활성화하지 않습니다.
