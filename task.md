# MT5 시스템 트레이딩 웹 대시보드 - 작업 목록

> [!NOTE]
> **문서 상태 (2026-07-10 기준): 초기 작업 목록 — 역사적. `plan.md`(2026-05-26)의 짝 문서.**
> 이 체크리스트는 초기 UI 계획(MT5 FX 대시보드, 6-Phase 프론트엔드+백엔드)의 작업 목록입니다. 이후 프로젝트는 **무료 데이터 전용 3-모듈 자동매매 시스템**(펀더멘털+기술적+정보 분석, KR KOSPI200+KOSDAQ150 / US S&P500+NASDAQ100 주식)으로 크게 확장됐습니다. 저장소는 SQLite가 아닌 **PostgreSQL**, FastAPI 서버는 이미 구축됨(routes 27개). MT5는 **FX/금 전용**으로만 연동(dry_run/live 게이트), 주식은 **PaperBroker(시뮬레이션)**만 배선 — KIS(KR)/Alpaca(US) 실거래 어댑터는 미구현이라 실전 전 페이퍼 관측이 필수입니다.
> 아래 미체크 박스는 **현재 상태를 반영하지 않습니다**(당시 UI 스코프 기준). 현재 상태·설계·일일 로그는 `WORK_LOG.md`(일자별 권위 기록)와 `GAPS.md`를 참조하세요. 아래 내용은 삭제하지 않고 초기 작업 기록으로 보존합니다.

## Phase 1: 기반 구축
- [ ] CSS 디자인 시스템 (`variables.css`, `base.css`, `components.css`, `layout.css`)
- [ ] SPA 라우터 및 사이드바 네비게이션
- [ ] 메인 `index.html` 엔트리

## Phase 2: 메인 대시보드
- [ ] Dashboard 페이지 UI 구현

## Phase 3: 트레이딩 & 포트폴리오
- [ ] Live Trading 페이지
- [ ] Portfolio 페이지

## Phase 4: 전략 & 분석
- [ ] Strategy 관리 페이지
- [ ] Analytics 성과 분석 페이지

## Phase 5: 기록 & 설정
- [ ] History 거래내역 페이지
- [ ] Settings 설정 페이지

## Phase 6: 백엔드 연동
- [ ] FastAPI 서버 구축
- [ ] MT5 Python API 연동
- [ ] WebSocket 실시간 데이터 스트리밍

## 검증
- [ ] 브라우저 확인 및 스크린샷
