"""YuNet 최적화 전후 벤치마크: OpenCV(CPU, 현재 convey_vision.py 방식) vs TensorRT FP32 / FP16.

같은 카메라 프레임을 세 방식에 똑같이 넣어 지연(전처리+추론+후처리), FPS, 메모리, CPU 사용,
그리고 현재 방식과의 결과 일치율(얼굴·상태·거리)을 잽니다. GPIO(LED·벨트)는 건드리지 않습니다.
정답 거리가 없으므로 '정확도'는 현재 방식(OpenCV)을 기준으로 한 일치율입니다.

사용법 (Jetson, convey_vision.py를 끈 상태로. 카메라는 한 프로그램만 열 수 있음)
  cd ~/Dokkab/optimization
  python3 benchmark_trt.py capture            # 카메라 프레임 300장을 frames.npy로 저장
  ../conveyor_dashboard/.venv/bin/python benchmark_trt.py run opencv_cpu
  python3 benchmark_trt.py run trt_fp32        # TensorRT는 시스템 python3에 설치되어 있음
  python3 benchmark_trt.py run trt_fp16
  python3 benchmark_trt.py report              # benchmark_result.md 작성
"""

import ctypes
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, '..', 'face_detection_yunet_2023mar.onnx')
ENGINES = {'trt_fp32': 'yunet_fp32.engine', 'trt_fp16': 'yunet_fp16.engine'}
FRAMES_FILE = os.path.join(HERE, 'frames.npy')

# convey_vision.py / evaluate_model.py와 같은 값
SCORE_THRESHOLD = 0.8
NMS_THRESHOLD = 0.3
TOP_K = 5000
DISTANCE_CONSTANT = 13000
DANGER_DISTANCE = 50
WARNING_DISTANCE = 200

NUM_FRAMES = 300
WARMUP = 30
ENGINE_SIZE = 640   # ONNX 입력이 1x3x640x640 고정이라 640x480 프레임 아래쪽을 0으로 채움


def get_status(distance):
    if distance is None or distance > WARNING_DISTANCE:
        return 'green'
    if distance > DANGER_DISTANCE:
        return 'yellow'
    return 'red'


def mem_available_mb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) / 1024


def peak_rss_mb():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('VmHWM:'):
                return int(line.split()[1]) / 1024


def capture():
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise IOError('카메라를 열 수 없습니다.')
    for _ in range(WARMUP):
        camera.read()
    frames = []
    while len(frames) < NUM_FRAMES:
        ok, frame = camera.read()
        if ok:
            frames.append(frame)
    camera.release()
    np.save(FRAMES_FILE, np.stack(frames))
    print(f'{len(frames)}장 저장: {frames[0].shape}')


class OpenCvDetector:
    """convey_vision.py와 같은 생성 방식 (CPU)."""

    def __init__(self, width, height):
        self.detector = cv2.FaceDetectorYN.create(
            model=MODEL, config='', input_size=(width, height),
            score_threshold=SCORE_THRESHOLD, nms_threshold=NMS_THRESHOLD)

    def detect(self, frame):
        _, faces = self.detector.detect(frame)
        return np.zeros((0, 4)) if faces is None else faces[:, :4]


class TrtDetector:
    """TensorRT 엔진 + OpenCV FaceDetectorYN과 같은 디코딩·NMS."""

    def __init__(self, engine_path):
        import tensorrt as trt
        self.cuda = ctypes.CDLL('libcudart.so.12')
        logger = trt.Logger(trt.Logger.WARNING)
        with open(engine_path, 'rb') as f:
            self.engine = trt.Runtime(logger).deserialize_cuda_engine(f.read())
        self.context = self.engine.create_execution_context()
        self.stream = ctypes.c_void_p()
        self._check(self.cuda.cudaStreamCreate(ctypes.byref(self.stream)))
        self.host, self.device = {}, {}
        for i in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(i)
            shape = tuple(self.engine.get_tensor_shape(name))
            self.host[name] = np.zeros(shape, dtype=np.float32)
            pointer = ctypes.c_void_p()
            self._check(self.cuda.cudaMalloc(ctypes.byref(pointer), ctypes.c_size_t(self.host[name].nbytes)))
            self.device[name] = pointer
            self.context.set_tensor_address(name, pointer.value)
        self.input_name = self.engine.get_tensor_name(0)
        self.device_memory_mb = self.engine.device_memory_size_v2 / 1024 / 1024

    @staticmethod
    def _check(code):
        if code != 0:
            raise RuntimeError(f'CUDA 오류 {code}')

    def _copy(self, dst, src, nbytes, kind):
        self._check(self.cuda.cudaMemcpyAsync(dst, src, ctypes.c_size_t(nbytes), kind, self.stream))

    def detect(self, frame):
        height, width = frame.shape[:2]
        padded = np.zeros((ENGINE_SIZE, ENGINE_SIZE, 3), dtype=np.float32)
        padded[:height, :width] = frame
        self.host[self.input_name][0] = padded.transpose(2, 0, 1)
        blob = self.host[self.input_name]
        self._copy(self.device[self.input_name], blob.ctypes.data_as(ctypes.c_void_p), blob.nbytes, 1)
        self.context.execute_async_v3(self.stream.value)
        for name, array in self.host.items():
            if name != self.input_name:
                self._copy(array.ctypes.data_as(ctypes.c_void_p), self.device[name], array.nbytes, 2)
        self._check(self.cuda.cudaStreamSynchronize(self.stream))
        return self._decode()

    def _decode(self):
        boxes, scores = [], []
        for stride in (8, 16, 32):
            cols = ENGINE_SIZE // stride
            cls = np.clip(self.host[f'cls_{stride}'][0, :, 0], 0, 1)
            obj = np.clip(self.host[f'obj_{stride}'][0, :, 0], 0, 1)
            score = np.sqrt(cls * obj)
            keep = np.where(score >= SCORE_THRESHOLD)[0]
            if keep.size == 0:
                continue
            bbox = self.host[f'bbox_{stride}'][0, keep]
            col, row = keep % cols, keep // cols
            cx = (col + bbox[:, 0]) * stride
            cy = (row + bbox[:, 1]) * stride
            w = np.exp(bbox[:, 2]) * stride
            h = np.exp(bbox[:, 3]) * stride
            boxes.append(np.stack([cx - w / 2, cy - h / 2, w, h], axis=1))
            scores.append(score[keep])
        if not boxes:
            return np.zeros((0, 4))
        boxes, scores = np.concatenate(boxes), np.concatenate(scores)
        keep = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), SCORE_THRESHOLD, NMS_THRESHOLD, top_k=TOP_K)
        return boxes[np.array(keep).reshape(-1)]


def run(method):
    frames = np.load(FRAMES_FILE)
    mem_before = mem_available_mb()
    if method == 'opencv_cpu':
        detector = OpenCvDetector(frames.shape[2], frames.shape[1])
    else:
        detector = TrtDetector(os.path.join(HERE, ENGINES[method]))
    for frame in frames[:WARMUP]:
        detector.detect(frame)
    times, results = [], []
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    for frame in frames:
        start = time.perf_counter()
        boxes = detector.detect(frame)
        times.append((time.perf_counter() - start) * 1000)
        results.append([[float(v) for v in box] for box in boxes])
    cpu_seconds, wall_seconds = time.process_time() - cpu_start, time.perf_counter() - wall_start
    times = np.array(times)
    summary = {
        'method': method,
        'python': sys.executable,
        'opencv': cv2.__version__,
        'frames': len(frames),
        'mean_ms': float(times.mean()),
        'p95_ms': float(np.percentile(times, 95)),
        'fps': float(1000 / times.mean()),
        'cpu_percent': float(cpu_seconds / wall_seconds * 100),
        'peak_rss_mb': peak_rss_mb(),
        'system_mem_used_mb': mem_before - mem_available_mb(),
        'trt_device_memory_mb': getattr(detector, 'device_memory_mb', None),
        'boxes': results,
    }
    with open(os.path.join(HERE, f'result_{method}.json'), 'w') as f:
        json.dump(summary, f)
    print({k: v for k, v in summary.items() if k != 'boxes'})


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def frame_status(boxes):
    distances = [DISTANCE_CONSTANT / int(box[3]) for box in boxes if int(box[3]) > 0]
    return get_status(min(distances) if distances else None), (min(distances) if distances else None)


def compare(reference, other):
    ref_faces = matched = extra = same_status = 0
    distance_diffs = []
    for ref_boxes, boxes in zip(reference['boxes'], other['boxes']):
        used = set()
        for ref_box in ref_boxes:
            best = max(((iou(ref_box, box), j) for j, box in enumerate(boxes) if j not in used), default=(0, None))
            if best[0] >= 0.5:
                matched += 1
                used.add(best[1])
        ref_faces += len(ref_boxes)
        extra += len(boxes) - len(used)
        (ref_status, ref_distance), (status, distance) = frame_status(ref_boxes), frame_status(boxes)
        same_status += ref_status == status
        if ref_distance is not None and distance is not None:
            distance_diffs.append(abs(ref_distance - distance))
    frames = len(reference['boxes'])
    return {
        'ref_faces': ref_faces, 'matched': matched, 'extra': extra,
        'status_agree': same_status / frames * 100,
        'distance_mae': float(np.mean(distance_diffs)) if distance_diffs else None,
    }


def report():
    methods = ['opencv_cpu', 'trt_fp32', 'trt_fp16']
    data = {m: json.load(open(os.path.join(HERE, f'result_{m}.json'))) for m in methods}
    base = data['opencv_cpu']
    face_counts = [len(b) for b in base['boxes']]
    statuses = [frame_status(b)[0] for b in base['boxes']]
    lines = [
        '| 항목 | OpenCV CPU (현재) | TensorRT FP32 | TensorRT FP16 |',
        '|---|---|---|---|',
        '| 평균 지연 (ms/프레임) | ' + ' | '.join(f"{data[m]['mean_ms']:.2f}" for m in methods) + ' |',
        '| p95 지연 (ms) | ' + ' | '.join(f"{data[m]['p95_ms']:.2f}" for m in methods) + ' |',
        '| FPS (검출만) | ' + ' | '.join(f"{data[m]['fps']:.1f}" for m in methods) + ' |',
        '| 속도 배수 | 1.00 | ' + ' | '.join(f"{data[m]['fps'] / base['fps']:.2f}" for m in methods[1:]) + ' |',
        '| CPU 사용 (코어 1개=100%) | ' + ' | '.join(f"{data[m]['cpu_percent']:.0f}%" for m in methods) + ' |',
        '| 프로세스 최대 RSS (MB) | ' + ' | '.join(f"{data[m]['peak_rss_mb']:.0f}" for m in methods) + ' |',
        '| 시스템 메모리 증가 (MB) | ' + ' | '.join(f"{data[m]['system_mem_used_mb']:.0f}" for m in methods) + ' |',
        '| TensorRT 작업 메모리 (MB) | - | ' + ' | '.join(f"{data[m]['trt_device_memory_mb']:.1f}" for m in methods[1:]) + ' |',
    ]
    agree = {m: compare(base, data[m]) for m in methods[1:]}
    lines += [
        '| 얼굴 일치 (IoU≥0.5) | 기준 | ' + ' | '.join(
            f"{agree[m]['matched']}/{agree[m]['ref_faces']} ({agree[m]['matched'] / max(1, agree[m]['ref_faces']) * 100:.1f}%)" for m in methods[1:]) + ' |',
        '| 기준에 없는 추가 얼굴 | - | ' + ' | '.join(str(agree[m]['extra']) for m in methods[1:]) + ' |',
        '| 상태(초록·노랑·빨강) 일치 | 기준 | ' + ' | '.join(f"{agree[m]['status_agree']:.1f}%" for m in methods[1:]) + ' |',
        '| 거리 차이 평균 (cm) | 기준 | ' + ' | '.join(
            '-' if agree[m]['distance_mae'] is None else f"{agree[m]['distance_mae']:.2f}" for m in methods[1:]) + ' |',
    ]
    info = (f"- 프레임 {base['frames']}장 (워밍업 {WARMUP}장 별도), OpenCV {base['opencv']}\n"
            f"- 기준 프레임의 얼굴 수: 0개 {face_counts.count(0)}장, 1개 {face_counts.count(1)}장, "
            f"2개 이상 {sum(c >= 2 for c in face_counts)}장 / 상태: 초록 {statuses.count('green')}, "
            f"노랑 {statuses.count('yellow')}, 빨강 {statuses.count('red')}\n")
    text = '# 벤치마크 결과 (자동 생성)\n\n' + info + '\n' + '\n'.join(lines) + '\n'
    with open(os.path.join(HERE, 'benchmark_result.md'), 'w') as f:
        f.write(text)
    print(text)


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command == 'capture':
        capture()
    elif command == 'run' and len(sys.argv) > 2 and sys.argv[2] in ('opencv_cpu', *ENGINES):
        run(sys.argv[2])
    elif command == 'report':
        report()
    else:
        print(__doc__)
