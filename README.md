# Stock Speed Lab

토스 앱의 증권 화면과 한국투자 앱의 **사용자 체감 속도**를 같은 조건에서 기록하고 페어로 비교하는 로컬 실험 도구입니다. 2026-10-15에 Galaxy S24+ 화면 녹화 4건을 예비 판독해 **서로 다른 조건 2개에 각 1페어씩** 로컬 DB에 기록했습니다. 본 실험 60회는 아직 시작하지 않았습니다. 예비 원본·프레임·DB는 Git에서 제외하며, 화면의 **가상 예시 데이터**와 실측을 구분합니다. 예비 1페어씩으로 두 앱의 일반적 속도 우열을 판단하지 않습니다.

## 실행

로컬 대시보드 실행에는 Python 3.10 이상만 있으면 됩니다. Galaxy S24+ 원본 영상을 Mac으로 옮기고 프레임 시각을 추출하는 별도 SOP에는 Android SDK Platform Tools와 FFmpeg의 `ffprobe`가 필요합니다. 네트워크 비교에는 휴대폰의 PCAPdroid 앱이 추가로 필요하며, 분석기는 Python 표준 라이브러리만 사용합니다.

```bash
python3 -m stocklab.server
```

브라우저에서 `http://127.0.0.1:8765`를 엽니다. 데이터는 `data/stocklab.sqlite3`에 저장됩니다. 같은 신뢰할 수 있는 Wi-Fi의 휴대폰에서 입력하려면 `python3 -m stocklab.server --host 0.0.0.0`으로 실행하고, PC의 LAN IP와 포트 `8765`로 접속합니다. 이 로컬 서버에는 로그인 기능이 없으므로 공용망에 노출하지 마세요.

재촬영 예비값 4건은 별도 `data/pilot-20261015-1619.sqlite3`에 있습니다. 이 DB를 다시 열 때는 `python3 -m stocklab.server --port 8767 --db data/pilot-20261015-1619.sqlite3`를 사용합니다. 기본 `8765` DB와 본 실험 DB에 예비값을 합치지 않습니다.

## 지금 가능한 것

- 영상을 브라우저에서 **로컬로만** 열어 시점을 대략 찾고, 영상 편집기에서 확인한 **프레임 번호와 고정 FPS** 또는 **두 프레임의 표시 시각(PTS)**으로 시간을 계산합니다. 계산 근거인 시작·끝 값도 함께 저장합니다. 브라우저 재생 위치로 구한 근사값은 별도 그룹으로 분리합니다. 영상 파일은 서버로 전송되지 않습니다.
- 실측 기록을 폼 또는 UTF-8 CSV로 SQLite에 저장하고 원자료를 CSV로 내보냅니다.
- 한 페어의 첫 시도에서 **저장 후 반대 앱 준비**를 누르면 동일 `run_id`와 실험 조건을 유지하고 앱·실행 순서를 바꿉니다. 두 번째 시도에서는 **기록 저장**을 누릅니다. 새 영상의 프레임/PTS 판독값은 다시 입력해야 합니다.
- 시나리오·**종목 코드·키보드 표시 상태**·촬영 방식·시간 판독 방식·웜/콜드·장중/장외·완료 기준·FPS·**기기·네트워크**가 같은 **비교 그룹**으로 묶습니다. 그룹 안에서 `run_id` 조건에 맞는 토스/한투 완료 기록 하나씩만 속도 통계에 사용합니다. 다른 조건, 짝 없음, 중복, 오류·시간 초과는 제외 사유와 상태 건수로 보여줍니다.
- 페어별 `Toss − KIS` 시간 차이의 중앙값과 앱별 중앙값을 표시합니다. p95는 완료 페어 20쌍 이상일 때만 표시하며, 5쌍 이상일 때 탐색적 부트스트랩 95% 구간을 표시합니다. 음수는 해당 표본에서 토스의 시간이 낮다는 뜻입니다.
- 실제 기록과 **가상 예시 데이터**를 별도 화면으로 분리합니다.

앱 버전과 실행 순서도 별도 필드로 기록합니다. 앱 버전은 두 회사가 달라 일치 조건으로 쓰지 않으므로 원자료에서 직접 검토해야 합니다. 실제 종목은 `stock_code`, 검색어·결과 행 위치·로그인·캐시 상태·측정 시간대 등은 `notes`에 기록합니다. 상태 건수는 기록한 시도만 세며, 누락된 시도를 추정하지 않습니다.

## 측정의 정의

| 필드 | 정의 |
|---|---|
| `capture_method` | `external_camera`, `screen_recording`, `manual_other`, `unspecified`. 같은 방식끼리만 분석 |
| `timing_basis` | `fixed_fps_frames`, `frame_timestamps`, `playback_seek`, `manual`, `unspecified`. 근사값과 프레임 판독값을 분리 |
| `start_frame`, `end_frame` | 판독한 시작·끝 프레임 번호. 고정 FPS 계산에는 필수이며, PTS 계산에도 함께 남겨 재판독 가능 |
| `start_pts_ms`, `end_pts_ms` | 각 프레임의 영상 표시 시각(PTS). 가변 FPS 영상은 두 시각의 차이로 계산 |
| `stock_code`, `keyboard_state` | 선택한 종목의 6자리 코드와 탭 전 검색 결과의 키보드 상태. 서로 다르면 별도 비교 그룹 |
| `start_state`, `market_phase` | 웜/콜드, 장중/장외를 분리. 기존 기록은 `unspecified` |
| `completion_criterion` | 화면에서 완료로 판단할 조건. 같은 문구인 기록끼리만 묶음 |
| `capture_fps` | 고정 FPS 영상의 프레임 속도. FPS가 다르면 별도 그룹 |
| `status` | `completed`, `timeout`, `error`. 완료된 두 앱 기록만 속도 통계에 사용 |
| `elapsed_ms` | 완료: 시작부터 콘텐츠 표시까지. 시간 초과/오류: 시작부터 관찰 종료까지. 촬영 방식별 시작점 정의는 아래 참고 |
| `first_response_ms` | 탭에서 첫 시각적 반응까지. 선택 항목 |
| `content_ms` | 첫 반응부터 콘텐츠 표시까지의 **구간 길이**. 선택 항목 |
| `frame_jank_pct` | 별도 시스템 추적에서 구한 프레임 끊김 비율. 입력할 때 추출 방법을 메모에 기록 |
| `network_rtt_ms` | 별도 네트워크 관찰에서 구한 왕복시간. 앱 API 처리시간과 다름 |
| `protocol_observation` | 사용자가 직접 확인한 전송 메타데이터. HTTP 버전이나 인과를 자동 판정하지 않음 |

`first_response_ms + content_ms`는 `elapsed_ms`를 넘을 수 없습니다. `run_id`는 한 번의 토스/한투 교차 측정 쌍에서 같게 두고, 새로운 쌍마다 고유하게 만듭니다. 예: `20261015-wifi-search-01`.

외부 촬영은 **손가락 접촉**, 화면 녹화는 **탭 표시가 나타난 프레임**을 시작점으로 삼습니다. 30fps 영상은 프레임 간격이 약 33.3ms, 60fps는 약 16.7ms입니다. 각 경계 프레임 판독 오차와 화면 녹화 부하, 탭 표시 지연을 함께 기록하세요. 기존 CSV에서 구조화 필드가 비어 있으면 `unspecified`로 가져오며, 명시된 그룹과 합치지 않습니다.

첫 30페어의 조건·실행 순서·중단 규칙은 [첫 실측 실험 계획](docs/EXPERIMENT_PLAN.md)에 고정했습니다. 촬영·판독의 자세한 절차는 [docs/MEASUREMENT.md](docs/MEASUREMENT.md)를 참고하세요. 빈 [CSV 양식](http://127.0.0.1:8765/api/template.csv)은 실행 중인 서비스에서도 받을 수 있습니다.

Galaxy S24+ 한 대로 시작하는 경우에는 [화면 녹화 4회 예비 측정](docs/GALAXY_S24_PLUS_PILOT.md)을 먼저 따릅니다. 예비 측정은 본 실험의 30페어에 합산하지 않습니다.

Mac에서 S24+ 영상을 복사하고 프레임·PTS를 판독한 뒤 결과를 포트폴리오로 정리하는 전체 순서는 [Mac + Galaxy S24+ SOP](docs/MAC_GALAXY_S24_PLUS_SOP.md)에 있습니다. `tools/measure_video_pts.py`는 FFmpeg의 `ffprobe`로 원본 영상 프레임 시각을 읽고, 눈으로 고른 시작·끝 프레임의 시간 차이와 SHA-256을 출력합니다.

예비 측정과 본 실험은 SOP에 따라 **서로 다른 SQLite 파일**로 실행합니다. 본 실험을 마친 뒤 `tools/build_public_report.py --db data/main-SESSION.sqlite3 --ledger trial-ledger.csv --out public/index.html`로 원본 행·메모·영상 경로 없이 집계 HTML을 생성할 수 있습니다. 대장의 60행 결과가 모두 채워지지 않으면 생성이 중단됩니다.

저장소 루트의 `amplify.yml`은 이 `public/` 정적 보고서만 AWS Amplify에 배포하도록 설정합니다. 파일 생성과 비식별 검토 전에는 배포하지 마세요.

첫 세션의 60회 시도 대장은 측정 시작 전에 만듭니다. 세션 ID는 날짜·망·시나리오가 드러나게 정하고, 같은 ID로 세션을 두 번 만들지 마세요.

```bash
python3 tools/make_trial_ledger.py --session-id 20261015-wifi-search > trial-ledger.csv
```

대장의 `position`은 앱 입력 폼의 `run_order` 1~4와 같습니다. 시작한 모든 시도의 `actual_local_time`과 결과(`completed`, `timeout`, `error`, `invalid_capture`)를 채우고, 시작하지 못한 시도는 `not_started`와 이유를 남깁니다. 촬영 실패도 행을 지우지 마세요. 대장은 앱 측정 CSV와 별개로 보관하며, 영상 파일명과 SHA-256을 포함할 수 있으므로 공개 전 개인정보를 확인하세요. `trial-ledger.csv`는 Git에 올리지 않도록 제외했습니다.

## 네트워크 메타데이터 비교 (비복호화)

화면 시간과 별도로, 같은 탭 동작에서 앱이 **어떤 방식으로 연결했는지**를 비교합니다. PCAPdroid로 앱 하나씩 캡처한 PCAP/PCAPNG를 `tools/analyze_pcap.py`가 읽어 TCP+TLS/QUIC 구분, TLS 버전, ALPN 제안 목록, 세션 재개, 탭 1회당 신규·재사용 연결과 DNS 질의, 탭 후 첫 응답까지의 시간, 요청 없이 서버 데이터가 반복되는 연결을 표로 냅니다. 패킷 내용은 복호화하지 않으며 보고서에 기기 주소·HTTP 경로·본문을 쓰지 않습니다.

```bash
python3 tools/tap_metronome.py --out captures/net/toss-01.marks --count 10 --interval 15
python3 tools/analyze_pcap.py toss=captures/net/toss-01.pcap kis=captures/net/kis-01.pcap --marks toss=captures/net/toss-01.marks --marks kis=captures/net/kis-01.marks --out captures/net/report-01.md
```

첫 명령은 캡처 중 15초마다 탭 신호를 보내고 그 시각을 기록합니다. 두 번째 명령은 기록된 탭과 맞는 네트워크 동작만 요약에 넣고 나머지는 배경으로 표시합니다. 촬영·캡처 절차, 해석 표, 한계는 [비복호화 네트워크 메타데이터 비교](docs/NETWORK_METADATA.md)에 있습니다. 이 결과도 HTTP/3·WebSocket 사용이나 속도 차이의 원인을 확정하지 않습니다.

공개 기술자료로 **확인된 것과 확인되지 않은 것**은 [docs/PUBLIC_EVIDENCE.md](docs/PUBLIC_EVIDENCE.md)에 따로 정리했습니다. 2022년 토스증권 발표에는 시세 WebSocket/HTTP API 경로가 나오고, 한국투자증권 개발자용 Open API도 WebSocket을 제공합니다. 두 모바일 앱이 지금 같은 방식으로 동작하거나 이 방식이 속도 차이의 원인이라는 뜻은 아닙니다.

## 재현 가능한 결과로 발전시키기

1. `종목 검색`, `종목 상세`, `차트 열기`, `콜드 시작`을 별도 실험으로 측정합니다. 동일 기기·종목·시장 상태·네트워크에서 앱 순서를 ABBA 또는 무작위로 교차하고, 시나리오와 시작 상태별로 20~30쌍 이상 수집합니다.
2. 실패와 로그인 재요구도 `timeout`/`error`로 기록합니다. 속도 중앙값·p95에는 완료된 페어만 들어가며, 실패 건수는 따로 표시됩니다.
3. `탭→첫 반응`과 `탭→콘텐츠 표시` 차이, Perfetto의 프레임 지연, [비복호화 전송 메타데이터](docs/NETWORK_METADATA.md)를 함께 봅니다. 화면 완료 시간과 네트워크 구간의 중앙값을 비교하면 네트워크 몫과 클라이언트 몫을 대략 나눠 볼 수 있습니다. 화면 표시 시각은 서버 응답시간이나 최신 시세 도착 시각이 아닙니다.
4. **프로토콜이 원인이라는 결론은 내리지 않습니다.** 캐시, 사전 로딩, 렌더링, 연결 재사용, 시세 갱신 주기 등을 가설로 둡니다.
5. 서버 개발 포트폴리오로는 동일 데이터·동일 클라이언트의 **자체 모의 시세 서버**에서 폴링/푸시, 캐시, 팬아웃, 페이로드 같은 변수를 한 번에 하나씩 바꿔 p50·p95·오류율·갱신 누락을 측정하는 다음 실험이 필요합니다. 외부 앱 관찰과 자체 서버의 인과 실험을 분리해 제시합니다.

## API

| Method | Path | 용도 |
|---|---|---|
| `GET` | `/api/observations?source=real` | 실제 기록 조회 (`source=demo`는 가상 예시) |
| `POST` | `/api/observations` | JSON 관찰값 1개 저장 |
| `POST` | `/api/import` | CSV 가져오기 |
| `GET` | `/api/analysis?source=real` | 페어 분석 결과 |
| `GET` | `/api/export.csv?source=real` | 원자료 CSV 내보내기 |
| `POST` | `/api/demo` | 명시적 가상 예시 생성 |

## 검증

```bash
python3 -m unittest discover -s tests -v
```

이 프로젝트는 두 회사와 무관한 개인 실험 도구입니다. 공개할 CSV·영상에서 계좌 번호, 잔고, 알림, 인증 정보 등 개인정보를 제거하세요.
