"""Aiven 로그를 읽어 왼쪽 카메라 / 오른쪽 최신 로그 HTML에 전달합니다."""

import argparse
import atexit
import os
import signal
import threading
from datetime import datetime, timezone

import pymysql
from flask import Flask, Response, jsonify, request, send_from_directory

from aiven_logs import ConfigurationError, ROOT, read_events

app = Flask(__name__, static_folder=None)
vision = None
db_enabled = True  # HTML의 DB 연동 버튼 상태. 서버를 다시 켜면 켜짐으로 시작합니다.


@app.get("/")
def dashboard():
    return send_from_directory(ROOT, "index.html")


@app.get("/dashboard.js")
def javascript():
    return send_from_directory(ROOT, "dashboard.js")


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/api/config")
def config():
    # DB 비밀번호 / 접속 정보는 브라우저로 보내지 않습니다.
    return jsonify(camera_stream_url='/video_feed' if vision else os.getenv("CAMERA_STREAM_URL", ""),
                   vision_enabled=vision is not None,
                   device_id=os.getenv("DEVICE_ID", "jetson-orin-nano-01"))


@app.get('/video_feed')
def video_feed():
    if not vision:
        return '카메라 연결 대기', 503
    return Response(vision.hub.multipart(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.get('/api/live_state')
def live_state():
    return jsonify(vision.hub.snapshot() if vision else {'enabled': False})


@app.get("/api/v1/logs")
def logs():
    try:
        limit = max(1, min(int(request.args.get("limit", "100")), 500))
    except ValueError:
        return jsonify(ok=False, error="limit은 숫자여야 합니다."), 400
    if not db_enabled:
        return jsonify(ok=False, db_enabled=False, error="DB 연동이 꺼져 있습니다.")
    try:
        # 이 Jetson의 로그만 조회합니다. 다른 장치의 오래된 이벤트와 섞지 않습니다.
        items = read_events(limit, os.getenv("DEVICE_ID", "jetson-orin-nano-01"))
        return jsonify(ok=True, count=len(items), items=items,
                       fetched_at_utc=datetime.now(timezone.utc).isoformat())
    except ConfigurationError as exc:
        return jsonify(ok=False, error=str(exc), configured=False), 503
    except (pymysql.MySQLError, OSError, ValueError):
        # 예외 원문에 비밀 정보가 포함될 수 있어 사용자에게 그대로 보내지 않습니다.
        return jsonify(ok=False, error="Aiven DB 연결을 확인해 주세요.", configured=True), 503


@app.get('/api/db')
def db_state():
    return jsonify(enabled=db_enabled)


@app.post('/api/db')
def db_toggle():
    global db_enabled
    if request.headers.get('X-Dashboard-Action') != 'db-toggle':
        return jsonify(ok=False, error='허용되지 않은 요청입니다.'), 403
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get('enabled'), bool):
        return jsonify(ok=False, error='enabled는 true 또는 false여야 합니다.'), 400
    db_enabled = body['enabled']
    # 비전 이벤트 기록(로컬 대기함 저장과 Aiven 전송)도 같이 켜고 끕니다.
    if vision:
        if db_enabled:
            vision.logger.enabled.set()
        else:
            vision.logger.enabled.clear()
    return jsonify(ok=True, enabled=db_enabled)


@app.post('/api/shutdown')
def shutdown():
    # 다른 사이트가 보낸 요청은 이 헤더를 붙일 수 없어 거절됩니다.
    if request.headers.get('X-Dashboard-Action') != 'shutdown':
        return jsonify(ok=False, error='허용되지 않은 요청입니다.'), 403
    # 응답을 먼저 보낸 뒤, stop_jetson.py와 같은 SIGTERM 경로로 모터/LED/핀/카메라를 해제하고 종료합니다.
    threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGTERM)).start()
    return jsonify(ok=True)


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='RYG 비전 + Aiven 로그 + HTML 대시보드')
    parser.add_argument('--vision', action='store_true', help='YuNet 카메라 처리 및 이벤트 기록 활성화')
    parser.add_argument('--camera', type=int, default=0, help='원본 카메라 번호: 0')
    parser.add_argument('--preview', action='store_true', help='OpenCV 창 표시, q 종료 / s 스냅샷')
    parser.add_argument('--hardware-module', help='LED/L9110S apply_state 어댑터: team_hardware')
    args = parser.parse_args()
    if args.vision:
        from vision_runtime import VisionRunner
        vision = VisionRunner(args.camera, args.preview, args.hardware_module)
        vision.start()
        atexit.register(vision.close)
        def stop_signal(signum, frame):
            # SIGTERM으로 종료할 때도 atexit의 모터/LED 해제를 수행합니다.
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop_signal)
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5080")),
            debug=False, threaded=True)
