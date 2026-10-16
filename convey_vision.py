"""얼굴 거리로 컨베이어 벨트를 제어하고, 같은 화면과 기록을 HTML로 보여주는 프로그램.

이 파일 하나만 실행하면 됩니다.
  python3 rail_face1.py
  -> 카메라 + 얼굴 거리 판단 + 벨트/LED 제어 (이 파일)
  -> HTML 대시보드 http://<Jetson IP>:5080 (카메라 화면과 이벤트 기록만 표시, 같은 Wi-Fi에서 접속)
  -> 이벤트 기록은 Aiven DB에 저장 (HTML의 'DB 연동' 버튼으로 켜고 끔)

동작 요약
  - 카메라로 얼굴을 찾고, 얼굴 박스 높이로 거리를 추정합니다.
  - 가장 가까운 얼굴의 거리로 상태를 정합니다.
      green  : 200cm 초과 또는 얼굴 없음  -> 벨트 작동
      yellow : 50cm 초과 ~ 200cm 이하      -> 벨트 작동 유지 + 노랑 LED (경고만)
      red    : 50cm 이하                   -> 벨트 정지 + 빨강 LED
  - 위험해지는 쪽(green -> yellow -> red)은 즉시 바뀝니다.
    안전해지는 쪽(red -> yellow -> green)은 RELEASE_SECONDS 동안 계속 유지될 때만 바뀝니다.
    얼굴 검출이 잠깐 끊겨도 벨트가 움직이지 않게 하기 위해서입니다.
  - 종료: 터미널에서 Ctrl+C, 카메라 창에서 q, 또는 HTML의 '대시보드 종료' 버튼
  - 스냅샷: 카메라 창에서 s

현재 배선 (BOARD 핀 번호)
  1번  -> 모터 드라이버 VCC
  9번  -> 모터 드라이버 GND
  15번 -> 빨강 LED
  31번 -> 노랑 LED (경고)
  33번 -> 모터 드라이버 B-1A
  B-1B -> 연결 안 됨 (L9110S 모듈에서는 연결 안 된 입력이 HIGH로 유지됨)

L9110S는 두 입력(B-1A, B-1B)이 서로 다를 때만 모터를 돌립니다.
B-1B가 항상 HIGH이므로
  33번 LOW  -> 입력이 서로 다름 -> 벨트 작동
  33번 HIGH -> 입력이 같음      -> 벨트 정지
즉 이 배선에서는 33번을 LOW로 내려야 벨트가 돕니다.
나중에 B-1B를 다른 GPIO 핀에 연결하면 run_motor / stop_motor를 다시 맞춰야 합니다.
"""
import logging
import os
import signal
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from types import SimpleNamespace

import cv2
import Jetson.GPIO as GPIO

# HTML 대시보드 코드(conveyor_dashboard 폴더)를 그대로 가져다 씁니다.
DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'conveyor_dashboard')
sys.path.insert(0, DASHBOARD_DIR)
import app as dashboard                              # noqa: E402  Flask 웹 서버 (HTML, 영상, 로그 API)
from aiven_logs import JetsonEventLogger, KST        # noqa: E402  이벤트 -> Aiven DB 저장
from vision_runtime import FrameHub                  # noqa: E402  최신 카메라 화면을 HTML로 전달

# ---------------------------------------------------------------------------
# 설정값
# ---------------------------------------------------------------------------

# 얼굴 검출 모델 파일과 다운로드 주소
MODEL_FILENAME = 'face_detection_yunet_2023mar.onnx'
MODEL_URL = 'https://huggingface.co/opencv/face_detection_yunet/resolve/main/' + MODEL_FILENAME

# 핀 번호 (BOARD 기준, 위 "현재 배선"과 같아야 함)
PIN_LED_RED = 15     # 빨강 LED: 위험(red)
PIN_LED_BLUE = 31    # 노랑 LED: 경고(yellow). 파랑 LED에서 교체, 변수 이름은 그대로 둠
PIN_MOTOR = 33       # 모터 드라이버 B-1A

# 거리 기준 (cm)
DANGER_DISTANCE = 50     # 이 거리 이하면 red
WARNING_DISTANCE = 200   # 이 거리 이하면 yellow

# 얼굴 박스 높이(px)를 거리(cm)로 바꾸는 비례상수 (실험으로 구한 근삿값)
DISTANCE_CONSTANT = 13000

# 안전해지는 쪽으로 상태를 바꾸기 전에 기다리는 시간(초)
RELEASE_SECONDS = 1.0

# HTML 대시보드 주소 (0.0.0.0 = 같은 Wi-Fi의 다른 기기에서도 http://<Jetson IP>:5080 으로 접속)
WEB_HOST = '0.0.0.0'
WEB_PORT = 5080

# 상태 이름(화면/LED용)과 대시보드 기록용 이름, 위험 순위
STATUS_SEVERITY = {'green': 'NORMAL', 'yellow': 'WARNING', 'red': 'DANGER'}
STATUS_RANK = {'green': 0, 'yellow': 1, 'red': 2}

# 상태별 화면 박스 색 (OpenCV는 BGR 순서)
BOX_COLORS = {
    'green': (0, 255, 0),
    'yellow': (0, 255, 255),
    'red': (0, 0, 255),
}


# ---------------------------------------------------------------------------
# 모델 파일 준비
# ---------------------------------------------------------------------------

def prepare_model():
    """모델 파일이 없거나 깨져 있으면(10KB 미만) 새로 내려받습니다."""
    if os.path.exists(MODEL_FILENAME) and os.path.getsize(MODEL_FILENAME) < 10000:
        os.remove(MODEL_FILENAME)
    if not os.path.exists(MODEL_FILENAME):
        print(f'{MODEL_FILENAME} 모델을 다운로드합니다...')
        request = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request) as response, open(MODEL_FILENAME, 'wb') as out_file:
            out_file.write(response.read())
        print(f'다운로드 완료! (파일 크기: {os.path.getsize(MODEL_FILENAME) / 1024:.1f} KB)')


# ---------------------------------------------------------------------------
# 거리와 상태 판단
# ---------------------------------------------------------------------------

def get_distance(box_height):
    """얼굴 박스 높이(px)로 거리(cm)를 추정합니다. 박스가 클수록 가깝습니다."""
    if box_height <= 0:
        return 0
    return DISTANCE_CONSTANT / box_height


def get_status(distance):
    """거리(cm)로 상태를 정합니다. 얼굴이 없으면 distance는 None입니다."""
    if distance is None or distance > WARNING_DISTANCE:
        return 'green'
    if distance > DANGER_DISTANCE:
        return 'yellow'
    return 'red'


class StatusFilter:
    """상태가 프레임마다 깜빡이지 않게 거릅니다.

    - 더 위험한 상태(순위가 높은 쪽)는 바로 적용합니다. 안전이 우선입니다.
    - 덜 위험한 상태는 RELEASE_SECONDS 동안 계속 나올 때만 적용합니다.
    """

    def __init__(self):
        self.status = 'green'
        self.release_started = None   # 덜 위험한 상태가 처음 나온 시각

    def update(self, detected_status):
        if STATUS_RANK[detected_status] >= STATUS_RANK[self.status]:
            self.status = detected_status
            self.release_started = None
        elif self.release_started is None:
            self.release_started = time.monotonic()
        elif time.monotonic() - self.release_started >= RELEASE_SECONDS:
            self.status = detected_status
            self.release_started = None
        return self.status


# ---------------------------------------------------------------------------
# 모터와 LED 제어
# ---------------------------------------------------------------------------

def run_motor():
    """벨트 작동: 33번 LOW (B-1B는 HIGH로 고정되어 있어 두 입력이 달라짐)."""
    GPIO.output(PIN_MOTOR, GPIO.LOW)


def stop_motor():
    """벨트 정지: 33번 HIGH (B-1B와 같은 HIGH가 되어 모터가 멈춤)."""
    GPIO.output(PIN_MOTOR, GPIO.HIGH)


def set_leds(status):
    """상태에 맞는 LED만 켭니다. green이면 둘 다 끕니다(초록 LED는 없음)."""
    GPIO.output(PIN_LED_BLUE, GPIO.HIGH if status == 'yellow' else GPIO.LOW)
    GPIO.output(PIN_LED_RED, GPIO.HIGH if status == 'red' else GPIO.LOW)


def apply_status(status):
    """상태에 맞게 벨트와 LED를 함께 바꿉니다. 정지를 LED보다 먼저 적용합니다."""
    # 빨강(위험)일 때만 멈춥니다. 노랑(경고)은 노랑 LED로 알리기만 하고 벨트는 계속 돕니다.
    if status == 'red':
        stop_motor()
    else:
        run_motor()
    set_leds(status)


# ---------------------------------------------------------------------------
# 이벤트 기록 (HTML 상태 표시 + Aiven DB)
# ---------------------------------------------------------------------------

def describe(status, distance, face_count):
    """기록에 남길 설명과 정지 이유를 만듭니다. 정지는 red뿐이라 정지 이유도 red만 있습니다."""
    if distance is None:
        description = '얼굴 미검출 · 작업자 없음, 정상 작동'
    else:
        description = f'얼굴 {face_count}명 검출 · 가장 가까운 추정거리 {distance:.1f}cm'
    if status == 'yellow':
        description += f' · 접근 경고({WARNING_DISTANCE}cm 이하), 벨트 작동 유지'
    stop_reason = f'작업자 위험 접근({DANGER_DISTANCE}cm 이하)' if status == 'red' else None
    return description, stop_reason


def make_live_event(severity, description, stop_reason, event_id=None):
    """HTML 상태 줄에 보여줄 현재 상태입니다. (대시보드가 기대하는 형식)"""
    now = datetime.now(timezone.utc)
    local = now.astimezone(KST)
    return {
        'event_id': event_id, 'severity': severity,
        'event_description': description,
        'equipment_stopped': severity == 'DANGER',
        'stop_reason': stop_reason,
        'occurred_at_utc': now.isoformat(),
        'event_date': local.strftime('%Y-%m-%d'),
        'event_time': local.strftime('%H:%M:%S'),
    }


def record_event(logger, severity, description, stop_reason):
    """이벤트를 DB 전송 대기함에 넣습니다. DB 연동이 꺼져 있으면 저장하지 않습니다."""
    try:
        return logger.record(severity, description,
                             equipment_stopped=severity == 'DANGER', stop_reason=stop_reason)
    except Exception as error:   # 기록 실패가 벨트 제어를 멈추게 하면 안 됩니다.
        print(f'이벤트 기록 실패: {error}', flush=True)
        return None


# ---------------------------------------------------------------------------
# 화면 표시
# ---------------------------------------------------------------------------

def draw_face(frame, face, distance):
    """얼굴 박스, 랜드마크 점, 박스 크기와 거리를 화면에 그립니다."""
    x, y, box_width, box_height = map(int, face[:4])
    color = BOX_COLORS[get_status(distance)]

    cv2.rectangle(frame, (x, y), (x + box_width, y + box_height), color, 2)

    # 랜드마크 5개: 오른쪽 눈, 왼쪽 눈, 코, 오른쪽 입꼬리, 왼쪽 입꼬리
    for landmark_x, landmark_y in face[4:14].reshape((5, 2)):
        cv2.circle(frame, (int(landmark_x), int(landmark_y)), 3, (0, 0, 255), -1)

    cv2.putText(frame, f'{box_width}x{box_height}, {distance:.1f}cm',
                (x, max(20, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


def start_dashboard(hub, logger):
    """HTML 대시보드를 별도 스레드로 띄웁니다. 카메라 화면과 기록만 보여줍니다."""
    # 대시보드 코드는 vision.hub(화면)와 vision.logger(DB 연동 버튼)를 사용합니다.
    dashboard.vision = SimpleNamespace(hub=hub, logger=logger)
    # 1초마다 들어오는 HTML 요청 로그가 터미널을 덮지 않게 숨깁니다.
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    thread = threading.Thread(
        target=dashboard.app.run,
        kwargs=dict(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True, use_reloader=False),
        daemon=True,   # 메인 프로그램이 끝나면 함께 끝납니다.
    )
    thread.start()
    print(f'HTML 대시보드: http://{WEB_HOST}:{WEB_PORT}', flush=True)


def stop_on_sigterm(signum, frame):
    """HTML의 '대시보드 종료' 버튼은 SIGTERM을 보냅니다. Ctrl+C처럼 정리 후 종료합니다."""
    raise SystemExit(0)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

prepare_model()

# 시작할 때 벨트는 정지(33번 HIGH), LED는 모두 끈 상태로 둡니다.
GPIO.setmode(GPIO.BOARD)
GPIO.setup([PIN_LED_RED, PIN_LED_BLUE], GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(PIN_MOTOR, GPIO.OUT, initial=GPIO.HIGH)

# SSH 터미널에서 실행해도 Jetson 모니터에 카메라 창을 띄웁니다.
os.environ.setdefault('DISPLAY', ':0')
signal.signal(signal.SIGTERM, stop_on_sigterm)

hub = FrameHub()
logger = JetsonEventLogger()
start_dashboard(hub, logger)

camera = None
try:
    # Jetson(Linux)의 USB 웹캠은 0번입니다.
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise IOError('카메라를 열 수 없습니다.')

    frame_width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
    detector = cv2.FaceDetectorYN.create(
        model=MODEL_FILENAME,
        config='',
        input_size=(frame_width, frame_height),
        score_threshold=0.8,   # 이 점수 이상인 얼굴만 인정
        nms_threshold=0.3,     # 겹치는 박스 정리 기준
    )

    status_filter = StatusFilter()
    previous_status = None   # 상태가 바뀔 때만 출력·기록하기 위해 직전 상태를 기억
    live_event = None        # HTML 상태 줄에 보여줄 현재 상태

    while True:
        ok, frame = camera.read()
        if not ok:
            print('프레임을 읽을 수 없습니다.')
            break

        # 얼굴 검출: faces는 얼굴마다 [x, y, w, h, 랜드마크 10개, 점수] 배열, 없으면 None
        _, faces = detector.detect(frame)

        # 모든 얼굴의 거리를 구하고 화면에 표시
        distances = []
        if faces is not None:
            for face in faces:
                distance = get_distance(int(face[3]))   # face[3] = 박스 높이
                distances.append(distance)
                draw_face(frame, face, distance)

        # 가장 가까운 얼굴로 상태 결정 (얼굴 없으면 None -> green), 깜빡임 거르기
        distance = min(distances) if distances else None
        status = status_filter.update(get_status(distance))
        apply_status(status)

        # 상태가 바뀐 순간에만 터미널 출력 + 이벤트 기록
        if status != previous_status:
            severity = STATUS_SEVERITY[status]
            description, stop_reason = describe(status, distance, len(distances))
            event_id = record_event(logger, severity, description, stop_reason)
            live_event = make_live_event(severity, description, stop_reason, event_id)
            distance_text = '얼굴 없음' if distance is None else f'{distance:.1f}cm'
            belt_text = '정지' if status == 'red' else '작동'
            print(f'[{status}] {distance_text} -> 컨베이어 {belt_text}', flush=True)
            previous_status = status

        # HTML로 최신 화면과 현재 상태 보내기
        encoded_ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if encoded_ok:
            hub.publish(jpeg.tobytes(), live_event)

        # Jetson 모니터의 카메라 창
        cv2.imshow('Face Detection (q = quit)', frame)
        key = cv2.waitKey(25) & 0xFF
        if key == ord('q'):
            break
        if key == ord('s'):
            cv2.imwrite(f'snapshot_{int(time.time())}.png', frame)

finally:
    # 어떤 이유로 끝나든 벨트를 멈추고 LED를 끈 뒤 핀과 카메라를 놓습니다.
    stop_motor()
    set_leds(None)
    GPIO.cleanup()
    if camera is not None:
        camera.release()
    cv2.destroyAllWindows()

    # 종료 기록을 남기고, HTML 영상 전송을 끝내고, 남은 DB 전송을 정리합니다.
    try:
        logger.record('NORMAL', '프로그램 종료로 벨트 모터를 정지했습니다.',
                      equipment_stopped=True, stop_reason='프로그램 종료')
    except Exception as error:
        print(f'종료 기록 실패: {error}', flush=True)
    with hub.condition:
        hub.closed = True
        hub.condition.notify_all()
    logger.close()
    print('종료했습니다.', flush=True)
