"""이 폴더의 server.pid에 기록된 대시보드만 종료합니다. Jetson 전용."""
import os
import signal
from pathlib import Path

root = Path(__file__).resolve().parent
pid_file = root / 'server.pid'
if not pid_file.exists():
    raise SystemExit('이 폴더에서 시작한 백그라운드 서버 PID가 없습니다.')
pid = int(pid_file.read_text().strip())
proc = Path('/proc') / str(pid)
if not proc.exists():
    raise SystemExit('서버가 이미 종료되어 있습니다.')
cwd = (proc / 'cwd').resolve()
args = (proc / 'cmdline').read_bytes().split(b'\0')
if cwd != root or not any(arg in (b'app.py', str(root/'app.py').encode()) for arg in args):
    raise SystemExit('다른 프로세스이므로 종료하지 않습니다.')
os.kill(pid, signal.SIGTERM)
print('이 프로젝트의 대시보드 서버를 종료했습니다.')
