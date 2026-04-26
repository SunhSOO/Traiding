# Red-Green 전략 Pine 요약

이 문서는 사용자가 제공한 Pine Script `Red-Green Signals + SL/TP v9 (Trail Fix)`를 서버 구현용으로 요약한 레퍼런스입니다. 원본 Pine Script는 TradingView 차트 표시와 알림을 포함하지만, 서버에서는 주문 판단과 상태 전이만 권위 소스로 사용합니다.

## 핵심 구성

- CEN BB-ICHI 트렌드 라인
- BB-ICHI 클라우드
- Kizun 매매선
- Supertrend
- SL/TP1/Trailing SL 상태 머신

## 신호 핵심

롱 신호:

```text
trend == 1
upperline1 >= upperline2
이전 봉은 위 조건이 아니었음
현재 포지션이 롱이 아님
kizun < close
```

숏 신호:

```text
trend == -1
upperline1 < upperline2
이전 봉은 위 조건이 아니었음
현재 포지션이 숏이 아님
kizun > close
```

## 상태 처리 순서

```text
1. 기존 포지션의 트레일링 SL 업데이트
2. SL, TP1, Trail 도달 여부 확인
3. TP1 도달 시 SL을 본절로 이동
4. SL 또는 Trail 청산 처리
5. 신규 진입 처리
```

이 순서를 바꾸면 Pine 원본과 다른 결과가 나올 수 있으므로 서버 구현에서도 같은 처리 순서를 유지합니다.
