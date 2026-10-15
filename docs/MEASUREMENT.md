# 휴대폰 실측 절차

## 1. 비교 질문을 먼저 고정

첫 실험은 **“종목 검색 결과에서 동일 종목을 탭한 뒤 선택 종목의 가격과 이름이 나타날 때까지”**로 제한합니다. `0`·빈칸·이전 종목 가격의 잔상은 완료가 아닙니다. 각 앱에서 화면 경로와 끝나는 기준을 글과 캡처로 남깁니다. UI 구조가 다르면 완전히 같은 기능만 비교하고, 경로 차이는 별도 사실로 기록합니다. 종목을 미리 열어 둔 상태와 새로 여는 상태를 섞지 않습니다. 첫 화면 반응, 가격·이름이 표시된 시점, 실제 조작이 가능한 시점을 각각 별도 지표로 두세요. 보이는 가격이 최신인지는 표시 시각이나 별도 근거가 있을 때만 판단합니다.

준비 표에 아래를 기재합니다.

| 항목 | 예시 |
|---|---|
| 기기·OS·화면 주사율 | 사용한 한 대의 Android 기기와 실제 버전 |
| 두 앱 버전 | 설정의 앱 정보 화면에서 확인 |
| 로그인·계정 상태 | 두 앱 모두 로그인 완료, 추가 인증 없음 |
| 종목·화면 경로 | 같은 종목, 시작/종료 시점 정의 |
| 통신망·위치 | 같은 Wi-Fi 또는 셀룰러, VPN 유무 |
| 시장 상태 | 장중/장외, 측정 시간대 |
| 실행 상태 | 콜드 시작 또는 앱을 연 상태에서 탐색 |
| 영상 계측 | 외부 카메라/화면 녹화, 실제 FPS, 프레임 판독 도구 |
| 완료 기준 | 두 앱에서 동일하게 적용할 콘텐츠 조건을 한 문장으로 기재 |
| 실행 순서 | 앱별 1~4번째 실행 위치와 페어 run ID |

장중 시세는 변하고, 앱별 캐시 정책도 다를 수 있습니다. 따라서 빠르게 보인 값이 최신 데이터라는 가정을 하지 않습니다. 두 앱의 콘텐츠 정의가 다르면 “정확히 같은 업무”였는지 먼저 검토합니다.

## 2. 영상으로 체감 시간을 측정

1. **가능하면 외부 120/240fps 카메라**로 손가락과 화면을 한 영상에 담습니다. 손가락이 화면에 닿은 첫 프레임부터 미리 정의한 가격·종목명이 나타난 첫 프레임까지 셉니다. 외부 촬영은 앱이 실행되는 휴대폰에 녹화 부하를 더하지 않습니다.
2. **휴대폰 한 대만 있으면 화면 녹화로 시작합니다.** Galaxy S24+에서는 삼성 내장 녹화 설정의 **누른 위치 표시**를 먼저 켭니다. 개발자 옵션은 필수가 아닙니다. 이 옵션이 없는 기기에서는 Android 개발자 옵션의 **탭 표시(Show taps)**를 확인합니다. 탭 표시가 처음 보인 프레임부터 콘텐츠가 표시된 프레임까지 셉니다. 탭 표시가 실제 손가락 접촉보다 늦을 수 있으므로 이 값은 **탭 표시→콘텐츠 표시 시간**으로 이름 붙입니다. 구체적인 S24+ 순서는 [예비 측정 가이드](GALAXY_S24_PLUS_PILOT.md), 일반 옵션은 [Android 개발자 옵션](https://developer.android.com/studio/debug/dev-options)을 참고하세요.
3. 프레임별로 넘길 수 있는 영상 도구에서 시작·끝 프레임을 확인합니다. **고정 FPS임을 확인한** 영상이라면 이 서비스의 프레임 번호 계산기에 두 번호와 FPS를 입력하세요. 계산식은 `(끝 프레임 번호 − 시작 프레임 번호) ÷ FPS × 1000`입니다. 가변 FPS 영상은 두 프레임의 **표시 시각(PTS)** 차이를 사용합니다. 30fps의 한 프레임은 약 33.3ms, 60fps는 약 16.7ms, 120fps는 약 8.3ms입니다. 각 경계의 프레임 판독 오차와 화면 녹화로 인한 기기 부하를 측정 메모에 남깁니다. 브라우저 영상 **재생 위치**는 프레임 단위 판독을 보장하지 않습니다. 이 값은 근사치로 분리해 저장합니다.
4. 녹화 파일을 PC로 옮겼다면 영상 편집기에서 각 프레임의 표시 시각을 읽을 수 있습니다. `ffprobe`가 설치된 경우 아래 명령으로 비디오 프레임의 PTS(초)를 순서대로 내보낼 수도 있습니다. 눈으로 고른 시작·끝 프레임의 시각을 찾아 서비스의 **PTS 계산기**에 입력합니다. 영상에 따라 첫 프레임 PTS는 0이 아닐 수 있으므로 절대 시각 대신 **차이**를 사용합니다. 프레임 번호와 실제 PTS의 대응을 확인하지 못했다면 정밀값으로 입력하지 마세요.

   ```bash
   ffprobe -v error -select_streams v:0 -show_entries frame=best_effort_timestamp_time -of csv=p=0 capture.mp4 > frame_pts.csv
   ```

   계산에 쓴 시작·끝 프레임 또는 PTS가 기록과 CSV에 함께 남습니다. 원본 영상은 서버로 올리지 않고 개인 기기에 보관하며, 파일을 찾을 수 있는 식별자를 측정 메모에 남깁니다. 실제 접촉이나 콘텐츠 표시가 두 프레임 사이에서 일어나므로 프레임 간격에 따른 경계 불확실성은 여전히 남습니다.
5. 토스/한투 실행 순서를 `A-B-B-A`처럼 교차합니다. 각 조건당 20~30쌍 이상을 목표로 하고, 시작 상태·Wi-Fi/셀룰러·장중/장외·촬영 방식·**시간 판독 방식**을 나눕니다. 1쌍의 두 측정값에는 같은 `run_id`를 입력합니다. 화면 녹화와 외부 촬영, 프레임 판독과 재생 위치 근사치를 같은 통계 그룹에 섞지 않습니다. 기기와 네트워크도 그룹별로 분리합니다.
6. 완료 시간 외에 **탭→첫 반응**, 화면이 끊기는 순간, 빈 화면·오류·재로그인 여부를 기록합니다. 제한 시간 초과나 오류는 `status`를 바꾸고 `elapsed_ms`에 관찰 종료까지의 시간을 넣습니다. 이 값은 속도 통계에서 제외하고 건수로 셉니다. 오류를 임의로 버리면 빠른 경우만 남는 편향이 생깁니다. 가격이 즉시 보이더라도 캐시된 값일 수 있으므로 이를 최신 시세 도착 시각으로 해석하지 않습니다.

금융 앱의 일부 화면은 보안 설정 때문에 녹화가 검게 나올 수 있습니다. 이 경우 우회하지 않고, 민감정보가 찍히지 않는 화면에 한정하거나 해당 시나리오를 제외합니다. [Android 보안 화면](https://developer.android.com/security/fraud-prevention/activities)

## 3. 프레임 지연을 별도로 확인

Android의 **시스템 추적(System Tracing)** 메뉴에서 빠른 설정 타일을 켜고 짧은 시나리오를 추적할 수 있습니다. Android 10 이상에서는 Perfetto trace 형식이 사용됩니다. 작업 직후 종료하고 추적 파일을 [Perfetto UI](https://ui.perfetto.dev/)에서 엽니다. 화면 녹화와 시스템 추적을 동시에 켜면 부하가 달라질 수 있으므로 기본 영상 측정과 **별도 반복**으로 비교합니다. 시스템 추적은 UI 스레드와 렌더링 지연의 단서이며, 가격이 실제로 보인 순간이나 서버 처리시간을 자동으로 알려주지는 않습니다. [Android 기기 시스템 추적](https://developer.android.com/topic/performance/tracing/on-device)

### PC를 사용할 수 있을 때의 보조 계측

Android SDK Platform Tools의 `adb`를 설치하고 USB 디버깅을 승인한 뒤 아래처럼 수집할 수 있습니다. 패키지명은 직접 확인하고 `<PACKAGE>`를 실제 값으로 바꾸세요.

```bash
adb devices
adb shell pm list packages
adb shell dumpsys gfxinfo <PACKAGE> reset
# 이제 휴대폰에서 정의한 시나리오를 수행합니다.
adb shell dumpsys gfxinfo <PACKAGE> framestats > frames.txt
```

`gfxinfo`는 앱 UI 프레임의 보조 자료입니다. **콘텐츠가 사용 가능한 시각이나 서버 응답시간을 대신하지 않습니다.** 별도의 콜드 시작 실험에서는 시작 Activity를 확인한 뒤 아래 방법을 쓸 수 있습니다.

```bash
adb shell am start -S -W <PACKAGE>/<LAUNCHER_ACTIVITY> \
  -a android.intent.action.MAIN -c android.intent.category.LAUNCHER
```

이 결과의 TTID(time to initial display)는 **초기 화면** 기준입니다. 비동기로 로딩된 시세까지 표시됐다는 뜻이 아닙니다. TTFD(time to full display)는 앱 자체가 `reportFullyDrawn()`을 호출해야 시스템이 알 수 있습니다. [Android 시작 시간](https://developer.android.com/topic/performance/issues/launch-time), [dumpsys gfxinfo](https://developer.android.com/tools/dumpsys)

시스템 추적에서 앱의 UI 스레드, RenderThread, SurfaceFlinger 흐름을 봅니다. Android 12 이상 **FrameTimeline 트랙이 실제 기록된 경우** 프레임 지연이 앱 측인지 시스템 측인지 살필 수 있습니다. SurfaceView에는 분석 제한이 있으며, 타사 앱의 상세 프로파일링은 앱 설정에 따라 제한될 수 있습니다. [Perfetto FrameTimeline](https://perfetto.dev/docs/data-sources/frametimeline), [Android profileable 설정](https://developer.android.com/guide/topics/manifest/profileable-element)

Android Studio Network Inspector는 지원되는 네트워크 라이브러리가 제한되므로, 본인이 개발하지 않은 상용 앱의 전체 API 호출을 볼 수 있는 도구로 가정하지 않습니다. [Android Network Inspector](https://developer.android.com/studio/debug/network-profiler)

## 4. 프로토콜을 어디까지 알 수 있나

개발자 옵션과 Perfetto만으로 HTTP/2·HTTP/3·WebSocket·gRPC 사용 여부, 요청별 서버 시간, 캐시 적중을 확정할 수 없습니다. 본인 기기에서 **복호화하지 않은** 트래픽을 관찰하면 TCP/TLS와 UDP/QUIC, 연결 수, 패킷량 등의 메타데이터는 볼 수 있습니다. 하지만 QUIC만 보고 HTTP/3라고 단정할 수 없습니다. ClientHello의 ALPN 목록은 제안이며 TLS 1.3에서 실제 선택과 애플리케이션 데이터는 암호화될 수 있습니다. [TLS 1.3](https://www.rfc-editor.org/rfc/rfc8446.html), [QUIC](https://www.rfc-editor.org/rfc/rfc9000.html), [HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html)

따라서 결과 표의 `protocol_observation`에는 “UDP/QUIC 트래픽 관찰”처럼 **관측된 것만** 적고, “HTTP/3라서 빠름” 같은 결론은 쓰지 않습니다. 금융 앱 트래픽을 복호화하거나 인증서를 우회할 필요는 없습니다.

이 관찰은 PCAPdroid 캡처와 `tools/analyze_pcap.py`로 진행합니다. 탭 1회당 신규 연결·재사용·DNS 질의, TLS 버전과 ALPN 제안, QUIC 사용, 서버 주도 반복 전송 연결을 두 앱별로 표로 비교합니다. 절차와 해석 기준은 [비복호화 네트워크 메타데이터 비교](NETWORK_METADATA.md)에 있습니다.

## 5. 결과 해석

| 관측 패턴 | 검토할 가설 (결론 아님) |
|---|---|
| 첫 반응은 빠르지만 실제 값 표시가 늦다 | 데이터 대기, 캐시 정책, UI 단계적 표시 |
| 값 표시는 비슷하지만 스크롤 끊김이 크다 | 클라이언트 렌더링 또는 기기 부하 |
| 콜드 시작에서만 차이가 크다 | 시작 작업, 인증, 연결 설정, 캐시 |
| Wi-Fi와 셀룰러에서 차이가 바뀐다 | 네트워크 지연 민감도, 경로 차이 |

앱 화면 실험은 **관찰 연구**입니다. 내부 서버 원인을 검증하려면 동일 클라이언트·동일 데이터·동일 서버의 자체 모의 시세 시스템을 만들고, 캐시·전송·페이로드를 한 번에 하나씩 바꿔 측정해야 합니다.

## iPhone인 경우

iPhone의 Developer Mode는 Xcode로 자신이 만든 앱을 실행·디버깅하기 위한 기능이므로 위 Android 시스템 추적과 동일하지 않습니다. iPhone만 있으면 화면 녹화 또는 외부 고속 촬영으로 **보이는 화면의 시간**을 비교합니다. Mac과 연결하면 Apple의 RVI 패킷 추적을 보조 자료로 사용할 수 있지만 암호화된 앱 내용을 보여주지는 않습니다. [Apple Developer Mode](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device), [Apple 패킷 추적](https://developer.apple.com/documentation/network/recording-a-packet-trace)
