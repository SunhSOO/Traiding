# Red-Green 자동매매 전략

## 목적

`Red-Green Signals + SL/TP v9 (Trail Fix)` Pine Script 전략을 SUPERRICH의 메인 자동매매 전략으로 구현합니다. 목표는 차트 신호를 서버에서 재현 가능한 상태 머신으로 바꾸고, MT5 주문 실행까지 안전하게 연결하는 것입니다.

## 기본 파라미터

| 항목 | 기본값 | 설명 |
| --- | --- | --- |
| `k` | 3 | K 라인 SMA 기간 |
| `d` | 3 | K 라인 smoothing 기간 |
| `d1_input` | 10 | 보조 추세선 기간 |
| `mult` | 2.0 | BB 표준편차 배수 |
| `shortPeriod` | 7 | 단기 high/low 기간 |
| `midPeriod` | 21 | 중기 high/low 및 BB 기준 기간 |
| `longPeriod` | 50 | 장기 high/low 기간 |
| `Periods` | 10 | Supertrend ATR 기간 |
| `Multiplier` | 3.0 | Supertrend ATR 배수 |
| `tp1_mult` | 1.5 | TP1 R:R 배수 |

## 지표 계산

### CEN BB-ICHI 트렌드 라인

- `kline = sma(close, k)`
- `smoothedsource = sma(kline, d)`
- `trendline1 = sma(kline, d1_input)`
- 색상은 `kline > smoothedsource`이면 청록, 아니면 빨강입니다.
- 자동매매 판단에는 색상 자체가 아니라 산출값만 사용합니다.

### BB-ICHI 클라우드

- `basis = sma(close, midPeriod)`
- `dev = mult * stdev(close, midPeriod)`
- `upper = basis + dev`
- `lower = basis - dev`
- `upperline1`, `upperline2`, `lowerline1`, `lowerline2`는 Pine 원본의 high/low 평균식을 그대로 재현합니다.
- `cloudGreen = upperline1 >= upperline2`
- `cloudRed = upperline1 < upperline2`

### Kizun 매매선

`kizun`은 단기, 중기, 장기 high/low 여섯 값을 평균낸 매매선입니다.

진입 시 최초 SL은 `kizun`을 사용합니다.

### Supertrend

- ATR 기반 `up`, `dn`을 계산합니다.
- 이전 봉의 `up1`, `dn1`을 사용해 trend를 전환합니다.
- `trend = 1`은 상승 추세, `trend = -1`은 하락 추세입니다.

## 신호 조건

### 롱 조건

- `longMatch = trend == 1 and cloudGreen`
- `prevLongMatch = trend[1] == 1 and upperline1[1] >= upperline2[1]`
- `freshLong = longMatch and not prevLongMatch`
- `goLong = freshLong and posState != 1`

롱 진입은 `kizun < close`일 때만 유효합니다.

### 숏 조건

- `shortMatch = trend == -1 and cloudRed`
- `prevShortMatch = trend[1] == -1 and upperline1[1] < upperline2[1]`
- `freshShort = shortMatch and not prevShortMatch`
- `goShort = freshShort and posState != -1`

숏 진입은 `kizun > close`일 때만 유효합니다.

### 반대 신호 청산

- `closeLong = goShort and posState[1] == 1`
- `closeShort = goLong and posState[1] == -1`

서버 구현에서는 반대 신호 발생 시 기존 포지션을 먼저 청산한 뒤, 신규 진입을 검토합니다.

## 포지션 상태 머신

| 상태 | 의미 |
| --- | --- |
| `posState = 0` | 포지션 없음 |
| `posState = 1` | 롱 포지션 보유 |
| `posState = -1` | 숏 포지션 보유 |
| `tp1Hit` | TP1 도달 여부 |
| `trailActive` | 트레일링 SL 활성 여부 |
| `posEntry` | 진입가 |
| `posSL` | 현재 SL |
| `posTP1` | TP1 가격 |

## 진입 가격과 리스크

서버의 기준 진입가는 봉 마감 신호 이후 MT5 시장가 체결 가격입니다. Pine 원본은 `close`를 `posEntry`로 사용하므로, 백테스트와 실거래 비교 시 다음 값을 모두 기록해야 합니다.

- 신호 봉 종가
- 주문 요청 가격
- 실제 체결 가격
- 슬리피지

롱 리스크:

- `risk = entry - kizun`
- `TP1 = entry + risk * tp1_mult`

숏 리스크:

- `risk = kizun - entry`
- `TP1 = entry - risk * tp1_mult`

## 청산 규칙

### TP1 전

- 롱: `low <= posSL`이면 SL 청산
- 숏: `high >= posSL`이면 SL 청산

### TP1 도달

- 롱: `high >= posTP1`
- 숏: `low <= posTP1`
- TP1 도달 시 `posSL = posEntry`
- 이후 트레일링 모드로 전환합니다.

### 트레일링

- 롱은 `up > posEntry`일 때 트레일링 활성화가 가능합니다.
- 활성화 이후 `up > posSL`이면 `posSL = up`으로 올립니다.
- 숏은 `dn < posEntry`일 때 트레일링 활성화가 가능합니다.
- 활성화 이후 `dn < posSL`이면 `posSL = dn`으로 내립니다.

### 트레일 청산

- 롱: TP1 이후 `low <= posSL`
- 숏: TP1 이후 `high >= posSL`

## 주문 정책

- 1개 심볼, 1개 전략 인스턴스당 같은 방향 포지션은 1개만 허용합니다.
- 반대 신호가 발생하면 기존 포지션 청산을 먼저 요청합니다.
- 기존 청산 성공이 확인되지 않으면 신규 반대 포지션을 열지 않습니다.
- SL, TP1, Trail 이벤트는 모두 전략 실행 로그에 기록합니다.
- 실거래 전 `dry_run` 모드와 데모 계좌 실행을 필수로 거칩니다.

## 미정 항목

- 기본 거래 심볼
- 기본 타임프레임
- 주문 수량 계산 방식: 고정 lot 또는 계좌 리스크 비율
- TP1에서 부분청산을 할지, Pine 원본처럼 본절 이동만 할지
- 최대 동시 전략 수
- 거래 허용 시간대와 뉴스 회피 규칙
