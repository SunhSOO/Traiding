# 아키텍처

이 저장소는 OpenAI의 하네스 엔지니어링 구조를 기준으로, SUPERRICH MT5 자동매매 시스템을 에이전트가 읽고 확장하기 쉬운 형태로 관리합니다.

## 저장소 지식 구조

```text
AGENTS.md
ARCHITECTURE.md
docs/
├── design-docs/
│   ├── index.md
│   └── core-beliefs.md
├── exec-plans/
│   ├── active/
│   │   └── red-green-auto-trading-implementation.md
│   ├── completed/
│   └── tech-debt-tracker.md
├── generated/
│   └── db-schema.md
├── product-specs/
│   ├── index.md
│   ├── new-user-onboarding.md
│   └── red-green-auto-trading.md
├── references/
│   ├── red-green-strategy-pine-summary.md
│   ├── design-system-reference-llms.txt
│   ├── nixpacks-llms.txt
│   └── uv-llms.txt
├── DESIGN.md
├── FRONTEND.md
├── PLANS.md
├── PRODUCT_SENSE.md
├── QUALITY_SCORE.md
├── RELIABILITY.md
└── SECURITY.md
```

## 자동매매 도메인 레이어

각 자동매매 도메인은 아래 흐름을 따릅니다.

```text
Types -> Config -> Repo -> Service -> Runtime -> UI
```

교차 관심사는 `Providers`를 통해서만 도메인에 주입합니다.

```text
Providers -> Service -> Runtime -> UI
```

## 레이어 책임

- `Types`: 캔들, 지표 값, 전략 신호, 주문 의도, 포지션 상태, 리스크 설정의 타입 계약을 정의합니다.
- `Config`: 전략 파라미터와 계좌별 리스크 한도를 검증된 설정으로 만듭니다.
- `Repo`: MT5 시세, 캔들, 포지션, 주문 이력, 전략 실행 로그를 읽고 저장합니다.
- `Providers`: MT5 연결, 시간 소스, 알림, 로깅, 인증, 환경 설정을 제공합니다.
- `Service`: Red-Green 전략 계산, 신호 판단, 포지션 상태 머신, 리스크 검사, 주문 의도 생성을 담당합니다.
- `Runtime`: 봉 마감 감지, 스케줄러, 웹소켓 브로드캐스트, 주문 실행, 장애 복구를 담당합니다.
- `UI`: 전략 상태 표시, 시작/중지, 파라미터 확인, 수동 승인, 로그 조회를 담당합니다.

## 메인 전략 경계

메인 전략은 `Red-Green Signals + SL/TP v9 (Trail Fix)`입니다.

서버 구현은 Pine Script를 그대로 복사하는 것이 아니라, 같은 계산 순서와 상태 전이를 재현해야 합니다. 특히 다음 순서를 유지합니다.

1. BB-ICHI 클라우드와 Supertrend 계산
2. `freshLong`, `freshShort` 신호 산출
3. 기존 포지션의 트레일링 업데이트
4. SL, TP1, Trail 도달 여부 확인
5. TP1 도달 시 SL을 본절로 이동
6. SL 또는 Trail 청산 처리
7. 신규 진입 처리

## 금지 사항

- UI에서 직접 MT5 주문을 생성하지 않습니다.
- 전략 계산 없이 단순 알림만으로 실거래 주문을 실행하지 않습니다.
- `Service`를 우회해 `Runtime`이나 `UI`가 포지션 상태를 임의 변경하지 않습니다.
- 실거래 모드에서 데모용 mock 데이터와 실제 MT5 데이터를 섞지 않습니다.
- Pine Script의 라벨, 색상, 라인 표시 로직을 주문 판단의 근거로 사용하지 않습니다.

## 강제 적용 대상

향후 구조적 테스트와 린트로 다음 항목을 검증합니다.

- 전략 도메인의 import가 레이어 방향을 따르는지
- 캔들 데이터가 충분한 lookback 이후에만 신호를 생성하는지
- 주문 전 리스크 한도가 항상 검사되는지
- 포지션 상태 전이가 로그로 남는지
- 실거래 명령과 데모 명령이 명확히 분리되는지
