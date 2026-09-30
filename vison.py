import cv2
import time
from jetson_pin import OutputPin
from ultralytics import YOLO

# AI 모델 로드 및 CUDA 할당
model_path = 'yolov8n.pt'
model = YOLO(model_path).to('cuda')

# 핀 설정
led_r = OutputPin(7)    # 빨강: 비상정지
led_y = OutputPin(15)   # 노랑: 경고
led_g = OutputPin(32)   # 초록: 정상 작동

in1 = OutputPin(33)     # 모터 드라이버 IN1
in2 = OutputPin(29)     # 모터 드라이버 IN2

# 카메라 초기화
camera = cv2.VideoCapture(0)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not camera.isOpened():
    print('카메라를 열 수 없습니다.')
    exit(1)

def run_motor():
    """모터 정방향 회전"""
    in1.on()
    in2.off()

def stop_motor():
    """모터 정지"""
    in1.off()
    in2.off()

def set_led(status):
    """상태별 LED 제어 ('green', 'yellow', 'red')"""
#    print(status)
    
    if status == 'green' :
        led_g.on()     
    else :
        led_g.off()
    
    if status == 'yellow' :
        led_y.on() 
    else :
        led_y.off()

    if status == 'red' :
        led_r.on() 
    else :
        led_r.off()

# 거리 판별 기준 (화면 전체 면적 대비 사람 바운딩 박스 비율)
FRAME_AREA = 640 * 480
WARN_RATIO = 0.15   # 15% 이상: 사람 접근 경고
STOP_RATIO = 0.35   # 35% 이상: 위험 수준 초근접

try:
    while True:
        ret, frame = camera.read()
        if not ret:
            print("프레임을 읽을 수 없습니다.")
            break

        # YOLO 추론 (0번 클래스: person)
        results = model(frame, classes=[0], verbose=False)
        
        max_person_area = 0
        boxes = results[0].boxes

        # 화면 내 감지된 사람 중 가장 큰 면적(가장 가까운 사람) 계산
        if boxes is not None and len(boxes) > 0:
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                area = (x2 - x1) * (y2 - y1)
                if area > max_person_area:
                    max_person_area = area

        occupancy_ratio = max_person_area / FRAME_AREA

        # 단계별 제어 로직
        if occupancy_ratio >= STOP_RATIO:
            # 3단계: 초근접 -> 비상 정지 (빨강등 ON, 모터 STOP)
            stop_motor()
            set_led('red')
            state_text = f"EMERGENCY STOP ({occupancy_ratio*100:.1f}%)"
            text_color = (0, 0, 255)
        elif occupancy_ratio >= WARN_RATIO:
            # 2단계: 접근 중 -> 경고 (노랑등 ON, 모터 작동 유지)
            run_motor()
            set_led('yellow')
            state_text = f"WARNING: PERSON NEAR ({occupancy_ratio*100:.1f}%)"
            text_color = (0, 255, 255)
        else:
            # 1단계: 안전/미감지 -> 정상 가동 (초록등 ON, 모터 RUN)
            run_motor()
            set_led('green')
            state_text = "NORMAL RUNNING"
            text_color = (0, 255, 0)

finally:
    # 안전 종료: 모터 정지, LED OFF, GPIO 라인 해제
    stop_motor()
    led_r.off()
    led_y.off()
    led_g.off()

    in1.close()
    in2.close()
    led_r.close()
    led_y.close()
    led_g.close()

    camera.release()