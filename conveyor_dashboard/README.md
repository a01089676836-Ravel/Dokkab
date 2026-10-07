# 컨베이어 비전 · Aiven MySQL · HTML

**구조:** 젯슨 카메라 → RYG/YuNet 분석 → FSM 상태 → Aiven MySQL 로그 → HTML.

- 왼쪽: 얼굴 상자·거리·랜드마크가 그려진 카메라 영상.
- 오른쪽: 발생 날짜, 발생 시간, 이벤트 설명, 정지 이유. 최신 발생 시각이 맨 위.
- HTML 경고는 **노랑**, 위험은 **빨강**. **경고와 위험 모두 벨트를 정지**합니다. 얼굴이 없으면 정상으로 보고 벨트를 구동합니다.
- 출력 장치는 빨강·경고·초록 LED와 **L9110S DC 벨트 모터**입니다. BOARD 핀은 빨강 15, 경고 31, 초록 32, 모터 입력 33·29입니다 (rail.py와 동일).
- DB에는 영상이나 얼굴 사진을 업로드하지 않습니다. 문자 이벤트만 기록합니다.

## 원격 접속과 로그 검색 (2026-10-06 추가)

현재는 저장소 루트의 `convey_vision.py` 하나로 비전·제어·기록·대시보드를 함께 실행합니다(`cd ~/Dokkab && conveyor_dashboard/.venv/bin/python convey_vision.py`). `launcher.py`로 실행하면(`cd ~/Dokkab && conveyor_dashboard/.venv/bin/python conveyor_dashboard/launcher.py`) "대시보드 종료" 뒤에도 같은 주소를 새로고침해 비밀번호를 넣고 [다시 시작]으로 다시 켤 수 있습니다.

### 다른 곳에서 접속

| 방법 | 주소 | 준비 |
|---|---|---|
| Jetson 안 | `http://127.0.0.1:5080` | 없음 |
| 같은 Wi-Fi의 PC·휴대폰 | `http://<Jetson IP>:5080` (예: `http://192.168.11.51:5080`) | 없음 (`convey_vision.py`의 `WEB_HOST = '0.0.0.0'`) |
| 인터넷 어디서나 | `https://….trycloudflare.com` | 다른 터미널에서 `./start_tunnel.sh` 실행 → 출력된 주소 사용, `Ctrl+C`로 닫기 |

- 인터넷 주소는 Cloudflare의 계정 없는 임시 터널(quick tunnel)이라 **켤 때마다 바뀝니다.** 고정 주소는 Cloudflare 계정과 도메인이 필요합니다.
- 터널 실행 파일 `cloudflared`는 Jetson의 `~/tools/cloudflared`에 파일 하나로 둡니다(설치·시스템 설정 변경 없음). Jetson(arm64) 전용이라 저장소에는 넣지 않습니다. 없으면 아래 명령으로 받습니다.

```bash
mkdir -p ~/tools && curl -fL -o ~/tools/cloudflared   https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 && chmod +x ~/tools/cloudflared
```

- **비밀번호:** 영상·로그 보기는 로그인 없이 열립니다. `.env`에 `DASHBOARD_PASSWORD=<비밀번호>`가 있으면 **종료·DB 연동 버튼을 누를 때만** 비밀번호 입력 창이 뜹니다(맞히면 새로고침 전까지 다시 묻지 않음). 비워 두면 주소를 아는 누구나 종료 버튼으로 벨트를 멈출 수 있으니 반드시 넣습니다. Jetson `.env`의 사본은 `jetson_env.txt`(보기용, Git 제외)에 있습니다.
- 영상은 공개되므로 화면에 찍힌 얼굴이 주소를 아는 사람에게 보입니다. 같은 Wi-Fi의 `http` 접속에서는 버튼 비밀번호가 암호화되지 않고 오갑니다. 인터넷 주소는 `https`입니다.
- 기존 방식인 SSH 터널(아래 "설치와 DB 설정")도 그대로 쓸 수 있습니다.

### 로그 기간 검색

로그 표 위 **기간** 줄에서 [오늘]·[1주]·[1개월]·[3개월]을 누르면 오늘까지의 기간이 달력 칸에 채워지고 바로 검색됩니다. 달력으로 시작·끝 날짜를 직접 고른 뒤 [검색]을 눌러도 됩니다. [최근]은 최근 100건으로 돌아갑니다. 기간 안 전체 건수를 보여 주고, 표에는 최신 500건까지 표시합니다.

API: `GET /api/v1/logs?limit=500&from=YYYY-MM-DD&to=YYYY-MM-DD` (한국 날짜, 양 끝 포함). 자세한 내용은 [DB 구조](../docs/05_DB_구조.md) 4.1절.

### LED

경고 LED는 파랑에서 **노랑**으로 교체했습니다(같은 31번 핀, 2026-10-06). 현재 배선은 `convey_vision.py` 맨 위 주석이 기준입니다. 이 문서 아래쪽의 핀 설명(초록 32, 모터 29 등)과 `hardware_integration.md`는 초기 배선 기록입니다.

## 원본에서 반영한 기준

입력: `C:\Users\wjswh\Downloads\RYG_face_distance_detection\RYG_face_distance_detection.py`

| 항목 | 반영값 |
|---|---|
| 카메라 | 0 |
| 모델 | face_detection_yunet_2023mar.onnx |
| 검출 점수 / NMS | 0.8 / 0.3 |
| 추정거리 | 13000 ÷ 얼굴 박스 높이(px), 단위 cm |
| 정상 | 200cm 초과 |
| 경고 | 50cm 초과 ~ 200cm 이하 |
| 위험 | 50cm 이하 |

여러 얼굴이면 가장 가까운 얼굴로 상태를 결정합니다. 원본의 비례상수는 근사값으로 유지했습니다. 카메라·해상도가 바뀌면 기존 거리 보정 과정으로 다시 확인하세요. 얼굴이 검출되지 않는 경우는 `ERROR / 거리 판단 불가`로 기록합니다. 원본 다운로드 파일은 그대로 보관됩니다.

## 설치와 DB 설정

현재 젯슨의 `/home/jetson/conveyor_dashboard_20260930`에 설치했으며 Aiven 연결과 테이블 생성까지 마쳤습니다. 현재 설치본에서는 접속 정보를 다시 입력할 필요가 없습니다. 아래는 다른 환경에 새로 설치할 때의 순서입니다.

Jetson에서 이 폴더 전체를 준비한 뒤 실행합니다.

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# .env가 이미 있으면 다음 복사 명령은 생략하세요.
cp .env.example .env
```

Jetson의 기존 OpenCV를 사용합니다. `python -c "import cv2; print(cv2.__version__)"`가 실패하면 OpenCV가 설치된 Python 환경을 사용해야 합니다. Windows에서 새로 설치한다면 `python -m pip install opencv-python`을 사용할 수 있습니다.

`.env`의 `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`에 Aiven 서비스의 접속 정보를 입력하고, CA 인증서를 같은 폴더의 `ca.pem`으로 저장합니다. `MYSQL_DATABASE=defaultdb`를 기본으로 사용합니다. 실제 접속 정보는 `.env.example`에 넣지 마세요.

```bash
chmod 600 .env
python aiven_logs.py --init-db
python app.py --vision --camera 0
```

젯슨에서 `http://127.0.0.1:5080`을 엽니다. PC에서도 보려면 공개 포트 대신 SSH 터널을 사용할 수 있습니다.

```powershell
ssh -L 5080:127.0.0.1:5080 jetson@192.168.11.5
```

그 상태에서 PC의 `http://127.0.0.1:5080`을 엽니다. 터널 세션은 계속 켜 두세요. 로그인 키를 쓰는 환경에서는 기존 SSH 명령에 `-L 5080:127.0.0.1:5080`을 추가하세요.

## 실행 옵션

이미 백그라운드 서버가 실행 중이면 먼저 이 폴더에서 `python stop_jetson.py`로 종료한 뒤 아래 명령을 실행합니다. 다른 프로세스는 종료하지 않습니다.

카메라 연결 실패 시 5초마다 다시 시도합니다. 나중에 USB 카메라를 연결하면 자동으로 영상 처리를 시작합니다. 같은 오류 상태는 DB에 반복 기록하지 않습니다.

```bash
# HTML과 DB 조회만 실행: 카메라/하드웨어 미구동
python app.py

# 비전 실행 + OpenCV 창에서 q 종료 / s 스냅샷
python app.py --vision --camera 0 --preview

# 실제 L9110S/LED 배선을 확인하고 하드웨어 제어를 켜는 경우
python app.py --vision --camera 0 --hardware-module team_hardware
```

`team_hardware.py`에 사용자가 제공한 L9110S 배선을 반영했습니다. `NORMAL`은 초록 LED와 벨트 구동, `WARNING`은 경고 LED와 벨트 정지, `DANGER`/`ERROR`는 빨간 LED와 벨트 정지입니다. 정상과 경고의 HTML 색상은 각각 초록과 노랑입니다. GPIO 명령을 성공적으로 적용한 뒤 정지면 `True`, 구동이면 `False`를 반환합니다. 자세한 핀표와 원그림의 반영 범위는 [하드웨어 연결 안내](hardware_integration.md)에 있습니다.

하드웨어 어댑터를 지정하지 않으면 비전 표시와 로그 저장만 수행합니다. 위험은 빨강으로 표시하며, 실제 모터 정지 명령 적용은 미확인으로 기록합니다. 정지 적용이 전달된 경우에만 HTML에 **작동정지**와 정지 이유가 표시됩니다. 이 값은 소프트웨어의 명령 적용 결과이며 물리적 정지 센서의 확인 결과와는 구분합니다.

## 기록 방법

`vision_runtime.py`가 상태 변화와 비전 오류를 기록합니다. 매 프레임을 DB에 쓰지 않습니다. 다른 FSM에서 직접 기록하려면 `jetson_integration_example.py`를 참고하세요.

```python
from datetime import datetime, timezone
from aiven_logs import JetsonEventLogger

logger = JetsonEventLogger()
# 기존 FSM이 정지 명령을 적용한 실제 지점에서 호출
logger.record(
    'DANGER', '작업자 위험 접근으로 벨트 모터 정지 명령 적용',
    occurred_at=datetime.now(timezone.utc),
    equipment_stopped=True, stop_reason='작업자 위험 접근',
)
# 프로그램 종료 시
logger.close()
```

인터넷이 끊기면 `jetson_log_outbox.sqlite3`에 보관합니다. 연결되면 같은 이벤트 UUID로 재전송하여 중복을 막고, 원래 발생 날짜·시간을 유지합니다. 프로그램을 다시 실행해도 대기 로그가 남습니다. 젯슨의 시계가 맞아야 실제 발생 시각도 정확합니다.

## SQL로 직접 확인

```sql
SELECT event_date AS `발생 날짜`, event_time AS `발생 시간`,
       severity AS `상태`, event_description AS `이벤트 설명`,
       equipment_stopped AS `정지 명령 적용`, stop_reason AS `정지 이유`
FROM conveyor_event_logs
WHERE device_id = 'jetson-orin-nano-01'
ORDER BY occurred_at_utc DESC, id DESC
LIMIT 100;
```

## 연결 근거

- 사용자 프로젝트 계획서 및 MySQL 실습 노트북의 CREATE TABLE 흐름을 사용했습니다.
- [Aiven Python 연결 안내](https://aiven.io/docs/products/mysql/howto/connect-with-python)의 PyMySQL 방식을 사용합니다.
- [Aiven CA 인증서 안내](https://aiven.io/docs/products/mysql/howto/connect-from-mysql-workbench)를 따라 CA 파일을 사용하고, 코드에서 인증서와 서버 이름 검증을 활성화했습니다.

## 주요 파일

`app.py`: HTML/API 서버 · `vision_runtime.py`: 원본 비전 통합 · `team_hardware.py`: L9110S/LED 제어 · `aiven_logs.py`: DB/전송 대기함 · `schema.sql`: 로그 테이블 · `index.html`/`dashboard.js`: 화면.

`setup_local.py`는 기존 접속 URI와 CA 인증서를 로컬 `.env`에 저장하는 설정 도구입니다. 기본 실행에 필요하지 않습니다. 사용했다면 설정을 마친 뒤 종료하세요. `.env`, `ca.pem`, 전송 대기함은 팀 공유 ZIP에 포함하지 마세요.

## 확인한 범위

2026-09-30에 젯슨에서 발생한 실제 카메라 연결 실패 이벤트가 Aiven MySQL에 저장되고 HTML에 표시되는 것을 확인했습니다. Python과 JavaScript의 문법도 확인했습니다. 카메라가 연결되지 않아 실제 얼굴 영상·거리 정확도와 LED/DC 모터 동작은 확인하지 않았습니다. 하드웨어 어댑터는 활성화하지 않았습니다. 원본 코드·임시 회로 그림 사본은 `reference/`에, 확인 시점의 로그 정보와 원본 해시는 `connection_evidence.json`에 있습니다.
