"""convey_vision.py를 실행하고, 꺼지면 같은 주소(5080)에 '다시 시작' 화면을 띄웁니다.

실행 (~/Dokkab에서, Jetson 모니터에 로그인된 상태):
    conveyor_dashboard/.venv/bin/python conveyor_dashboard/launcher.py

대시보드의 '대시보드 종료' 버튼은 convey_vision.py만 끕니다(벨트 정지, 카메라·핀 해제).
그 뒤 같은 주소를 새로고침해 비밀번호(.env의 DASHBOARD_PASSWORD)를 넣고 [다시 시작]을 누르면 다시 켭니다.
런처까지 끄려면 터미널에서 Ctrl+C를 누릅니다. convey_vision.py는 수정하지 않습니다.
"""

import hmac
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
load_dotenv(HERE / ".env")
HOST = "0.0.0.0"  # convey_vision.py의 WEB_HOST와 같은 주소로 엽니다.
PORT = 5080

app = Flask(__name__)
start_requested = threading.Event()
last_exit = None  # 마지막으로 꺼진 convey_vision.py의 종료 코드


class Stop(Exception):
    """런처가 SIGTERM을 받았을 때 씁니다."""


def on_sigterm(signum, frame):
    raise Stop


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>컨베이어 프로그램 꺼짐</title>
<style>
:root { --bg:#f4f6f8; --paper:#fff; --ink:#16202b; --muted:#5a6776; --line:#d3dae2; --accent:#2550b8; }
@media (prefers-color-scheme: dark) { :root { --bg:#10161d; --paper:#17202a; --ink:#e4eaf0; --muted:#9aa8b6; --line:#2b3744; --accent:#7ea2ff; } }
body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--ink);
  font-family:"Malgun Gothic", system-ui, sans-serif; padding:16px; box-sizing:border-box; }
main { background:var(--paper); border:1px solid var(--line); border-top:4px solid var(--accent); padding:28px; max-width:460px; width:100%; display:grid; gap:14px; }
h1 { font-size:22px; margin:0; }
p { margin:0; color:var(--muted); line-height:1.6; }
button { font:inherit; font-weight:600; padding:10px 18px; border:0; background:var(--accent); color:#fff; cursor:pointer; justify-self:start; }
button:disabled { opacity:.5; cursor:default; }
#msg { color:var(--ink); min-height:1.6em; }
</style></head><body><main>
<h1>컨베이어 프로그램이 꺼져 있습니다</h1>
<p>벨트는 멈춰 있고 카메라와 GPIO 핀은 해제된 상태입니다. 마지막 종료 코드: <b>__EXIT__</b></p>
<p>[다시 시작]을 누르면 비밀번호를 확인한 뒤 convey_vision.py를 다시 켭니다. 켜지면 벨트가 바로 돌기 시작합니다.</p>
<button id="start">다시 시작</button>
<p id="msg"></p>
</main><script>
const btn = document.getElementById('start'), msg = (t) => { document.getElementById('msg').textContent = t; };
let password = '';
btn.addEventListener('click', async () => {
  btn.disabled = true; msg('시작 요청 중…');
  try {
    for (let attempt = 0; ; attempt++) {
      const r = await fetch('/api/start', { method: 'POST',
        headers: { 'X-Dashboard-Action': 'start', 'X-Dashboard-Password': encodeURIComponent(password) } });
      if (r.ok) break;
      if (r.status !== 401) throw new Error(`시작 요청 실패: HTTP ${r.status}`);
      if (attempt === 2) throw new Error('비밀번호가 맞지 않습니다.');
      const typed = prompt(attempt ? '비밀번호가 틀렸습니다. 다시 입력하세요.' : '대시보드 비밀번호를 입력하세요.');
      if (typed === null) throw new Error('비밀번호 입력을 취소했습니다.');
      password = typed;
    }
    msg('프로그램을 켜는 중입니다. 카메라와 모델 준비에 몇 초 걸립니다…');
    for (let i = 0; i < 45; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try { if ((await fetch('/api/config', { cache: 'no-store' })).ok) { location.reload(); return; } } catch (e) {}
    }
    msg('90초 안에 켜지지 않았습니다. Jetson 터미널을 확인하세요.'); btn.disabled = false;
  } catch (error) { msg(error.message); btn.disabled = false; }
});
</script></body></html>"""


@app.get("/")
def page():
    return PAGE.replace("__EXIT__", "없음" if last_exit is None else str(last_exit))


@app.post("/api/start")
def start():
    # 대시보드의 종료·DB 버튼과 같은 규칙: 전용 헤더 + .env의 DASHBOARD_PASSWORD
    if request.headers.get("X-Dashboard-Action") != "start":
        return jsonify(ok=False, error="허용되지 않은 요청입니다."), 403
    password = os.getenv("DASHBOARD_PASSWORD", "")
    given = unquote(request.headers.get("X-Dashboard-Password", ""))
    if password and not hmac.compare_digest(given.encode(), password.encode()):
        return jsonify(ok=False, error="비밀번호가 맞지 않습니다."), 401
    start_requested.set()
    return jsonify(ok=True)


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def wait_for_start():
    """'다시 시작' 화면을 띄우고, 버튼이 눌릴 때까지 기다린 뒤 포트를 비웁니다."""
    start_requested.clear()
    server = make_server(HOST, PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"convey_vision.py가 꺼졌습니다(종료 코드 {last_exit}). "
          f"http://{HOST}:{PORT} 에서 [다시 시작]을 누르면 다시 켭니다. 런처 종료는 Ctrl+C.", flush=True)
    try:
        while not start_requested.wait(timeout=1):
            pass
        time.sleep(0.5)  # 시작 응답이 브라우저에 도착하도록 잠시 기다립니다.
    finally:
        server.shutdown()
        server.server_close()


def main():
    global last_exit
    signal.signal(signal.SIGTERM, on_sigterm)
    while True:
        child = subprocess.Popen([sys.executable, "convey_vision.py"], cwd=REPO)
        try:
            last_exit = child.wait()
        except KeyboardInterrupt:
            # Ctrl+C는 같은 터미널의 convey_vision.py에도 전달됩니다. 벨트 정지 정리가 끝날 때까지 기다립니다.
            child.wait()
            return
        except Stop:
            # 런처만 SIGTERM을 받은 경우: convey_vision.py에도 같은 종료 경로(SIGTERM)를 보냅니다.
            child.terminate()
            child.wait()
            return
        try:
            wait_for_start()
        except (KeyboardInterrupt, Stop):
            return


if __name__ == "__main__":
    main()
