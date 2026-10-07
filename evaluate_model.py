"""얼굴 검출(YuNet) + 거리 판단의 성능 평가: Accuracy, Precision/Recall/F1, FPS.

convey_vision.py와 같은 모델·설정·거리식·상태 기준으로 프레임마다 상태를 판단하고,
줄자로 잰 실제 거리로 정한 정답 상태와 비교합니다. GPIO(LED·벨트)는 건드리지 않습니다.

정답 상태
  빈 화면(사람 없음) 또는 200cm 초과 -> green
  50cm 초과 ~ 200cm 이하             -> yellow
  50cm 이하                          -> red

사용법 (Jetson 모니터에서 실행, convey_vision.py는 꺼 둔 상태로. 카메라는 한 프로그램만 열 수 있음)
  cd ~/Dokkab && conveyor_dashboard/.venv/bin/python evaluate_model.py
      화면에 나온 위치(TARGET)에 서서 Space -> 5초 뒤부터 프레임 60장을 자동 판단·저장
      n: 이 위치 건너뛰기, q: 종료. 끝나면 eval_results/<시각>/ 에 결과 저장
  conveyor_dashboard/.venv/bin/python evaluate_model.py --score eval_results/<시각>/frames.csv
      저장된 CSV로 지표만 다시 계산 (PC에서도 가능, 카메라 불필요)
"""

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

# convey_vision.py와 같은 값 (바꾸면 두 파일을 함께 바꿔야 평가가 의미 있음)
MODEL_FILENAME = 'face_detection_yunet_2023mar.onnx'
SCORE_THRESHOLD = 0.8
NMS_THRESHOLD = 0.3
DISTANCE_CONSTANT = 13000
DANGER_DISTANCE = 50
WARNING_DISTANCE = 200

# 측정 위치(cm). None은 빈 화면. 기준선(50, 200cm) 바로 위는 줄자 오차로 정답이 애매해서 피함
POSITIONS = [None, 300, 250, 150, 100, 70, 40, 30]
FRAMES_PER_POSITION = 60
COUNTDOWN_SECONDS = 5

STATUSES = ['green', 'yellow', 'red']
CSV_FIELDS = ['position', 'true_distance_cm', 'true_status', 'face_count',
              'pred_distance_cm', 'pred_status', 'detect_ms']


def get_distance(box_height):
    """convey_vision.py의 get_distance와 같습니다. 박스가 클수록 가깝습니다."""
    if box_height <= 0:
        return 0
    return DISTANCE_CONSTANT / box_height


def get_status(distance):
    """convey_vision.py의 get_status와 같습니다. 얼굴이 없으면 distance는 None입니다."""
    if distance is None or distance > WARNING_DISTANCE:
        return 'green'
    if distance > DANGER_DISTANCE:
        return 'yellow'
    return 'red'


def judge(detector, frame):
    """한 프레임을 판단합니다: (얼굴 수, 가장 가까운 얼굴 거리, 상태, 검출 시간 ms)."""
    start = time.perf_counter()
    _, faces = detector.detect(frame)
    detect_ms = (time.perf_counter() - start) * 1000
    distances = [] if faces is None else [get_distance(int(face[3])) for face in faces]   # face[3] = 박스 높이
    distance = min(distances) if distances else None
    return len(distances), distance, get_status(distance), detect_ms


def capture(output_dir, frames_per_position):
    """위치마다 Space를 누르면 카운트다운 뒤 프레임을 판단해 CSV에 저장합니다."""
    import cv2

    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise IOError('카메라를 열 수 없습니다. convey_vision.py가 켜져 있으면 먼저 끄세요.')
    width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
    detector = cv2.FaceDetectorYN.create(model=MODEL_FILENAME, config='', input_size=(width, height),
                                         score_threshold=SCORE_THRESHOLD, nms_threshold=NMS_THRESHOLD)
    csv_path = output_dir / 'frames.csv'
    loop_seconds, loop_frames = 0.0, 0
    try:
        with csv_path.open('w', newline='', encoding='utf-8-sig') as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for true_distance in POSITIONS:
                label = 'EMPTY (no person)' if true_distance is None else f'{true_distance} cm'
                true_status = get_status(true_distance)
                started_at = None   # Space를 누른 시각
                saved = 0
                loop_start = None
                while saved < frames_per_position:
                    ok, frame = camera.read()
                    if not ok:
                        raise IOError('프레임을 읽을 수 없습니다.')
                    face_count, distance, status, detect_ms = judge(detector, frame)
                    waiting = started_at is None or time.time() - started_at < COUNTDOWN_SECONDS
                    if not waiting:
                        if loop_start is None:
                            loop_start = time.perf_counter()
                        writer.writerow({
                            'position': label, 'true_distance_cm': '' if true_distance is None else true_distance,
                            'true_status': true_status, 'face_count': face_count,
                            'pred_distance_cm': '' if distance is None else round(distance, 1),
                            'pred_status': status, 'detect_ms': round(detect_ms, 2)})
                        saved += 1
                    if started_at is None:
                        guide = 'SPACE: start  n: skip  q: quit'
                    elif waiting:
                        guide = f'starting in {COUNTDOWN_SECONDS - int(time.time() - started_at)} s'
                    else:
                        guide = f'recording {saved}/{frames_per_position}'
                    cv2.putText(frame, f'TARGET: {label}  (answer: {true_status})', (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                    cv2.putText(frame, f'now: {status}  faces: {face_count}  {guide}', (10, 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                    cv2.imshow('Model evaluation', frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        return csv_path, loop_frames, loop_seconds
                    if key == ord('n') and started_at is None:
                        break
                    if key == 32 and started_at is None:
                        started_at = time.time()
                if saved:
                    loop_seconds += time.perf_counter() - loop_start
                    loop_frames += saved
                    print(f'{label}: {saved}장 저장', flush=True)
    finally:
        camera.release()
        cv2.destroyAllWindows()
    return csv_path, loop_frames, loop_seconds


def score(csv_path, loop_frames=0, loop_seconds=0.0):
    """CSV로 혼동행렬, Accuracy, 클래스별 Precision/Recall/F1, FPS를 계산해 Markdown으로 저장합니다."""
    with Path(csv_path).open(encoding='utf-8-sig') as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError('평가할 프레임이 없습니다.')

    matrix = {t: {p: 0 for p in STATUSES} for t in STATUSES}
    for row in rows:
        matrix[row['true_status']][row['pred_status']] += 1
    total = len(rows)
    accuracy = sum(matrix[s][s] for s in STATUSES) / total

    per_class = {}
    for s in STATUSES:
        tp = matrix[s][s]
        predicted = sum(matrix[t][s] for t in STATUSES)
        actual = sum(matrix[s].values())
        precision = tp / predicted if predicted else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[s] = (precision, recall, f1, actual)
    evaluated = [s for s in STATUSES if per_class[s][3]]
    macro_f1 = sum(per_class[s][2] for s in evaluated) / len(evaluated)

    detect_ms = [float(row['detect_ms']) for row in rows]
    mean_ms = sum(detect_ms) / len(detect_ms)
    errors = [abs(float(r['pred_distance_cm']) - float(r['true_distance_cm']))
              for r in rows if r['true_distance_cm'] and r['pred_distance_cm']]

    names = {'green': '정상(green)', 'yellow': '경고(yellow)', 'red': '위험(red)'}
    lines = [f'# 모델 성능 평가 결과 ({datetime.now():%Y-%m-%d %H:%M})', '',
             f'- 데이터: `{csv_path}` · 프레임 {total}장',
             f'- **Accuracy: {accuracy * 100:.1f}%** ({sum(matrix[s][s] for s in STATUSES)}/{total})',
             f'- **Macro F1: {macro_f1:.3f}** (정답 프레임이 있는 클래스 {len(evaluated)}개 평균)',
             f'- **위험(red) Recall: {per_class["red"][1] * 100:.1f}%** (실제 위험인데 위험으로 판단한 비율. 안전상 가장 중요)',
             f'- **검출 속도: 평균 {mean_ms:.1f} ms/프레임 → 모델 {1000 / mean_ms:.1f} FPS**'
             + (f' · 카메라 포함 {loop_frames / loop_seconds:.1f} FPS' if loop_seconds else ''),
             f'- 거리 오차(MAE): {sum(errors) / len(errors):.1f} cm (얼굴이 검출된 {len(errors)}장 기준)' if errors else '- 거리 오차(MAE): 얼굴이 검출된 프레임 없음',
             '', '## 혼동행렬 (행: 정답, 열: 판단)', '',
             '| 정답 \\ 판단 | ' + ' | '.join(names[s] for s in STATUSES) + ' |',
             '|---|' + '---|' * len(STATUSES)]
    lines += [f'| {names[t]} | ' + ' | '.join(str(matrix[t][p]) for p in STATUSES) + ' |' for t in STATUSES]
    lines += ['', '## 클래스별 지표', '', '| 상태 | Precision | Recall | F1 | 정답 프레임 |', '|---|---|---|---|---|']
    lines += [f'| {names[s]} | {p:.3f} | {r:.3f} | {f:.3f} | {n} |' for s, (p, r, f, n) in per_class.items()]
    lines += ['', '## 위치별 결과', '', '| 위치 | 정답 | 프레임 | 정답 일치 | 얼굴 검출 |', '|---|---|---|---|---|']
    for position in dict.fromkeys(row['position'] for row in rows):
        group = [row for row in rows if row['position'] == position]
        hit = sum(row['true_status'] == row['pred_status'] for row in group)
        found = sum(int(row['face_count']) > 0 for row in group)
        lines.append(f'| {position} | {group[0]["true_status"]} | {len(group)} | {hit / len(group) * 100:.0f}% | {found / len(group) * 100:.0f}% |')

    report = '\n'.join(lines) + '\n'
    report_path = Path(csv_path).with_name('metrics.md')
    report_path.write_text(report, encoding='utf-8')
    print(report)
    print(f'저장: {report_path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='YuNet 상태 판단 성능 평가 (Accuracy, F1, FPS)')
    parser.add_argument('--score', help='저장된 frames.csv로 지표만 다시 계산')
    parser.add_argument('--frames', type=int, default=FRAMES_PER_POSITION, help='위치마다 저장할 프레임 수')
    args = parser.parse_args()
    if args.score:
        score(args.score)
    else:
        output_dir = Path('eval_results') / datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path, frames, seconds = capture(output_dir, args.frames)
        score(csv_path, frames, seconds)
