import os
import urllib.request
import cv2
import time

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

# 카메라 연결
cap = cv2.VideoCapture(0) # 카메라 번호 유의할것
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

# 얼굴 박스 높이를 이용한 거리 측정 함수
def distance_face(box_h):
    if box_h <= 0:
        return 0

    # 실험으로 구한 대략적인 비례상수
    return 13000 / box_h


# 실시간 카메라 프레임 추출
while True:
    ret, frame = cap.read()

    if not ret:
        break

    # 프레임 사이즈 추출
    h, w, _ = frame.shape

    # 얼굴 검출 모델 실행
    _, faces = detector.detect(frame)

    if faces is not None:
        for face in faces:
            # 얼굴 영역 추출
            x, y, box_w, box_h = map(int, face[:4])

            # 얼굴 박스 높이를 이용한 거리 계산 및 안전 단계별 색상 선택
            distance = distance_face(box_h)
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

            score = face[-1]

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
cap.release()
cv2.destroyAllWindows()