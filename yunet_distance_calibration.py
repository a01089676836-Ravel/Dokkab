"""YuNet 얼굴 검출 결과로 거리 보정 데이터를 모으는 스크립트.

5.00 m에서 시작해 0.45 m 간격으로 가까워지며, 각 위치에서 Space를 눌러
얼굴 상자가 그려진 사진과 실제 거리 라벨을 저장합니다. 마지막에는
얼굴 상자 가로·세로 크기를 이용한 간단한 거리 보정식을 저장합니다.
"""

import csv
import json
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


# 얼굴 검출 모델 파일과 다운로드 주소
MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"
MODEL_URL = (
    "https://huggingface.co/opencv/face_detection_yunet/resolve/main/"
    + MODEL_FILENAME
)
MIN_MODEL_BYTES = 10_000

# 사용자 코드 기준. 카메라가 열리지 않으면 1을 0, 2 등으로 바꿔 확인합니다.
CAMERA_INDEX = 1

# 측정 위치: 5.00 m에서 45 cm씩 가까워져 0.50 m까지
START_DISTANCE_M = 5.00
STEP_DISTANCE_M = 0.45
END_DISTANCE_M = 0.50

# YuNet 검출 시작값. 실제 환경에서 검출 결과를 보고 조정합니다.
SCORE_THRESHOLD = 0.80
NMS_THRESHOLD = 0.30

# 결과는 실행 위치 아래에 실행 시각별 폴더로 저장합니다.
OUTPUT_ROOT = Path("face_distance_calibration")

CSV_FIELDS = [
    "captured_at",
    "step_number",
    "known_distance_m",
    "bbox_x_px",
    "bbox_y_px",
    "bbox_width_px",
    "bbox_height_px",
    "face_score",
    "frame_width_px",
    "frame_height_px",
    "image_file",
]


def build_distance_steps():
    """5.00 m부터 0.45 m씩 줄어드는 측정 거리 목록을 만듭니다."""
    distances = []
    distance = START_DISTANCE_M

    while distance >= END_DISTANCE_M - 1e-9:
        distances.append(round(distance, 2))
        distance = round(distance - STEP_DISTANCE_M, 10)

    return distances


def ensure_model(model_path):
    """모델이 없거나 너무 작으면 다운로드해 준비합니다."""
    if model_path.exists() and model_path.stat().st_size < MIN_MODEL_BYTES:
        model_path.unlink()

    if model_path.exists():
        return

    print(f"모델을 다운로드합니다: {MODEL_FILENAME}")
    request = urllib.request.Request(
        MODEL_URL,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    temporary_path = model_path.with_suffix(model_path.suffix + ".download")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            model_bytes = response.read()

        if len(model_bytes) < MIN_MODEL_BYTES:
            raise ValueError("다운로드한 모델 파일이 너무 작습니다.")

        temporary_path.write_bytes(model_bytes)
        temporary_path.replace(model_path)
        print(f"다운로드 완료: {model_path} ({len(model_bytes) / 1024:.1f} KB)")
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def append_csv_row(csv_path, row):
    """샘플을 즉시 CSV에 추가해 촬영 도중 종료되어도 기록을 보존합니다."""
    write_header = not csv_path.exists()

    with csv_path.open("a", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def draw_detections(frame, faces):
    """얼굴 상자, 5개 랜드마크, 검출 점수를 영상에 표시합니다."""
    display = frame.copy()

    if faces is None:
        return display

    for face in faces:
        x, y, box_width, box_height = face[:4]
        x, y = int(round(x)), int(round(y))
        box_width, box_height = int(round(box_width)), int(round(box_height))

        cv2.rectangle(
            display,
            (x, y),
            (x + box_width, y + box_height),
            (0, 255, 0),
            2,
        )

        # YuNet 얼굴 결과의 4~13번 값은 5개 랜드마크의 x, y 좌표입니다.
        landmarks = face[4:14].reshape((5, 2))
        for landmark_x, landmark_y in landmarks:
            cv2.circle(
                display,
                (int(round(landmark_x)), int(round(landmark_y))),
                3,
                (0, 0, 255),
                -1,
            )

        score = float(face[14])
        cv2.putText(
            display,
            f"{score:.2f}",
            (x, max(20, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            2,
        )

    return display


def save_calibration_fit(records, output_dir):
    """수집한 상자 크기와 실제 거리로 간단한 보정식을 계산합니다.

    얼굴 상자 면적의 제곱근은 픽셀 단위 크기입니다. 이를 역수로 바꿔
    실제 거리와 직선 회귀를 하므로, YuNet 신경망 가중치를 재학습하는
    과정이 아니라 이 카메라·측정 조건에 맞는 거리 환산 보정입니다.
    """
    valid_records = [
        record
        for record in records
        if record["bbox_width_px"] > 0 and record["bbox_height_px"] > 0
    ]

    if len(valid_records) < 3:
        print("보정식을 계산하려면 얼굴 검출 샘플을 3개 이상 저장해야 합니다.")
        return

    box_sizes = np.sqrt(
        np.array(
            [
                record["bbox_width_px"] * record["bbox_height_px"]
                for record in valid_records
            ],
            dtype=np.float64,
        )
    )
    inverse_box_sizes = 1.0 / box_sizes
    known_distances = np.array(
        [record["known_distance_m"] for record in valid_records],
        dtype=np.float64,
    )

    slope, intercept = np.polyfit(inverse_box_sizes, known_distances, 1)
    predicted_distances = slope * inverse_box_sizes + intercept
    mean_absolute_error = float(
        np.mean(np.abs(predicted_distances - known_distances))
    )

    calibration = {
        "method": "linear_fit_using_inverse_sqrt_of_face_box_area",
        "formula": "distance_m = slope / sqrt(width_px * height_px) + intercept",
        "slope": float(slope),
        "intercept": float(intercept),
        "sample_count": len(valid_records),
        "training_mean_absolute_error_m": mean_absolute_error,
        "camera_index": CAMERA_INDEX,
        "model_filename": MODEL_FILENAME,
        "score_threshold": SCORE_THRESHOLD,
        "nms_threshold": NMS_THRESHOLD,
    }

    calibration_path = output_dir / "distance_calibration.json"
    calibration_path.write_text(
        json.dumps(calibration, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"보정식 저장: {calibration_path}")
    print(
        "거리식: distance_m = "
        f"{slope:.6f} / sqrt(width_px * height_px) + {intercept:.6f}"
    )
    print(f"수집 샘플 기준 평균 절대 오차: {mean_absolute_error:.3f} m")


def main():
    model_path = Path(MODEL_FILENAME)
    ensure_model(model_path)

    if not hasattr(cv2, "FaceDetectorYN"):
        raise RuntimeError(
            "현재 OpenCV에서 FaceDetectorYN을 찾지 못했습니다. "
            "OpenCV 버전과 설치 패키지를 확인하세요."
        )

    run_folder = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / run_folder
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "face_samples.csv"

    distances = build_distance_steps()
    records = []
    distance_index = 0
    cap = cv2.VideoCapture(CAMERA_INDEX)

    try:
        if not cap.isOpened():
            raise IOError(
                f"카메라 인덱스 {CAMERA_INDEX}를 열 수 없습니다. "
                "CAMERA_INDEX를 0 또는 다른 번호로 바꿔 확인하세요."
            )

        # 첫 프레임의 실제 해상도로 YuNet 입력 크기를 설정합니다.
        ret, frame = cap.read()
        if not ret:
            raise IOError("카메라에서 첫 프레임을 읽지 못했습니다.")

        frame_height, frame_width = frame.shape[:2]
        detector = cv2.FaceDetectorYN.create(
            model=str(model_path),
            config="",
            input_size=(frame_width, frame_height),
            score_threshold=SCORE_THRESHOLD,
            nms_threshold=NMS_THRESHOLD,
        )

        print(f"저장 폴더: {output_dir}")
        print("측정 위치에 얼굴을 정면으로 두고 Space를 누르세요.")
        print("N: 현재 위치 건너뛰기, Q: 종료")

        while distance_index < len(distances):
            target_distance = distances[distance_index]
            frame_height, frame_width = frame.shape[:2]
            detector.setInputSize((frame_width, frame_height))

            # detect는 (검출 상태, 얼굴 결과)를 반환합니다.
            _, faces = detector.detect(frame)
            face_count = 0 if faces is None else len(faces)
            display = draw_detections(frame, faces)

            cv2.putText(
                display,
                f"Target distance: {target_distance:.2f} m",
                (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 255, 0),
                2,
            )
            cv2.putText(
                display,
                "SPACE: save  |  N: skip  |  Q: quit",
                (15, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
            )
            face_message = f"Detected faces: {face_count}"
            if face_count != 1:
                face_message += " (save requires exactly one face)"
            cv2.putText(
                display,
                face_message,
                (15, 90),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2,
            )

            cv2.imshow("YuNet distance calibration", display)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("사용자 요청으로 촬영을 마칩니다.")
                break

            if key == ord("n"):
                print(f"{target_distance:.2f} m 위치를 건너뜁니다.")
                distance_index += 1

            elif key == 32:  # Space: 현재 거리의 얼굴 상자와 사진을 저장
                if face_count != 1:
                    print("얼굴이 정확히 하나 검출될 때만 저장할 수 있습니다.")
                else:
                    face = faces[0]
                    x, y, box_width, box_height = map(float, face[:4])
                    score = float(face[14])
                    captured_at = datetime.now().isoformat(timespec="seconds")
                    image_name = (
                        f"sample_{distance_index + 1:02d}_"
                        f"{target_distance:.2f}m_"
                        f"{datetime.now().strftime('%H%M%S')}.jpg"
                    )
                    image_path = output_dir / image_name

                    if not cv2.imwrite(str(image_path), display):
                        print(f"스크린샷 저장 실패: {image_path}")
                    else:
                        row = {
                            "captured_at": captured_at,
                            "step_number": distance_index + 1,
                            "known_distance_m": target_distance,
                            "bbox_x_px": round(x, 2),
                            "bbox_y_px": round(y, 2),
                            "bbox_width_px": round(box_width, 2),
                            "bbox_height_px": round(box_height, 2),
                            "face_score": round(score, 4),
                            "frame_width_px": frame_width,
                            "frame_height_px": frame_height,
                            "image_file": image_name,
                        }
                        append_csv_row(csv_path, row)
                        records.append(row)
                        print(
                            f"저장 완료: {target_distance:.2f} m, "
                            f"상자 {box_width:.1f} x {box_height:.1f} px, "
                            f"점수 {score:.3f}"
                        )
                        distance_index += 1

            # 다음 반복에서 새 카메라 프레임을 읽습니다.
            if distance_index < len(distances):
                ret, frame = cap.read()
                if not ret:
                    print("카메라 프레임을 더 읽지 못해 촬영을 종료합니다.")
                    break

        print(f"CSV 기록: {csv_path}")

    finally:
        cap.release()
        cv2.destroyAllWindows()

    save_calibration_fit(records, output_dir)


if __name__ == "__main__":
    main()
