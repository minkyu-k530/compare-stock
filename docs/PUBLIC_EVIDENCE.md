# 공개 자료로 확인된 통신 방식과 확인되지 않은 것

확인일: 2026-10-27. 이 문서는 **회사 공개 발표·운영 공지·공식 개발자 문서**를 근거로 한 조사 노트입니다. 이번 조사에서 앱의 새 패킷을 측정하지는 않았습니다. 근거 ID, 두 회사의 경쟁 구조, 케이스별 이유와 4주 계획은 [아키텍처 추론 케이스 스터디](CASE_STUDY_ROADMAP.md)에 있습니다. 비복호화 관측 절차는 [NETWORK_METADATA](NETWORK_METADATA.md)에 준비했습니다.

| 출처 | 확인할 수 있는 사실 | 이 자료만으로 알 수 없는 것 |
|---|---|---|
| [토스증권 SLASH 22 「실시간 시세 적용기」](https://static.toss.im/assets/homepage/slash22/pt-session/SLASH22_%EB%B0%95%EC%84%B1%EC%9A%B0%EB%8B%98.pdf) | 발표 당시 실시간 시세 구성에서 Toss App ↔ 시세 Gateway의 HTTP API와 Toss App ↔ 시세 Realtime의 WebSocket 경로를 도식화했고, WebSocket 실패 시의 fallback도 소개했다. | 2026년 현재의 모든 화면·종목에서 같은 경로인지, HTTP/2·HTTP/3 버전, 한국투자 앱 대비 속도 우위의 원인 |
| [SLASH23 「실시간 시세 데이터 안전하고 빠르게 처리하기」](https://toss.im/slash-23/session-detail/B1-7), 2023 | 내부 시세 수신·처리 경로의 병목을 구분하고 전달 방식과 실행 구조를 개선한 사례를 공개했다. | 모바일 최초 표시 지연, 2026년 전체 구성, 전사 Kafka 교체 여부 |
| [토스증권 Kafka 데이터센터 이중화 #1](https://toss.tech/article/kafka-distribution-1), 2025-01-08 | 두 데이터센터의 Active-Active 서비스와 Kafka 이중화의 선택 이유를 설명했다. | 특정 화면이 어떤 데이터센터나 내부 호출 경로를 거쳤는지 |
| [토스증권 Visualize Lineage](https://toss.tech/article/toss-securities-visualize-lineage), 2025-08-11 | 실시간 파이프라인·CQRS 활용과 시세 데이터의 분석·집계용 파생 경로를 공개했다. | 앱 가격이 모든 분석 저장소를 순차적으로 거친다는 주장 |
| [한국투자증권 Open API 서비스 소개](https://apiportal.koreainvestment.com/apiservice-summary) 및 [공식 샘플 저장소](https://github.com/koreainvestment/open-trading-api) | **개발자용 Open API**가 REST와 WebSocket 실시간 시세 구독을 제공한다. | 일반 고객이 쓰는 **한국투자 모바일 앱**이 어떤 내부 프로토콜·캐시·화면 구성으로 동작하는지 |
| [한국투자 관심종목 점검 안내](https://securities.koreainvestment.com/main/customer/notice/Notice.jsp?cmd=TF04ga000002&num=46150), 2026-02-06 | 서버 증설 중 MTS·HTS·웹의 목록 변경을 제한하고 기존 조회는 허용한다고 안내했다. | 별도 마이크로서비스·CQRS·캐시의 존재, 종목 상세의 속도 원인 |

“토스는 WebSocket, 한국투자는 폴링이어서 토스가 빠르다”는 주장은 **현재 근거로 지지되지 않습니다.** 양측 자료는 서로 다른 시점·제품 경로를 설명하며 현재 모바일 앱의 차이를 입증하지 않습니다. 한국투자 Open API의 WebSocket 지원만으로 MTS의 폴링 가설을 반증할 수도 없습니다.

## 프로젝트에서 사용할 가설 순서

1. **화면 단계 차이:** 첫 시각 반응은 언제이고, 값이 실제로 보이는 시점은 언제인가?
2. **클라이언트 렌더링 차이:** 프레임 지연은 어느 단계에서 발생하는가?
3. **데이터 공급 차이:** 캐시·사전 로딩·연결 재사용·업데이트 주기 후보가 있는가? 화면 표시만으로 서버 처리시간을 추정하지 않는다.
4. **전송 메타데이터:** 본인 기기의 비복호화 관찰에서 TCP/UDP, 연결 재사용 등 무엇을 확인했는가? HTTP 버전·WebSocket·gRPC는 확인 가능한 근거가 생길 때만 쓴다.
5. **구조 갱신:** 관측과 양립하는 후보를 남기고, 부하·장애에 대한 회사 공개 사례와 대조해 내 설계 판단을 적는다. 모의 구현은 후보 동작을 이해하기 위한 선택적 보조 실험이며, 회사 내부 구조의 증거로 쓰지 않는다.

토스증권의 [RCA 사례](https://toss.im/career/article/sec_tech_talk_server)도 문제 인지, 자료 수집, 특이점 파악, 가설 생성, 가설 검증의 흐름을 강조합니다. 이 문서는 그 흐름을 따라 공개 근거, 실측 근거, 자체 재현 결과를 서로 구분합니다.
