# SUPERRICH MT5 시스템 트레이딩 대시보드

SUPERRICH는 MetaTrader 5 자동매매 시스템을 모니터링하고 제어하기 위한 웹 기반 대시보드입니다. 계좌 상태, 포지션, 주문, 전략 실행 상태를 한 화면에서 확인하고, 데모/실거래 환경에 맞춰 전략을 운용할 수 있도록 구성되어 있습니다.

## 빠른 시작

### 프론트엔드만 실행하기

데모 화면을 빠르게 확인할 때 사용합니다.

```bash
cd superrich
python -m http.server 8080
```

브라우저에서 `http://localhost:8080`을 엽니다.

### 백엔드와 함께 실행하기

MT5 터미널과 연동해 전체 기능을 확인할 때 사용합니다.

```bash
cd backend
pip install -r requirements.txt
python main.py
```

브라우저에서 `http://localhost:8000`을 엽니다.

## 주요 기능

- **대시보드**: 계좌 개요, 손익, 포지션 상태를 실시간으로 확인
- **실시간 트레이딩**: 차트, 주문 패널, 포지션 관리 기능 제공
- **포트폴리오**: 자산 배분과 리스크 상태 점검
- **전략 관리**: 자동매매 전략 제어, 백테스트, 실행 상태 관리
- **거래 내역**: 필터 기반 거래 기록 조회
- **분석**: 성과 차트와 캘린더 히트맵 제공
- **설정**: MT5 연결, 리스크 한도, 알림 설정 관리

## 기술 스택

| 계층 | 기술 |
| --- | --- |
| 프론트엔드 | HTML5, CSS, JavaScript |
| 백엔드 | Python, FastAPI |
| MT5 연동 | MetaTrader5 Python 패키지 |
| 실시간 통신 | WebSocket |

## 요구 사항

- Python 3.10 이상
- MetaTrader 5 터미널
- 최신 웹 브라우저

## 참고 문서

- [ARCHITECTURE.md](ARCHITECTURE.md): 시스템 구조와 레이어 의존성
- [docs/FRONTEND.md](docs/FRONTEND.md): 프론트엔드 화면 구성과 UI 규칙
- [docs/RELIABILITY.md](docs/RELIABILITY.md): 실거래 안정성 기준
- [docs/SECURITY.md](docs/SECURITY.md): 보안과 권한 경계
