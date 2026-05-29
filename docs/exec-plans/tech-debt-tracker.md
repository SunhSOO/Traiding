# 기술 부채 추적기

에이전트가 외부 컨텍스트에 의존하지 않고 기술 부채를 찾고, 우선순위를 정하고, 정리할 수 있도록 알려진 기술 부채를 기록합니다.

| 항목 | 도메인 | 레이어 | 상태 | 메모 |
| --- | --- | --- | --- | --- |
| 영속화 부재 | core | Repo | open | StrategyManager events는 200개 in-memory 링버퍼. PostgreSQL 도입(Phase 0.2)으로 해결. |
| 재시작 복구 부재 | runtime | Runtime | open | 서버 재시작 시 MT5 포지션과 전략 상태 대조 없음. Phase 0.7 + 1 이후 적용. |
| 리스크 경계 미실행 | core | Service | open | `max_spread_points` 등 필드만 선언, 검사 코드 없음. Phase 0.5에서 실구현. |
| 인증 부재 | core | Service | open | `allow_origins=["*"]` + 인증 미들웨어 없음. Phase 0.4에서 JWT 도입. |
| look-ahead bias 가드 부재 | core | Service | open | as_of 인자 없이 데이터 조회 가능. Phase 0.8에서 데코레이터로 강제. |
| 결정 감사 로그 부재 | core | Repo | open | 결정의 입력·LLM 출력·실행 결과 영구 저장 없음. Phase 0.6에서 `decision_audit` 테이블. |
| 페이퍼 트레이딩 부재 | brokers | Service | open | dry_run은 주문만 막을 뿐 가상 P&L 계산 없음. Phase 0.7. |
| 시장 분리 부재 | markets | Types | open | KR/US 분리 어댑터 없음. Phase 0 후반. |
| 데이터 인제스천 부재 | data | Repo | open | 외부 데이터 소스(DART/EDGAR/BIGKinds/GDELT 등) 어댑터 0개. Phase 1. |
| LLM 어댑터 부재 | core | Providers | open | Ollama/Groq/Gemini 어댑터 없음. Phase 2. |
| 백테스트 프레임워크 부재 | training | Service | open | vectorbt 등 미도입. Phase 3. |
| 종목 클러스터링 부재 | training | Service | open | 종목별 모델 차별화 위한 클러스터링 없음. Phase 3. |
| 모델 레지스트리 부재 | training | tooling | open | MLflow 등 모델 버전 관리 없음. Phase 3. |
| 드리프트 감지 부재 | decision | Service | open | 실제 결정 vs 백테스트 분포 비교 없음. Phase 4. |
| 구조적 아키텍처 검사 부재 | repo | tooling | open | `ARCHITECTURE.md`의 레이어 방향을 검증하는 테스트 또는 린트 필요. |
| Red-Green 시그널 다중화 | technical | Service | open | 현재 시그널 1개. pandas-ta 기반 다중 지표 도입 Phase 2. |
| 차트 미구현 | frontend | UI | open | Live Trading 차트가 placeholder. TradingView Lightweight Charts 도입 Phase 5. |
| KR/US 탭 UI 부재 | frontend | UI | open | 현재 시장 구분 없음. Phase 5에서 모든 페이지 탭 적용. |
