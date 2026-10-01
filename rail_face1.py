"""얼굴 거리로 컨베이어 벨트를 제어하는 프로그램 (rail_face.py 정리본).

동작 요약
  - 카메라로 얼굴을 찾고, 얼굴 박스 높이로 거리를 추정합니다.
  - 가장 가까운 얼굴의 거리로 상태를 정합니다.
      green  : 200cm 초과 또는 얼굴 없음  -> 벨트 작동
      yellow : 50cm 초과 ~ 200cm 이하      -> 벨트 정지 + 파랑 LED
      red    : 50cm 이하                   -> 벨트 정지 + 빨강 LED
  - 종료: 카메라 창에서 q, 또는 터미널에서 Ctrl+C
  - 스냅샷: 카메라 창에서 s

현재 배선 (BOARD 핀 번호)
  1번  -> 모터 드라이버 VCC
  9번  -> 모터 드라이버 GND
  15번 -> 빨강 LED
  31번 -> 파랑 LED (경고)
  33번 -> 모터 드라이버 B-1A
  B-1B -> 연결 안 됨 (L9110S 모듈에서는 연결 안 된 입력이 HIGH로 유지됨)

L9110S는 두 입력(B-1A, B-1B)이 서로 다를 때만 모터를 돌립니다.
B-1B가 항상 HIGH이므로
  33번 LOW  -> 입력이 서로 다름 -> 벨트 작동
  33번 HIGH -> 입력이 같음      -> 벨트 정지
즉 이 배선에서는 33번을 LOW로 내려야 벨트가 돕니다.
나중에 B-1B를 다른 GPIO 핀에 연결하면 run_motor / stop_motor를 다시 맞춰야 합니다.
"""
import os
import time
import urllib.request

import cv2
import Jetson.GPIO as GPIO

# ---------------------------------------------------------------------------
# 설정값
# ---------------------------------------------------------------------------

# 얼굴 검출 모델 파일과 다운로드 주소
MODEL_FILENAME = 'face_detection_yunet_2023mar.onnx'
MODEL_URL = 'https://huggingface.co/opencv/face_detection_yunet/resolve/main/' + MODEL_FILENAME

# 핀 번호 (BOARD 기준, 위 "현재 배선"과 같아야 함)
PIN_LED_RED = 15     # 빨강 LED: 위험(red)
PIN_LED_BLUE = 31    # 파랑 LED: 경고(yellow)
PIN_MOTOR = 33       # 모터 드라이버 B-1A

# 거리 기준 (cm)
DANGER_DISTANCE = 50     # 이 거리 이하면 red
WARNING_DISTANCE = 200   # 이 거리 이하면 yellow

# 얼굴 박스 높이(px)를 거리(cm)로 바꾸는 비례상수 (실험으로 구한 근삿값)
DISTANCE_CONSTANT = 13000

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
    if status == 'green':
        run_motor()
    else:
        stop_motor()
    set_leds(status)


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

    previous_status = None   # 상태가 바뀔 때만 출력하기 위해 직전 상태를 기억

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

        # 가장 가까운 얼굴로 상태 결정 (얼굴 없으면 None -> green)
        distance = min(distances) if distances else None
        status = get_status(distance)
        apply_status(status)

        # 상태가 바뀐 순간에만 한 줄 출력
        if status != previous_status:
            distance_text = '얼굴 없음' if distance is None else f'{distance:.1f}cm'
            belt_text = '작동' if status == 'green' else '정지'
            print(f'[{status}] {distance_text} -> 컨베이어 {belt_text}', flush=True)
            previous_status = status

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
