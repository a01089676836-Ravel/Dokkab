#!/bin/bash
# 대시보드(127.0.0.1:5080)를 인터넷 주소(https://….trycloudflare.com)로 엽니다.
# convey_vision.py를 먼저 실행한 뒤, 다른 터미널에서 실행하세요. Ctrl+C로 닫습니다.
# 주소는 실행할 때마다 바뀝니다. 보기는 로그인 없이 되고, 종료·DB 버튼만 .env의 DASHBOARD_PASSWORD를 묻습니다.
BIN="${CLOUDFLARED:-$HOME/tools/cloudflared}"
if [ ! -x "$BIN" ]; then
  echo "cloudflared가 없습니다. 아래 명령으로 받은 뒤 다시 실행하세요 (Jetson arm64용):" >&2
  echo "  mkdir -p ~/tools && curl -fL -o ~/tools/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 && chmod +x ~/tools/cloudflared" >&2
  exit 1
fi
if ! grep -q '^DASHBOARD_PASSWORD=.' "$(dirname "$0")/.env" 2>/dev/null; then
  echo "경고: .env에 DASHBOARD_PASSWORD가 없습니다. 주소를 아는 누구나 종료 버튼을 누를 수 있습니다." >&2
fi
"$BIN" tunnel --no-autoupdate --url http://127.0.0.1:5080 2>&1 \
  | grep --line-buffered -o 'https://[a-z0-9-]*\.trycloudflare\.com' \
  | sed -u 's/^/인터넷 대시보드 주소: /'
