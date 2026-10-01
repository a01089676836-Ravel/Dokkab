import os
import urllib.request
import cv2
import time
import Jetson.GPIO as GPIO

# 얼굴 검출 모델
model_filename = 'face_detection_yunet_2023mar.onnx'
# HuggingFace에서 모델 링크
model_url = ('https://huggingface.co/opencv/face_detection_yunet/resolve/main/' + model_filename)

# 모델 파일 확인 후 재 다운로드
if os.path.exists(model_filename) :
    if os.path.getsize(model_filename) < 10000 :
        os.remove(model_filename)
# 모델 파일이 없으면 다운로드
if not os.path.exists(model_filename) :
    print(f'{model_filename} 모델을 다운로드합니다...')
    req = urllib.request.Request(model_url, headers={'User-Agent' : 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response, open(model_filename, 'wb') as out_file :
        out_file.write(response.read())
    print(f'다운로드 완료! (파일 크기 : {os.path.getsize(model_filename) / 1024:.1f} KB)')

# 핀 번호 (BOARD 기준, rail.py와 동일)
led_r = 15
led_b = 31
led_g = 32
in1 = 33
in2 = 29

GPIO.setmode(GPIO.BOARD)
GPIO.setup([led_r, led_b, led_g, in1, in2], GPIO.OUT, initial=GPIO.LOW)


def run_motor():
    """컨베이어 작동"""
    GPIO.output(in1, GPIO.HIGH)
    GPIO.output(in2, GPIO.LOW)


def stop_motor():
    """컨베이어 정지"""
    GPIO.output(in1, GPIO.LOW)
    GPIO.output(in2, GPIO.LOW)


def set_led(status):
    """green / yellow / red 상태 LED 표시"""
    GPIO.output(led_g, GPIO.HIGH if status == 'green' else GPIO.LOW)
    GPIO.output(led_b, GPIO.HIGH if status == 'yellow' else GPIO.LOW)
    GPIO.output(led_r, GPIO.HIGH if status == 'red' else GPIO.LOW)


# 얼굴 박스 높이를 이용한 거리 측정 함수
def distance_face(box_h):
    if box_h <= 0:
        return 0

    # 실험으로 구한 대략적인 비례상수
    return 13000 / box_h


# SSH 터미널에서 실행해도 Jetson 모니터에 창을 띄움
os.environ.setdefault('DISPLAY', ':0')

cap = None
try:
    # 카메라 연결 (Jetson은 Linux라 CAP_MSMF 대신 0번 카메라 사용)
    cap = cv2.VideoCapture(0)
    if not cap.isOpened() :
        raise IOError('카메라를 열 수 없습니다.')
    # 동영상 사이즈 추출
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    # 얼굴 검출 모델 생성
    detector = cv2.FaceDetectorYN.create(
        model=model_filename,
        config='',
        input_size=(width,height),
        score_threshold=0.8,
        nms_threshold=0.3
    )

    last_status = None

    # 실시간 카메라 프레임 추출
    while True:
        ret, frame = cap.read()

        if not ret:
            print('프레임을 읽을 수 없습니다.')
            break

        # 얼굴 검출 모델 실행
        _, faces = detector.detect(frame)

        # 가장 가까운 얼굴 거리 (얼굴 없으면 None)
        nearest = None

        if faces is not None:
            for face in faces:
                # 얼굴 영역 추출
                x, y, box_w, box_h = map(int, face[:4])

                # 얼굴 박스 높이를 이용한 거리 계산 및 안전 단계별 색상 선택
                distance = distance_face(box_h)
                if nearest is None or distance < nearest:
                    nearest = distance
                if distance <= 50:
                    box_color = (0, 0, 255)
                elif distance <= 200:
                    box_color = (0, 255, 255)
                else:
                    box_color = (0, 255, 0)

                # 화면에 사각형 그리기
                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + box_w, y + box_h),
                    box_color,
                    2
                )

                # 랜드마크 설정
                landmarks = face[4:14].reshape((5, 2))

                # 랜드마크 위치에 점 찍기
                for lx, ly in landmarks:
                    cv2.circle(
                        frame,
                        (int(lx), int(ly)),
                        3,
                        (0, 0, 255),
                        -1
                    )

                # 얼굴 박스 크기와 거리 출력
                cv2.putText(
                    frame,
                    f'{box_w}x{box_h}, {distance:.1f}cm',
                    (x, max(20, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    box_color,
                    1
                )

        # 안전 단계별 컨베이어 제어: 경고(노랑)·위험(빨강)이면 정지
        if nearest is not None and nearest <= 50:
            status = 'red'
            stop_motor()
        elif nearest is not None and nearest <= 200:
            status = 'yellow'
            stop_motor()
        else:
            status = 'green'
            run_motor()
        set_led(status)

        # 상태가 바뀔 때만 터미널에 출력
        if status != last_status:
            dist_text = '얼굴 없음' if nearest is None else f'{nearest:.1f}cm'
            motor_text = '작동' if status == 'green' else '정지'
            print(f'[{status}] {dist_text} -> 컨베이어 {motor_text}', flush=True)
            last_status = status

        # 얼굴 검출 결과 표시
        cv2.imshow('Face Detection(q = quit)',frame)
        key = cv2.waitKey(25) & 0Xff
        if key == ord('q') :
            break
        elif key == ord('s') :  # 스냅샷
            # 현재시간기반 파일명 생성
            filename = f'snapshot_{int(time.time())}.png'
            # 프레임 저장
            cv2.imwrite(filename, frame)

finally:
    stop_motor()
    set_led(None)
    GPIO.cleanup()

    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()
