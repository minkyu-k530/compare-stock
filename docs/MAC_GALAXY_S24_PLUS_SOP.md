# Mac + Galaxy S24+ 실측부터 서버 개발 포트폴리오까지

> 상태: 2026-10-15 기준 **예비 실측 4건, 본 실험 0건**. 재촬영 예비값은 종목·키보드 상태가 다른 두 조건에 각 1페어씩 들어 있어 일반적 앱 속도 우열을 말할 수 없다. 예비값은 Git에서 제외된 `data/pilot-20261015-1619.sqlite3`와 로컬 `http://127.0.0.1:8767/`에서 확인한다. 기본 대시보드 `http://127.0.0.1:8765/`는 별도 DB다. 이 SOP는 [예비 측정](GALAXY_S24_PLUS_PILOT.md), [본 실험 계획](EXPERIMENT_PLAN.md), [측정 정의](MEASUREMENT.md)를 순서대로 실행하기 위한 작업표다.

## 0. 지원 일정과 현재 제출 가능한 내용

사용자가 지정한 [토스증권 Server Developer (Product), 3년 이하 공고](https://toss.im/career/job-detail?job_id=4071141003&company=%ED%86%A0%EC%8A%A4%EC%A6%9D%EA%B6%8C&sub_position_id=8000585003)는 확인 시점에 **2026-10-15 23:59 마감**으로 표시됐다. 일정은 공고에서 다시 확인한다. 30쌍 본 실험과 후속 모의 시세 서버의 완성을 오늘 지원의 조건으로 삼지 않는다.

오늘 사실대로 쓸 수 있는 범위는 **Python/SQLite 로컬 측정 서비스 구현**, 프레임·PTS 근거와 실패 상태를 보존하는 데이터 모델, 동일 조건 페어 분석, 테스트, 그리고 실행 전 고정한 실험 계획이다. [비복호화 네트워크 메타데이터 분석기](NETWORK_METADATA.md)의 구현과 검증 범위도 쓸 수 있다. **상용 앱의 속도 우열·실제 프로토콜·원인 검증, Kotlin/Spring 모의 서버는 아직 완료 성과로 쓰지 않는다.** 현재 작업 폴더는 Git 저장소가 아니므로 공개 링크도 아직 없다.

이력서 초안 문장: “증권 앱 화면 지연 비교를 위해 프레임 PTS 근거를 저장하는 로컬 계측 서비스를 구현하고, Galaxy S24+ 예비 영상 4건을 종목·키보드 상태별 1페어씩 판독했습니다. 기기·망·시장 상태·실행 순서를 통제할 본 실험은 60회 시도 계획과 실패/중복 제외 규칙을 설계한 단계입니다. 원인 분석을 위해 PCAP을 복호화 없이 파싱해 탭 단위 신규·재사용 연결과 TLS·QUIC 협상 정보를 비교하는 분석기를 표준 라이브러리만으로 구현했고, 두 앱의 실제 캡처는 아직 수집 전입니다.” 제출 전 실제 테스트 수와 본인이 작성한 범위를 확인해 구체화한다.

## 1. Mac과 S24+ 준비

| 점검 | 완료 조건 |
|---|---|
| 기기 | Galaxy S24+, **Android 버전/One UI 버전**을 `설정 → 휴대전화 정보 → 소프트웨어 정보`에서 기록 |
| 앱 | 토스 앱과 한국투자 앱의 버전, 로그인·추가 인증 상태를 기록 |
| 화면 | 삼성 내장 **화면 녹화 → 누른 위치 표시** 사용. 개발자 옵션은 녹화 자체에 불필요 |
| 조건 | 같은 Wi-Fi, VPN·절전·화면 주사율 설정 고정. 동일 종목의 검색 결과 화면에서 시작 |
| Mac | 저장소의 Python 3.10+, `ffprobe`/`ffmpeg`, 영상 전송용 Android SDK Platform Tools 확인 |

삼성은 [S24+ 화면 녹화와 터치 표시](https://www.samsung.com/uk/support/mobile-devices/how-to-record-your-screen-on-your-samsung-galaxy-device/) 및 [기본 영상 저장 위치](https://www.samsung.com/es/support/mobile-devices/how-to-record-your-screen-on-your-samsung-galaxy-device/)를 안내한다. 저장 위치는 설정에 따라 바뀔 수 있다. [OS 버전 확인 안내](https://www.samsung.com/ae/support/mobile-devices/how-to-check-the-operating-system-of-samsung-galaxy-devices/)를 따른다. 화면이 120Hz로 동작해도 녹화 영상이 120fps라는 뜻은 아니다.

Mac의 터미널에서 저장소 루트로 이동한다. 이 문서의 `~/MK/self-project/compare-stock`은 작성자 PC의 위치이므로, 저장소를 받은 위치에 맞게 바꾼다. `ffprobe`가 없다면 Homebrew의 [FFmpeg 공식 패키지](https://formulae.brew.sh/formula/ffmpeg.html)를 설치한다. 설치 전후에 명령 존재 여부를 확인한다.

```bash
cd ~/MK/self-project/compare-stock
python3 --version
command -v ffprobe
brew install ffmpeg
ffprobe -version
```

`command -v ffprobe`가 이미 경로를 출력하면 설치 명령은 건너뛴다. Android SDK [Platform Tools Mac용 공식 다운로드](https://developer.android.com/tools/releases/platform-tools)를 받아 압축을 푼다. 아래에서는 압축을 저장소 루트의 `platform-tools/`에 풀었다고 가정한다. 이 폴더는 Git에서 제외된다. `adb` 파일을 시스템 전역에 설치할 필요는 없다.

## 2. 네 개의 예비 영상 촬영

1. **같은 국내 주식 종목**을 두 앱에서 검색한다. 각 앱에서 종목을 한 번 열었다가 결과 화면으로 돌아오는 준비 동작을 동일하게 한다. 촬영 시작 카운트다운이 끝나고 결과 화면이 안정된 뒤 **3초** 기다린다.
2. **토스 → 한국투자 → 한국투자 → 토스** 순서로 시도한다. 각 시도를 별도 원본 파일로 보관하고 `pilot-s24p-01a`(1·2회), `pilot-s24p-01b`(3·4회)를 페어 ID로 쓴다. 예비 4회는 본 실험 통계에 합산하지 않는다.
3. 영상에서 **터치 점이 처음 보이는 프레임**과 **선택한 종목명·그 종목의 가격 숫자가 동시에 읽히는 최초 프레임**을 찾는다. `0`·빈칸·이전 종목 가격의 잔상은 완료로 세지 않는다. 터치 점은 물리적 손가락 접촉의 정확한 시각이 아니다. 따라서 지표 이름은 **“터치 지표 표시 → 상세 콘텐츠 표시”**다.
4. 화면이 검게 나오거나 터치 점/완료 시점이 불분명하면 `invalid_capture`를 별도 시도 대장에 기록한다. 앱 오류·재로그인과 촬영 실패를 구분한다. 계좌·잔고·인증·알림 정보가 녹화되지 않도록 한다.

**촬영 직전 확인:** 두 앱 모두 같은 종목의 **검색 결과 목록**이 화면에 보이고, 로그인·지문 인증·팝업을 이미 끝낸 상태여야 한다. 검색어와 결과 행 위치, **키보드 표시 여부**도 고정한다. 보유 종목, 최근 본 종목, 실시간 랭킹에서 들어간 영상은 이 시나리오의 비교 표본에서 제외한다. 녹화를 켜기 전에 계좌 화면을 지나가면 그 장면도 원본에 남을 수 있으므로, 민감한 화면을 지나지 않는 촬영 동선을 먼저 연습한다.

삼성은 일부 앱에서 화면 녹화가 제한될 수 있다고 [안내](https://www.samsung.com/us/support/answer/ANS10001616/)한다. 우회하지 말고 짧은 시험 영상으로 해당 화면의 녹화 가능 여부를 먼저 확인한다. 자세한 4회 표는 [S24+ 예비 측정 가이드](GALAXY_S24_PLUS_PILOT.md)에 있다.

## 3. 원본을 Mac으로 복사하고 해시 보관

USB로 영상만 로컬 복사하려면 S24+에서 **설정 → 휴대전화 정보 → 소프트웨어 정보 → 빌드 번호 7회 → 개발자 옵션 → USB 디버깅**을 켠다. 이 옵션은 **ADB 전송용**이다. Mac 연결 후 휴대폰 화면의 RSA 디버깅 허용 메시지를 확인한다. Mac은 별도 USB 드라이버가 필요하지 않다. [삼성 개발자 옵션](https://www.samsung.com/us/support/answer/ANS10003430/), [Android 기기 연결](https://developer.android.com/studio/run/device), [ADB pull 문서](https://developer.android.com/tools/adb)

저장소 루트에서 다음 순서로 실행한다. 파일명과 실제 저장 경로는 먼저 목록을 보고 바꾼다. 공백이 있는 경로의 따옴표를 유지한다.

```bash
cd ~/MK/self-project/compare-stock
./platform-tools/adb devices -l
./platform-tools/adb shell 'ls -l "/sdcard/DCIM/Screen recordings"'
mkdir -p captures/pilot
./platform-tools/adb pull "/sdcard/DCIM/Screen recordings/<실제파일명>.mp4" captures/pilot/
```

원본을 편집·재인코딩하지 않는다. 아래는 첫 번째 파일 이름 변경 예시다. **이 명령을 실행하지 않았다면 이후의 `01-toss.mp4`는 존재하지 않으므로 실제 복사된 파일명을 사용한다.** 앱을 영상으로 확인하기 전에는 파일명만 보고 `kis` 또는 `toss`로 바꾸지 않는다. 네 파일의 SHA-256을 기록한다. `captures/`는 `.gitignore`에 들어 있어 GitHub에 올라가지 않는다.

```bash
cd ~/MK/self-project/compare-stock
mv "captures/pilot/<실제파일명>.mp4" "captures/pilot/01-toss.mp4"
shasum -a 256 captures/pilot/*.mp4
```

## 4. 프레임을 고르고 PTS로 시간 계산

먼저 로컬 [대시보드](http://127.0.0.1:8765/)에 영상을 열어 **시작·완료가 대략 어디인지** 찾는다. 브라우저의 재생 위치는 정확한 프레임 번호가 아니므로 정밀 결과로 기록하지 않는다. 짧은 원본 영상의 프레임을 번호가 붙은 이미지로 추출해 터치 점의 첫 프레임과 종목명·가격의 첫 프레임을 눈으로 확인한다. 파생 이미지는 `captures/` 아래에 두고, 원본은 유지한다.

```bash
cd ~/MK/self-project/compare-stock
mkdir -p captures/pilot/01-frames
ffmpeg -hide_banner -loglevel error -i "captures/pilot/01-toss.mp4" -map 0:v:0 -fps_mode passthrough -start_number 1 -f image2 "captures/pilot/01-frames/%06d.png"
open captures/pilot/01-frames
```

`Error opening input: No such file or directory`가 나오면 `ls -lh captures/pilot`로 실제 이름을 확인한다. 예를 들어 `Screen_Recording_20261015_160106_Toss.mp4`가 그대로 있다면 위 명령의 `captures/pilot/01-toss.mp4`를 `captures/pilot/Screen_Recording_20261015_160106_Toss.mp4`로 바꾼다. 프레임 폴더 이름은 그대로 사용해도 된다.

파일 번호는 **1부터** 시작한다. 시작·종료 경계의 바로 이전 이미지도 열어 첫 출현임을 확인한다. 한 시도의 영상 번호를 고른 후, 아래처럼 파일의 각 프레임 PTS를 읽어 차이를 계산한다. `--start-frame`과 `--end-frame`에 선택한 이미지 번호를 넣는다.

```bash
python3 tools/measure_video_pts.py captures/pilot/01-toss.mp4 --start-frame 120 --end-frame 158 --frames-dir captures/pilot/01-frames
```

이 도구는 이미지 개수와 PTS 프레임 수가 맞는지 검사하고, 원본 SHA-256, 시작·끝 `PTS`(초·ms), 차이 `elapsed_ms`, 관측 프레임 간격 범위를 출력한다. **120·158은 명령 형식 예시이며 실제 측정 프레임이 아니다.** `ffprobe`의 [프레임별 표시 시각](https://ffmpeg.org/ffprobe.html)을 사용하므로 60fps/120fps를 가정하지 않는다. 이미지 추출에는 [FFmpeg의 `passthrough` 모드](https://ffmpeg.org/ffmpeg.html)를 사용하며, `-r`, FPS 필터, `-ss`를 추가하지 않는다. 그래도 디코딩 오류가 있으면 번호 대응을 재검토한다. 고정 FPS가 검증된 파일만 프레임 수 ÷ FPS를 쓴다. 화면 녹화 오버레이의 지연과 기기 부하는 PTS 계산으로 제거되지 않는다.

## 5. 로컬 서비스에 예비값 기록

```bash
cd ~/MK/self-project/compare-stock
python3 -m stocklab.server --port 8767 --db data/pilot-20261015-1619.sqlite3
```

재촬영 예비 4건은 [예비 대시보드](http://127.0.0.1:8767/)와 `data/pilot-20261015-1619.sqlite3`에 기록했다. 이미 `8767`에서 실행 중이면 명령을 다시 실행하지 않고 링크만 연다. 기본 `8765` 서버는 다른 DB를 사용한다. 새 시도에 `capture_method=screen_recording`, `timing_basis=frame_timestamps`, `start_state=warm`, `scenario=search_result_to_detail_quote`를 사용한다. `device`에는 **Galaxy S24+ / Android 버전 / One UI 버전**을, `app_version`에는 해당 앱 버전을 적는다. PTS 계산기의 **초 단위** 칸에는 도구의 `start_pts_seconds`/`end_pts_seconds`를 넣는다. 계산값을 폼에 적용하면 서버가 원시 PTS를 보관하고 경과 시간을 다시 계산한다.

`OSError: [Errno 48] Address already in use`는 해당 포트의 서버가 이미 실행 중이라는 뜻이다. 브라우저에서 위 주소를 먼저 연다. 이 오류 자체가 DB나 영상 문제를 뜻하지는 않는다.

| 필드 | 예비 실험 값 |
|---|---|
| 완료 기준 | `선택 종목명과 0·빈칸·이전 종목 잔상이 아닌 가격 숫자가 함께 읽힘` |
| 종목·키보드 | `stock_code`에 선택 종목의 6자리 코드, `keyboard_state`에 탭 직전 표시 여부 입력. 다른 값은 별도 그룹 |
| 결과 상태 | 완료, 10초 제한 시간 초과, 앱 오류 중 실제 상태 |
| 네트워크·시장 상태 | 실제 Wi-Fi/셀룰러와 장중/장외 기록 |
| 실행 순서·run ID | [4회 표](GALAXY_S24_PLUS_PILOT.md)의 1~4 / 두 앱에 같은 ID |
| 메모 | 종목, 영상 파일 식별자, 촬영 설정, 특이 사항. 민감정보는 적지 않음 |

한 페어의 첫 앱에서는 **저장 후 반대 앱 준비**, 두 번째 앱에서는 **기록 저장**을 누른다. 전자는 공통 조건과 ID를 복사하지만 새 영상의 프레임·PTS는 비운다. 결과 화면에서 페어 수와 제외 사유, 앱 버전·실행 순서 경고를 확인한다. 예비 결과는 판독 가능성 확인에만 사용한다.

## 6. 본 실험: 15블록 × 4회 = 30페어

재촬영 예비 4회에서 양쪽 앱의 같은 종료 기준은 판독했다. 다만 두 종목과 키보드 상태가 달라 **각 조건에 1페어**만 있으며, 촬영 순서도 둘 다 한국투자→토스였다. 이 네 건은 본 실험에 합산하지 않는다. [본 실험 계획](EXPERIMENT_PLAN.md)을 실행하기 전에 **한 종목·한 검색어·한 결과 행·한 키보드 상태**를 고정하고 날짜·망·시나리오를 담은 고유 세션 ID로 **60행 시도 대장**을 만든다.

```bash
cd ~/MK/self-project/compare-stock
python3 tools/make_trial_ledger.py --session-id 20261015-wifi-search > trial-ledger.csv
```

날짜 문자열은 **실제 본 실험 날짜**로 바꾼다. 홀수 블록은 토스-한투-한투-토스, 짝수 블록은 한투-토스-토스-한투 순서다. 계획된 60행을 지우지 않는다. 실제 시작 시각, 영상명, SHA-256, 완료/시간 초과/오류/촬영 실패/미시작과 이유를 채운다. 앱이 10초 안에 완료되지 않으면 `timeout=10000ms`로 기록하고 완료 시간 통계에는 포함하지 않는다. 촬영 실패는 앱 오류로 바꾸지 않는다.

**본 실험은 예비 측정과 별도 SQLite 파일**에 넣는다. 아래 예시는 기본 예비 서버(`8765`)를 유지하고 본 실험 서버를 `8766`에서 여는 방식이다. `SESSION`을 위에서 정한 실제 세션 ID로 바꾸고, 본 실험에는 [새 대시보드](http://127.0.0.1:8766/)만 사용한다.

```bash
cd ~/MK/self-project/compare-stock
python3 -m stocklab.server --port 8766 --db data/main-SESSION.sqlite3
```

시나리오·종목·기기·앱 버전·망·장중·웜 상태·완료 기준을 고정한다. 앱 업데이트, Wi-Fi 전환, 발열·절전 상태 변화, 인증 재요구, 녹화 차단이 생기면 그 시도와 블록의 유효성을 대장에 표시한다. 결과가 기대와 다르더라도 예정된 15블록을 임의로 늘리거나 느린 시도만 다시 찍어 교체하지 않는다.

## 7. 결과 검증과 원인 해석

1. **분모를 먼저 보고한다:** 계획 60회, 실제 시작, 완료, 제한 시간 초과, 앱 오류, 촬영 실패, 유효 페어. `invalid_capture`는 앱 DB 밖 대장에 있어 수동으로 합산해야 한다.
2. 동일 기기·네트워크·촬영 방식·PTS 판독·시장 상태·완료 기준의 **완료된 페어**만 지연 통계에 넣는다. `Toss − KIS` 중앙 차이가 음수면 해당 표본에서 토스의 관찰 시간이 낮다. 20쌍 미만이면 첫 세션을 예비 결과로 표시하고 p95는 숨긴다.
3. 앱별 중앙값, 페어 차이, p95(20쌍 이상), 탐색적 부트스트랩 구간(5쌍 이상), 실패율, 실행 순서·시간대 경향을 본다. 앱 버전이 섞였거나 실행 순서가 누락되면 결과와 함께 경고를 공개한다. 숫자는 **이 기기·이 세션의 화면 관찰**로만 해석한다.
4. 화면 영상만으로 두 상용 앱의 현행 HTTP 버전, WebSocket 사용, 캐시 적중, 서버 처리시간 또는 특정 원인의 우위를 **확정하지 않는다.** 별도 시스템 추적은 UI 프레임 지연 단서로만, 비복호화 네트워크 메타데이터는 관찰 사실로만 기록한다. 트래픽 계측을 켠 반복은 기본 영상 시도와 합산하지 않는다.

본 실험 원자료는 `http://127.0.0.1:8766/api/export.csv?source=real`로 받을 수 있다. 외부 공개 전 `notes`, 종목, 영상 파일 경로, 개인·계좌 정보가 들어 있지 않은지 확인한다. [공개 자료와 주장 범위](PUBLIC_EVIDENCE.md)를 따른다.

## 8. 서버 개발자 포트폴리오로 확장

이 영상 실험은 **문제를 정의한 근거**다. 사용자가 지정한 [Product 서버 개발 공고](https://toss.im/career/job-detail?job_id=4071141003&company=%ED%86%A0%EC%8A%A4%EC%A6%9D%EA%B6%8C&sub_position_id=8000585003)는 사용자 접점의 실시간 데이터, 안정성, Java/Kotlin·Spring, 동시성·성능 진단과 개인의 정량 기여를 중시한다. 서버 능력은 별도의 **자체 모의 시세 서버**로 입증한다.

| 단계 | 구현·검증할 것 | 발표할 증거 |
|---|---|---|
| 기준선 | 동일한 시드의 모의 시세, 동일 클라이언트·요청 부하, Kotlin/Spring API | 부하 조건·p50/p95/p99·오류율·CPU·메모리 |
| 전송 실험 | 폴링과 서버 푸시(SSE 또는 WebSocket) 중 **한 변수** 변경 | 화면 반영 지연과 시세 신선도, 누락·재연결율 |
| 캐시·팬아웃 | 같은 조건에서 캐시 또는 구독자 수를 한 번에 하나씩 변경 | 처리량·지연·데이터 정합성·운영 자원 |
| 장애 처리 | 느린 소비자, 연결 해제, 중복·순서 변경을 재현 | 배압·재시도 결정, 모니터링 경보, 회복시간 |

이 자체 실험의 인과 결론은 **직접 만든 서버**에만 적용한다. 토스와 한국투자 앱이 같은 설계를 쓴다고 추정하지 않는다. [Platform 3년 이하 공고](https://toss.im/career/job-detail?job_id=4071141003&company=%ED%86%A0%EC%8A%A4%EC%A6%9D%EA%B6%8C&sub_position_id=8000588003)도 있으나, 현 주제는 종목 검색·가격 표시의 사용자 경로와 직접 연결되는 **Product**가 더 적합하다.

## 9. 공개 포트폴리오와 AWS

GitHub에는 코드, 테스트, SOP, 익명화한 **집계 결과와 재현 절차**를 올린다. 영상 원본·프레임 이미지·SQLite DB·시도 대장 원본은 기본적으로 공개하지 않는다. 이 프로젝트의 `.gitignore`는 `captures/`, 동영상 확장자, `data/`, `trial-ledger*.csv`를 제외한다. `git status`로 다시 확인하고, 공개 저장소를 만들기 전 민감한 파일이 없는지 직접 검토한다. 현재 폴더에는 아직 `.git`이 없다.

보고서는 **질문 → 촬영·시간 정의 → 계획과 제외 → 페어 결과 → 오차·한계 → 자체 서버의 별도 통제 실험 → 본인 결정·정량 기여** 순서로 작성한다. 현재 예비 결과는 `조건별 1페어`라고 표시하고 본 실험 결과는 `수집 전`으로 둔다. 가상 예시를 실측 그래프에 넣지 않는다. 면접에는 원본 공개 없이 PTS 판독 근거와 해시, 분석 코드·테스트, 익명화 결과표를 보여준다. 현재의 `web/` 폴더는 로컬 API에 의존하므로 **그대로 정적 호스팅에 올리지 않는다.** 본 실험 DB에서 허용된 집계값만 뽑는 `tools/build_public_report.py`로 별도 정적 HTML을 만든다.

```bash
cd ~/MK/self-project/compare-stock
python3 tools/build_public_report.py --db data/main-SESSION.sqlite3 --ledger trial-ledger.csv --out public/index.html
open public/index.html
```

생성된 페이지와 시도 대장의 건수를 대조하고, 파일을 직접 열어 개인정보·원본 경로가 없음을 확인한 뒤 공개한다. 본 실험 0건인 현재는 완료 결과 보고서를 생성할 단계가 아니며, 예비 4건을 본 실험 결과로 바꾸지 않는다.

공개할 준비가 되면 이 폴더를 Git 저장소로 초기화하고 **허용한 소스·문서·정적 보고서만** 추가한다. `git status --short`와 `git diff --cached`에서 민감정보가 없는지 검토한다. 두 GitHub 계정 중 포트폴리오에 사용할 한 곳에 빈 저장소를 만든 뒤, 그 저장소가 알려주는 URL을 `origin`에 연결한다. 아래 URL은 형식 예시이므로 실제 계정·저장소 주소로 바꾼다.

```bash
cd ~/MK/self-project/compare-stock
git init
git branch -M main
git add .gitignore README.md amplify.yml docs stocklab tests tools web public/index.html
git status --short
git diff --cached --stat
git diff --cached
git commit -m "Add reproducible stock app latency study"
git remote add origin "https://github.com/YOUR_ACCOUNT/compare-stock.git"
git push -u origin main
```

실제 `public/index.html`이 아직 생성되지 않았다면 그 파일을 `git add` 목록에서 뺀다. 원본 영상·DB·시도 대장은 Git에 추가하지 않는다. 코드 공개와 보고서 게시 전에는 이력에 실수로 올라간 파일도 없는지 확인한다.

AWS에서는 현재의 **쓰기 가능한 로컬 Python/SQLite 서버를 그대로 공개하지 않는다.** 인증이 없고 원본 자료가 들어갈 수 있다. 실측 뒤 비식별 **정적 보고서**만 [Amplify Hosting](https://aws.amazon.com/amplify/pricing/)으로 GitHub에서 배포하면 기본 URL·SSL을 쓸 수 있고 **서버 이미지(Docker/AMI)가 필요 없다.** 저장소 루트의 `amplify.yml`은 [AWS 빌드 명세](https://docs.aws.amazon.com/amplify/latest/userguide/yml-specification-syntax.html)에 따라 `public/index.html` 존재를 검사하고 [`public/` 폴더만 배포](https://docs.aws.amazon.com/amplify/latest/userguide/edit-build-settings.html)하도록 설정했다. GitHub 저장소를 Amplify에 연결한 뒤 브랜치 `main`의 빌드 설정에 이 파일이 적용되는지 확인한다. 공식 요금표 기준, 무료 혜택이 없는 경우 월 10회 × 3분 빌드, 저장 50MB, 전송 1GB의 예시는 약 **$0.45/월**이다. 무료 이용 구간·크레딧·도메인·실제 트래픽에 따라 달라진다. 배포 전 [AWS Budgets](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/) 알림을 설정한다. 알림은 지출을 자동 차단하지 않는다.

읽기 전용 API 상시 데모가 정말 필요해질 때만 [Lightsail 1GB Linux/Unix dual-stack 인스턴스](https://aws.amazon.com/lightsail/pricing/) **$7/월** 수준을 검토한다. 이 경우에는 Linux/Unix **OS Only 이미지**를 쓰고 Python 서비스를 별도로 배포해야 한다. 인증/공개 범위, 영속 저장·백업, 로그, HTTPS도 설계해야 한다. [중지한 인스턴스도 삭제 전에는 과금](https://docs.aws.amazon.com/en_en/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html)된다. 첫 포트폴리오는 정적 보고서와 GitHub 코드만으로 충분하다.

## 완료 판정

- [ ] S24+ OS·One UI와 두 앱 버전, 종목·망·시장 상태를 기록했다.
- [ ] 터치 점·완료 화면이 보이는 4개 예비 원본과 SHA-256을 보관했다.
- [ ] 4개 원본의 시작·끝 프레임 및 PTS를 다른 사람이 재판독할 수 있다.
- [ ] 60행 대장을 실험 전에 만들고, 모든 시도와 제외 이유를 남겼다.
- [ ] 유효 페어 수·실패율과 통계 사용 범위를 함께 보고했다.
- [ ] 상용 앱 관찰과 자체 서버 통제 실험의 결론을 분리했다.
- [ ] 공개 자료에서 민감정보·원본 영상·개인 경로를 제거했다.
- [ ] GitHub 링크·실행 방법·테스트 결과·본인 기여를 보고서에 적었다.
