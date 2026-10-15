# 공개 자료로 확인된 통신 방식과 확인되지 않은 것

확인일: 2026-10-15. 이 문서는 **회사 공개 발표와 공식 개발자 문서**를 근거로 한 조사 노트입니다. 앱의 현재 패킷을 직접 측정한 기록은 아직 없습니다. 비복호화 캡처 분석 도구와 절차는 [NETWORK_METADATA](NETWORK_METADATA.md)에 준비했습니다.

| 출처 | 확인할 수 있는 사실 | 이 자료만으로 알 수 없는 것 |
|---|---|---|
| [토스증권 SLASH 22 「실시간 시세 적용기」](https://static.toss.im/assets/homepage/slash22/pt-session/SLASH22_%EB%B0%95%EC%84%B1%EC%9A%B0%EB%8B%98.pdf) | 발표 당시 실시간 시세 구성에서 Toss App ↔ 시세 Gateway의 HTTP API와 Toss App ↔ 시세 Realtime의 WebSocket 경로를 도식화했고, WebSocket 실패 시의 fallback도 소개했다. | 2026년 현재의 모든 화면·종목에서 같은 경로인지, HTTP/2·HTTP/3 버전, 한국투자 앱 대비 속도 우위의 원인 |
| [토스증권 Server Developer / Market Platform 공고](https://toss.im/career/job-detail?job_id=4071141003&company=%ED%86%A0%EC%8A%A4%EC%A6%9D%EA%B6%8C&sub_position_id=7696445003) | 현재 시세팀 업무 범위에 대고객 API/WebSocket과 실시간 대용량 트래픽·저지연 이벤트 처리가 포함된다. | 특정 사용자 화면의 전체 응답시간이나 동작별 사용 프로토콜 |
| [한국투자증권 KIS Developers](https://apiportal.koreainvestment.com/docs) 및 [공식 실시간 시세 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py) | **개발자용 Open API**가 REST와 WebSocket 실시간 시세 구독을 제공한다. | 일반 고객이 쓰는 **한국투자 모바일 앱**이 어떤 내부 프로토콜·캐시·화면 구성으로 동작하는지 |

따라서 “토스는 WebSocket, 한국투자는 폴링이어서 토스가 빠르다”는 가설은 **현재 근거와 맞지 않습니다.** 양측 공개 자료 모두 WebSocket을 설명하지만 서로 다른 제품 경로를 설명하며, 외부 앱의 한 화면에서 어떤 방식이 사용되는지는 아직 확인되지 않았습니다.

## 프로젝트에서 사용할 가설 순서

1. **화면 단계 차이:** 첫 시각 반응은 언제이고, 값이 실제로 보이는 시점은 언제인가?
2. **클라이언트 렌더링 차이:** 프레임 지연은 어느 단계에서 발생하는가?
3. **데이터 공급 차이:** 캐시·사전 로딩·연결 재사용·업데이트 주기 후보가 있는가? 화면 표시만으로 서버 처리시간을 추정하지 않는다.
4. **전송 메타데이터:** 본인 기기의 비복호화 관찰에서 TCP/UDP, 연결 재사용 등 무엇을 확인했는가? HTTP 버전·WebSocket·gRPC는 확인 가능한 근거가 생길 때만 쓴다.
5. **자체 통제 실험:** 동일 데이터·클라이언트·서버의 모의 시세 시스템에서 한 변수씩 바꾸어 지연·누락·오류·서버 자원을 측정한다. 이것으로 **자체 시스템의** 원인을 검증하며, 타사 앱 내부 구조로 일반화하지 않는다.

토스증권의 [RCA 사례](https://toss.im/career/article/sec_tech_talk_server)도 문제 인지, 자료 수집, 특이점 파악, 가설 생성, 가설 검증의 흐름을 강조합니다. 이 문서는 그 흐름을 따라 공개 근거, 실측 근거, 자체 재현 결과를 서로 구분합니다.
