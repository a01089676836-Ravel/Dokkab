# Jetson 실행 코드 설명

카메라 입력부터 실시간 추론, 장치 제어, DB 기록, 대시보드 표시까지 **`convey_vision.py` 하나로 실행**한다.

## 1. 파일 구성

| 파일 | 역할 |
|---|---|
| `convey_vision.py` | 메인 프로그램. 카메라 → 얼굴 검출 → 거리 추정 → 상태 판단 → LED·벨트 제어 → 기록 → 대시보드 |
| `conveyor_dashboard/app.py` | Flask 웹 서버. HTML, 카메라 영상, 로그 조회, DB 켜기/끄기, 종료 API |
| `conveyor_dashboard/launcher.py` | `convey_vision.py`를 실행·감시. 꺼지면 같은 주소에 [다시 시작] 화면 |
| `conveyor_dashboard/aiven_logs.py` | 이벤트를 로컬 대기함(SQLite)에 넣고 Aiven MySQL로 전송 |
| `conveyor_dashboard/vision_runtime.py` | `FrameHub`: 최신 카메라 화면을 웹으로 전달 |
| `conveyor_dashboard/index.html`, `dashboard.js` | 대시보드 화면 |
| `conveyor_dashboard/schema.sql` | DB 테이블 정의 |
| `face_detection_yunet_2023mar.onnx` | YuNet 모델 (없으면 자동 다운로드) |

## 2. 실행 환경

| 항목 | 값 |
|---|---|
| 보드 | NVIDIA Jetson Orin Nano |
| Python | 3.10 (`conveyor_dashboard/.venv`, 시스템 패키지 포함) |
| 주요 패키지 | OpenCV 4.14.0, Jetson.GPIO, Flask, PyMySQL, python-dotenv |
| 카메라 | USB 웹캠, `/dev/video0`, 640×480 |

## 3. 배선 (BOARD 핀 번호)

| 핀 | 연결 |
|---|---|
| 1 | 모터 드라이버(L9110S) VCC |
| 9 | 모터 드라이버 GND |
| 15 | 빨강 LED (위험) |
| 31 | 노랑 LED (경고, 2026-10-06 파랑에서 교체) |
| 33 | 모터 드라이버 B-1A |
| — | B-1B는 연결하지 않음 (모듈 기본값 HIGH) |

L9110S는 두 입력이 다를 때만 모터를 돌린다. B-1B가 HIGH이므로 **33번 LOW = 벨트 작동, 33번 HIGH = 벨트 정지**다.

## 4. 처리 흐름

```
카메라 프레임 (640×480)
  └─ YuNet 얼굴 검출 (score 0.8, NMS 0.3)
       └─ 얼굴마다 추정거리 = 13000 ÷ 상자 높이
            └─ 가장 가까운 거리로 상태 결정
                 green  : 얼굴 없음 / 200cm 초과
                 yellow : 50 ~ 200cm
                 red    : 50cm 이하
                 └─ StatusFilter (위험 쪽 즉시, 안전 쪽 1초 유지 후)
                      ├─ LED·벨트 제어 (red만 정지)
                      ├─ 상태가 바뀌면: 터미널 출력 + DB 기록
                      └─ 매 프레임: JPEG로 대시보드에 전송 + Jetson 모니터 창 표시
```

### 주요 함수

| 함수 | 하는 일 |
|---|---|
| `get_distance(box_height)` | 상자 높이(px) → 추정거리(cm) |
| `get_status(distance)` | 거리 → green / yellow / red |
| `StatusFilter.update()` | 상태 깜빡임 거르기 |
| `apply_status(status)` | red면 `stop_motor()`, 아니면 `run_motor()`, 그리고 `set_leds()` |
| `describe()` | DB에 남길 설명과 정지 이유 생성 |
| `record_event()` | 이벤트를 DB 대기함에 넣음 (DB 연동 꺼짐이면 저장 안 함) |
| `start_dashboard()` | Flask 서버를 별도 스레드로 실행 |

## 5. 설치

Jetson에서 한 번만 한다.

```bash
cd ~/Dokkab/conveyor_dashboard
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env      # Aiven 접속 정보 입력
# Aiven 콘솔에서 받은 CA 인증서를 ca.pem으로 저장
.venv/bin/python aiven_logs.py --init-db   # 테이블 생성
```

> `.env`와 `ca.pem`에는 DB 접속 정보가 들어 있다. GitHub에 올리지 않는다(`.gitignore`로 제외됨).

## 6. 실행과 종료

```bash
cd ~/Dokkab
conveyor_dashboard/.venv/bin/python conveyor_dashboard/launcher.py   # 권장: 종료 후 웹에서 다시 시작 가능
# 또는 런처 없이: conveyor_dashboard/.venv/bin/python convey_vision.py
```

- `launcher.py`는 `convey_vision.py`를 실행하고 지켜본다. 프로그램이 꺼지면(대시보드 종료 버튼, 카메라 창 `q`, 오류) 같은 주소(5080)에 [다시 시작] 화면을 띄운다. 비밀번호(`DASHBOARD_PASSWORD`)를 넣으면 다시 켠다. 런처까지 끄려면 터미널에서 `Ctrl+C`.

> **반드시 Jetson 모니터에 로그인된 상태에서 실행한다.** 프로그램이 Jetson 화면(`DISPLAY=:0`)에 카메라 창을 띄우기 때문이다. 모니터 창을 열 수 없으면 프로그램이 비정상 종료(Qt 오류, 종료 코드 134)하고, 이때는 벨트 정지 코드(`finally`)가 실행되지 않는다.

- 대시보드: Jetson에서 `http://127.0.0.1:5080`. 같은 Wi-Fi의 PC·휴대폰에서는 `http://<Jetson IP>:5080`(예: `http://192.168.11.51:5080`).
- 인터넷 공개: 프로그램을 켠 뒤 다른 터미널에서 `conveyor_dashboard/start_tunnel.sh`를 실행한다. 출력된 `https://….trycloudflare.com` 주소로 어디서나 접속한다. `Ctrl+C`로 닫는다.
  - Cloudflare 무료 임시 터널(계정 불필요)이라 **주소가 켤 때마다 바뀐다.** 실행 파일은 `~/tools/cloudflared`에 두었다(설치·시스템 설정 변경 없음).
  - `.env`에 `DASHBOARD_PASSWORD`가 있으면 종료·DB 버튼을 누를 때만 비밀번호를 묻는다(영상·로그 보기는 비밀번호 없이 열린다). 비워 두면 주소만 알면 누구나 종료 버튼으로 벨트를 멈출 수 있으니 반드시 넣는다. 영상은 공개되므로 화면에 찍힌 얼굴이 외부에 보인다는 점은 남는다.
- 종료: 터미널 `Ctrl+C`, 카메라 창 `q`, 대시보드 "대시보드 종료" 버튼. 어느 방법이든 벨트를 멈추고 핀과 카메라를 해제한다. 런처로 실행했다면 종료 버튼이나 `q` 뒤에 페이지를 새로고침해 [다시 시작]으로 다시 켤 수 있다.
- 비밀번호에는 `q`·`s`를 넣지 않는다. Jetson 화면에서 입력할 때 키가 카메라 창으로 가면 `q`는 종료, `s`는 스냅샷이 된다.
- 스냅샷: 카메라 창에서 `s`

## 7. 자주 나는 오류

| 메시지 | 원인 | 해결 |
|---|---|---|
| `Device or resource busy` | 다른 프로그램이 GPIO 핀을 쓰는 중 | `pkill -INT -f rail_face; pkill -INT -f convey_vision` 후 다시 실행 |
| `프레임을 읽을 수 없습니다` / `select() timeout` | USB 웹캠이 영상 전송을 멈춤 | 웹캠 USB를 뽑았다가 다시 꽂기 |
| `No module named 'flask'` | 시스템 python3로 실행함 | `conveyor_dashboard/.venv/bin/python`으로 실행 |
| `Could not open /dev/mem for pinmux check` | 일반 사용자 권한이라 핀 점검만 건너뜀 | 무시해도 됨 (동작에 영향 없음) |
| `qt.qpa.xcb: could not connect to display` 후 비정상 종료 | Jetson 모니터에 로그인되어 있지 않아 카메라 창을 못 띄움 | Jetson 모니터에 로그인한 뒤 실행. 종료 후 벨트가 멈췄는지 눈으로 확인 |
