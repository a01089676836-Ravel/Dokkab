"""RYG_face_distance_detection.py의 1차 완성본을 HTML / Aiven에 연결합니다.

원본 기준: 카메라 0, YuNet score 0.8 / NMS 0.3,
추정거리(cm)=13000/얼굴 상자 높이, 위험 <=50cm, 경고 <=200cm.
얼굴 검출 모델과 근사식은 유지하고, 여러 얼굴 중 가장 가까운 값을 사용합니다.
"""
from __future__ import annotations

import importlib
import logging
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timezone

from aiven_logs import JetsonEventLogger, KST, ROOT

LOG = logging.getLogger(__name__)
DANGER_CM = 50.0
WARNING_CM = 200.0
DISTANCE_SCALE = 13000.0


class CameraError(RuntimeError):
    pass


def distance_face(box_h):
    # 높이가 잘못된 박스는 거리를 0으로 취급하지 않고 검출 목록에서 제외합니다.
    return DISTANCE_SCALE / box_h if box_h > 0 else None


def classify_distance(distance_cm):
    if distance_cm <= DANGER_CM:
        return 'DANGER'
    if distance_cm <= WARNING_CM:
        return 'WARNING'
    return 'NORMAL'


class FrameHub:
    """같은 카메라를 중복 열지 않고, 주석이 그려진 최신 JPEG를 HTML로 보냅니다."""
    def __init__(self):
        self.condition = threading.Condition()
        self.jpeg = None
        self.sequence = 0
        self.last_frame = 0.0
        self.event = None
        self.closed = False

    def publish(self, jpeg, event):
        with self.condition:
            self.jpeg = jpeg
            self.event = event
            self.sequence += 1
            self.last_frame = time.monotonic()
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {'enabled': True, 'camera_online': bool(self.jpeg) and not self.closed
                    and time.monotonic() - self.last_frame < 3,
                    'event': dict(self.event) if self.event else None}

    def multipart(self):
        sequence = -1
        while True:
            with self.condition:
                self.condition.wait_for(lambda: (self.jpeg is not None and self.sequence != sequence)
                                        or self.closed, timeout=3)
                if self.closed:
                    return
                if not self.jpeg:
                    continue
                if time.monotonic() - self.last_frame > 3:
                    return
                if self.sequence == sequence:
                    continue
                sequence, frame = self.sequence, self.jpeg
            yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'


class SafetyController:
    """분석 결과 -> FSM 상태 -> 하드웨어 어댑터 -> 이벤트 기록 순서입니다.

    하드웨어 어댑터를 지정하지 않으면 상태 표시/DB 기록만 수행합니다.
    이 경우 작동정지 완료로 기록하지 않습니다.
    """
    def __init__(self, logger, hardware_module=None):
        self.logger = logger
        self.hardware = importlib.import_module(hardware_module) if hardware_module else None
        self.previous = None
        self.previous_reason = None
        self.stop_applied = None
        self._lock = threading.RLock()
        self._closed = False
        self._last_event = None

    def update(self, state, reason, *, occurred_at=None):
        with self._lock:
            if self._closed:
                return self._last_event
            return self._update(state, reason, occurred_at=occurred_at)

    def _update(self, state, reason, *, occurred_at=None):
        moment = occurred_at or datetime.now(timezone.utc)
        changed = self.previous != state
        if changed and self.hardware:
            try:
                # 모듈의 apply_state는 LED/L9110S 모터 명령을 적용한 뒤
                # 정지 적용=True, 구동 적용=False, 미확인=None을 반환합니다.
                self.stop_applied = self.hardware.apply_state(state)
                if self.stop_applied is not None and not isinstance(self.stop_applied, bool):
                    raise ValueError('하드웨어 어댑터 반환값이 올바르지 않습니다.')
            except Exception:
                state, reason, self.stop_applied = 'ERROR', 'LED/L9110S 모터 명령 적용 실패', None
                LOG.exception('하드웨어 명령 적용 실패')
                changed = self.previous != state
        event_id = None
        if changed or (state == 'ERROR' and reason != self.previous_reason):
            # 실제 제어 처리를 먼저 마친 후 로그를 저장합니다.
            try:
                event_id = self.logger.record(
                    state, reason, occurred_at=moment,
                    equipment_stopped=self.stop_applied,
                    stop_reason=reason if self.stop_applied is True else None,
                )
            except (OSError, ValueError, sqlite3.Error):
                LOG.exception('로컬 이벤트 기록 실패')
        self.previous = state
        self.previous_reason = reason
        local = moment.astimezone(KST)
        self._last_event = dict(event_id=event_id, severity=state, event_description=reason,
                    equipment_stopped=self.stop_applied,
                    stop_reason=reason if self.stop_applied is True else None,
                    occurred_at_utc=moment.isoformat(), event_date=local.strftime('%Y-%m-%d'),
                    event_time=local.strftime('%H:%M:%S'))
        return dict(self._last_event)

    def close(self):
        """종료 처리가 시작된 뒤 모터를 다시 구동하지 않습니다."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if not self.hardware:
                return
            state, reason, stopped = 'NORMAL', '프로그램 종료로 벨트 모터 출력을 해제했습니다.', None
            try:
                cleanup = getattr(self.hardware, 'cleanup', None)
                stopped = cleanup() if callable(cleanup) else self.hardware.apply_state('ERROR')
                if stopped is not True:
                    state, reason = 'ERROR', '프로그램 종료 · 모터 정지 출력 적용 미확인'
            except Exception:
                state, reason = 'ERROR', '프로그램 종료 중 L9110S 출력 해제 실패'
                LOG.exception('하드웨어 종료 처리 실패')
            try:
                self.logger.record(state, reason, equipment_stopped=stopped,
                                   stop_reason='프로그램 종료' if stopped is True else None)
            except (OSError, ValueError, sqlite3.Error):
                LOG.exception('프로그램 종료 이벤트 저장 실패')


class VisionRunner:
    def __init__(self, camera=0, preview=False, hardware_module=None):
        self.camera = camera
        self.preview = preview
        self.hub = FrameHub()
        self.logger = JetsonEventLogger()
        self.controller = SafetyController(self.logger, hardware_module)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name='yunet-vision', daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        try:
            while not self.stop_event.is_set():
                retry_camera = self._capture()
                if not retry_camera or self.stop_event.wait(5):
                    break
        finally:
            with self.hub.condition:
                self.hub.closed = True
                self.hub.condition.notify_all()

    def _capture(self):
        cap = None
        cv2 = None
        try:
            import cv2
            cap = cv2.VideoCapture(self.camera)
            if not cap.isOpened():
                raise CameraError(f'카메라 {self.camera}번을 열 수 없습니다. 연결 상태를 확인하세요.')
            model = ROOT / 'face_detection_yunet_2023mar.onnx'
            if not model.exists() or model.stat().st_size < 10000:
                url = 'https://huggingface.co/opencv/face_detection_yunet/resolve/main/' + model.name
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                temporary = model.with_suffix('.download')
                with urllib.request.urlopen(req, timeout=30) as response, temporary.open('wb') as out:
                    out.write(response.read())
                if temporary.stat().st_size < 10000:
                    raise ValueError('모델 다운로드 파일이 너무 작습니다.')
                os.replace(temporary, model)
            detector = cv2.FaceDetectorYN.create(str(model), '', (320, 320),
                                                score_threshold=0.8, nms_threshold=0.3)
            colors = {'NORMAL': (0, 255, 0), 'WARNING': (0, 255, 255), 'DANGER': (0, 0, 255)}
            while not self.stop_event.is_set():
                ret, frame = cap.read()
                moment = datetime.now(timezone.utc)
                if not ret or frame is None:
                    raise CameraError('카메라 프레임 수신 실패 · 카메라 연결이 끊겼습니다.')
                h, w = frame.shape[:2]
                detector.setInputSize((w, h))
                _, faces = detector.detect(frame)
                distances = []
                if faces is not None:
                    for face in faces:
                        x, y, box_w, box_h = map(int, face[:4])
                        distance = distance_face(box_h)
                        if distance is None:
                            continue
                        distances.append(distance)
                        state = classify_distance(distance)
                        color = colors[state]
                        cv2.rectangle(frame, (x, y), (x+box_w, y+box_h), color, 2)
                        for lx, ly in face[4:14].reshape((5, 2)):
                            cv2.circle(frame, (int(lx), int(ly)), 3, (0, 0, 255), -1)
                        cv2.putText(frame, f'{box_w}x{box_h}, {distance:.1f}cm',
                                    (x, max(20, y-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                if distances:
                    nearest = min(distances)
                    state = classify_distance(nearest)
                    reason = f'얼굴 {len(distances)}명 검출 · 가장 가까운 추정거리 {nearest:.1f}cm'
                    if state == 'DANGER':
                        reason += ' · 위험 접근(50cm 이하)'
                    elif state == 'WARNING':
                        reason += ' · 접근 경고(50cm 초과 200cm 이하)'
                    else:
                        reason += ' · 정상 범위(200cm 초과)'
                else:
                    # 사용자 결정: 얼굴이 보이지 않으면 작업자가 없는 정상 상태로 보고 벨트를 구동합니다.
                    state, reason = 'NORMAL', '얼굴 미검출 · 작업자 없음, 정상 작동'
                event = self.controller.update(state, reason, occurred_at=moment)
                if event is None or self.stop_event.is_set():
                    break
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    self.hub.publish(encoded.tobytes(), event)
                if self.preview:
                    cv2.imshow('Face Detection(q = quit)', frame)
                    key = cv2.waitKey(25) & 0xff
                    if key == ord('q'):
                        event = self.controller.update('ERROR', '비전 화면에서 q를 눌러 영상 처리를 종료했습니다.')
                        with self.hub.condition:
                            self.hub.event = event
                        break
                    if key == ord('s'):
                        snapshot = ROOT / f'snapshot_{time.time_ns()}.png'
                        cv2.imwrite(str(snapshot), frame)
        except Exception as exc:
            # 비밀번호 등을 출력하지 않습니다. 실제 예외 유형을 오류 이벤트로 기록합니다.
            detail = str(exc) if isinstance(exc, CameraError) else type(exc).__name__
            event = self.controller.update('ERROR', f'비전 처리 중단 · {detail}')
            if event:
                with self.hub.condition:
                    self.hub.event = event
            LOG.error('비전 처리 중단: %s', type(exc).__name__)
            # USB 카메라가 나중에 연결되면 자동으로 다시 엽니다.
            # 같은 오류 상태는 반복해서 DB에 쓰지 않습니다.
            return isinstance(exc, CameraError)
        finally:
            if cap:
                cap.release()
            if self.preview and cv2:
                cv2.destroyAllWindows()

    def close(self):
        self.stop_event.set()
        self.controller.close()
        self.thread.join(timeout=3)
        self.logger.close()
