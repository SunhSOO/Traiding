# 에이전트 맵

이 저장소는 **woonam-auto-trading** 자동매매 시스템을 에이전트가 안전하게 확장할 수 있도록 구성합니다. 메인 비전은 기본적·기술적·정보 분석 세 모듈의 복합 결정 기반 한·미 주식 자동매매이며, 기존 MT5 + Red-Green 코드는 그중 기술적 분석 모듈의 한 시그널로 흡수됩니다. 이 파일은 권위 있는 문서로 안내하는 목차 역할만 합니다.

## 먼저 볼 문서

- [ARCHITECTURE.md](ARCHITECTURE.md): 도메인 레이어 구조와 의존성 방향, 백엔드 도메인 폴더 맵
- [docs/exec-plans/active/woonam-auto-trading-implementation.md](docs/exec-plans/active/woonam-auto-trading-implementation.md): **메인** 구현 실행 계획 (Phase 0~6)
- [docs/product-specs/red-green-auto-trading.md](docs/product-specs/red-green-auto-trading.md): 기존 기술 시그널 1개의 명세 (기술 분석 모듈의 일부)
- [docs/exec-plans/active/red-green-auto-trading-implementation.md](docs/exec-plans/active/red-green-auto-trading-implementation.md): 위 시그널의 구현 계획 (woonam 계획의 하위 집합)
- [docs/FRONTEND.md](docs/FRONTEND.md): UI 규칙. KR/US 탭 분리 의무 포함
- [docs/RELIABILITY.md](docs/RELIABILITY.md): 실거래 안정성, 장애 대응, 관측 기준
- [docs/SECURITY.md](docs/SECURITY.md): 계정, 주문, 비밀값, 권한 경계, look-ahead bias 차단
- [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md): 도메인×레이어 품질 현황

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
- 새 데이터·새 시그널·새 결정은 항상 `as_of` 시각이 명시되어야 하며, 학습/백테스트/실거래 모두 동일 파이프라인을 거칩니다 (look-ahead bias 차단).
- 한국과 미국 시장의 종목 데이터·결정·표시는 절대 섞지 않습니다. 백엔드는 `(market, ticker)` 복합키, UI는 시장별 탭으로 분리합니다.
- 무료 도구만 사용합니다. 유료 API·유료 LLM·유료 뉴스 구독은 금지이며, 빠지면 자유 대체 또는 명시적 갭으로 남깁니다.
- Pine Script의 차트 표시 로직과 서버 주문 로직을 섞지 않습니다.
- 주문 실행 전에는 항상 심볼, 타임프레임, 계좌 상태, 포지션 상태, 리스크 한도를 확인합니다.
- 전략 조건이 바뀌면 제품 명세와 실행 계획을 함께 갱신합니다.
- 실거래 주문을 추가하거나 바꾸는 변경은 페이퍼 트레이딩 검증, 백테스트, 결정 감사 로그 없이 바로 활성화하지 않습니다.

## 백엔드 도메인 폴더 맵

| 폴더 | 책임 | 레이어 |
| --- | --- | --- |
| `backend/core/` | 공통 — 설정, DB, 인증, 로깅, LLM 어댑터, 리스크 엔진, 페이퍼 계정, `as_of` 유틸 | Providers/Repo/Service 일부 |
| `backend/core/models/` | SQLAlchemy ORM 베이스와 공유 테이블 (15개) | Types/Repo |
| `backend/markets/` | 시장별 어댑터 (`kr/`, `us/`): 거래일·세금·종목코드·통화 | Providers |
| `backend/brokers/` | 주문 라우팅 + 페이퍼 회계 + equity curve 수학 | Repo / Service |
| `backend/data/` | 데이터 인제스천 (`price/`, `fundamental/`, `disclosures/`, `news/`, `macro/`, `universe/`) | Repo |
| `backend/data/universe/membership_query.py` | 시점별 멤버십 O(1) 조회 — survivorship bias 차단 | Repo |
| `backend/data/news/aliases.py` | KR/US 별칭 사전 (NER 강화) | Repo |
| `backend/data/news/historical_backfill.py` | Month-chunked resumable 백필 orchestrator | Service (pure) |
| `backend/fundamental/` | 기본적 분석 모듈 | Service |
| `backend/technical/` | 기술적 분석 모듈 (`signals/`) | Service |
| `backend/information/` | 정보 분석 모듈 (LLM 분류기, 신뢰도, tickers_mentioned) | Service |
| `backend/decision/` | 복합 결정 엔진 + 사이저 + 드리프트 + 통화 mismatch 가드 | Service |
| `backend/training/` | 종목/클러스터별 OLS 학습 + walk-forward | Service |
| `backend/analytics/` | Attribution / data quality / freshness — 순수 함수 | Service (pure) |
| `backend/backtest/` | Replay + Re-scoring 엔진 (survivorship-bias 가드 포함) | Service (pure + DB glue) |
| `backend/scan/` | 시그널 dry-run 엔진 (Pre-trade preview) | Service (pure) |
| `backend/runtime/` | 봉 마감 감지, 스케줄러 (16+ 잡), 웹소켓 브로드캐스트 | Runtime |
| `backend/routes/` | FastAPI 라우터 (26개 모듈) | UI(서버) |
| `backend/alembic/` | DB 마이그레이션 (10개) | tooling |

## 운영자 도구 카탈로그

| 도구 | 위치 | 책임 |
| --- | --- | --- |
| Kill switch | `POST /api/admin/kill-switch` | 모든 페이퍼 계정의 오픈 포지션 청산 + 스케줄러 일시정지 |
| Scheduler pause/resume | `POST /api/admin/scheduler/{pause,resume}` | 잡 일괄 토글 |
| Cluster weight override | `PUT /api/overrides/{cluster_id}` | 학습된 weights를 운영자가 즉시 override (audit 자동) |
| Risk gate stats | `GET /api/risk/{summary,recent-failures}` | 6개 한도 통과율 모니터링 |
| Data quality | `GET /api/data-quality/{freshness-by-ticker,gaps}` | DEAD/STALE 종목 + 가격 갭 |
| Historical news backfill | `POST /api/backfill/news/start` | BIGKinds + GDELT chip-away |
| Historical macro backfill | `POST /api/backfill/macro/run` | FRED + ECOS 10년 windows |
| Backtest persistence | `POST /api/backtest/{run,rescoring}` with `persist=true` | 결과를 `backtest_runs` 영구화, comparison view |
| Walk-forward weekly job | `backtest.walk_forward.weekly` (enabled=False default) | 학습 직후 자동 walk-forward |
| Health summary | `GET /api/health/summary` | 7개 영역 OK/WARN/ERROR 통합 |
| Toast notifications | Browser-side polling | 새 BUY/SELL + tier 전이 실시간 알림 |

## 결정-시점 안전 invariant

| Invariant | 강제 위치 |
| --- | --- |
| `as_of` 누락 거부 | `core/as_of.py @require_as_of` |
| 시장 통화 ≠ 계정 통화 → HOLD | `decision/runner.py:_decide_one` |
| 학습 전이라도 운영자 override 적용 | `decision/runner.py: merged_weights = {**learned, **operator}` |
| 페이퍼 계정 KR/US 자동 분리 | `main.py` lifespan 부트스트랩 + `routes/paper.py:_resolve_account_name` |
| Backtest survivorship bias 차단 | `backtest/rescoring_runner.py:restrict_to_indices` |
| Live 모드 활성화 | JWT live 플래그 + "I UNDERSTAND THE RISK" 확인 문구 |
| 리스크 검사 6개 한도 | `core/risk.py:RiskEngine.check` 모든 주문 의도 통과 |
