"""evaluate_model.py를 브라우저에서 진행하는 버전 (Jetson에 모니터가 없을 때).

측정 위치, 저장 형식(frames.csv), 지표 계산(metrics.md)은 evaluate_model.py와 같습니다.
키보드 대신 웹 페이지의 [시작] [건너뛰기] [끝내기] 버튼으로 진행합니다.

사용법 (convey_vision.py는 꺼 둔 상태로. 카메라는 한 프로그램만 열 수 있음)
  cd ~/Dokkab && conveyor_dashboard/.venv/bin/python evaluate_web.py
  노트북 브라우저에서 http://<Jetson IP>:5090 접속
"""

import csv
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify

from evaluate_model import (COUNTDOWN_SECONDS, CSV_FIELDS, FRAMES_PER_POSITION, MODEL_FILENAME,
                            NMS_THRESHOLD, POSITIONS, SCORE_THRESHOLD, get_status, judge, score)

PORT = 5090

app = Flask(__name__)
lock = threading.Lock()
state = {'target': '', 'answer': '', 'now': '', 'faces': 0, 'guide': '준비 중', 'step': '',
         'done': False, 'report': ''}
command = {'value': None}
latest_jpeg = {'data': None}


def take_command():
    with lock:
        value, command['value'] = command['value'], None
    return value


def run_capture(output_dir):
    """evaluate_model.capture와 같은 순서로 측정합니다. 키 입력 대신 웹 버튼 명령을 받습니다."""
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        state.update(guide='카메라를 열 수 없습니다. convey_vision.py가 켜져 있으면 먼저 끄세요.', done=True)
        return
    width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
    detector = cv2.FaceDetectorYN.create(model=MODEL_FILENAME, config='', input_size=(width, height),
                                         score_threshold=SCORE_THRESHOLD, nms_threshold=NMS_THRESHOLD)
    csv_path = output_dir / 'frames.csv'
    loop_seconds, loop_frames = 0.0, 0
    quit_requested = False
    try:
        with csv_path.open('w', newline='', encoding='utf-8-sig') as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for index, true_distance in enumerate(POSITIONS, 1):
                label = 'EMPTY (no person)' if true_distance is None else f'{true_distance} cm'
                true_status = get_status(true_distance)
                started_at = None
                saved = 0
                loop_start = None
                while saved < FRAMES_PER_POSITION:
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
                        guide = '위치에 서서 [시작]을 누르세요'
                    elif waiting:
                        guide = f'{COUNTDOWN_SECONDS - int(time.time() - started_at)}초 뒤 촬영 시작'
                    else:
                        guide = f'촬영 중 {saved}/{FRAMES_PER_POSITION}'
                    state.update(target='빈 화면 (카메라 앞에서 비켜 주세요)' if true_distance is None else f'{true_distance} cm',
                                 answer=true_status, now=status, faces=face_count, guide=guide,
                                 step=f'{index}/{len(POSITIONS)}')
                    ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        latest_jpeg['data'] = jpeg.tobytes()
                    value = take_command()
                    if value == 'quit':
                        quit_requested = True
                        break
                    if value == 'skip' and started_at is None:
                        break
                    if value == 'start' and started_at is None:
                        started_at = time.time()
                if saved:
                    loop_seconds += time.perf_counter() - loop_start
                    loop_frames += saved
                    print(f'{label}: {saved}장 저장', flush=True)
                if quit_requested:
                    break
    finally:
        camera.release()
    try:
        score(csv_path, loop_frames, loop_seconds)
        report = (output_dir / 'metrics.md').read_text(encoding='utf-8')
    except ValueError as error:
        report = f'지표를 계산하지 못했습니다: {error}'
    state.update(guide='측정이 끝났습니다. 아래 결과를 확인하세요.', report=report, done=True)


PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>모델 평가</title>
<style>
body{font-family:system-ui,sans-serif;margin:16px;background:#f6f7f9;color:#1d2330}
main{max-width:720px;margin:auto}img{width:100%;border-radius:8px;background:#000}
.info{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:12px 0;font-size:18px}
.big{font-size:26px;font-weight:700}button{font-size:18px;padding:10px 18px;margin-right:8px;border-radius:8px;border:1px solid #888;cursor:pointer}
#start{background:#1f6feb;color:#fff;border:0}pre{white-space:pre-wrap;background:#fff;padding:12px;border-radius:8px}
</style></head><body><main>
<h1>모델 평가 (Accuracy · F1)</h1>
<img src="/video" alt="카메라 화면">
<div class="info">
<span>순서</span><b id="step"></b>
<span>설 위치</span><span class="big" id="target"></span>
<span>정답 상태</span><b id="answer"></b>
<span>지금 판단</span><b id="now"></b>
<span>얼굴 수</span><b id="faces"></b>
<span>안내</span><b id="guide"></b>
</div>
<button id="start" onclick="send('start')">시작</button>
<button onclick="send('skip')">건너뛰기</button>
<button onclick="if(confirm('측정을 끝내고 지금까지 찍은 것으로 결과를 계산할까요?'))send('quit')">끝내기</button>
<pre id="report"></pre>
</main><script>
function send(c){fetch('/command/'+c,{method:'POST'})}
async function poll(){
  try{const s=await (await fetch('/state')).json();
    for(const k of ['step','target','answer','now','faces','guide'])document.getElementById(k).textContent=s[k];
    if(s.report)document.getElementById('report').textContent=s.report;
    if(s.done)return;}catch(e){}
  setTimeout(poll,500)}
poll();
</script></body></html>"""


@app.get('/')
def index():
    return PAGE


@app.get('/state')
def get_state():
    return jsonify(state)


@app.post('/command/<value>')
def post_command(value):
    if value in ('start', 'skip', 'quit'):
        with lock:
            command['value'] = value
    return jsonify(ok=True)


@app.get('/video')
def video():
    def stream():
        while not state['done']:
            if latest_jpeg['data']:
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + latest_jpeg['data'] + b'\r\n'
            time.sleep(0.07)
    return Response(stream(), mimetype='multipart/x-mixed-replace; boundary=frame')


if __name__ == '__main__':
    output_dir = Path('eval_results') / datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=run_capture, args=(output_dir,), daemon=True).start()
    print(f'브라우저에서 http://<Jetson IP>:{PORT} 접속. 결과 폴더: {output_dir}', flush=True)
    app.run(host='0.0.0.0', port=PORT, threaded=True)
