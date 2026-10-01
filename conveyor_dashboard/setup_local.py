"""접속 URI를 채팅/터미널 출력 없이 .env에 저장하는 임시 로컬 설정 도구.

127.0.0.1에서만 실행하며 설정 후 종료하세요. HTML 로그 서버와는 별개입니다.
"""
import secrets
import ssl
from urllib.parse import unquote, urlparse

from flask import Flask, redirect, render_template_string, request

from aiven_logs import ROOT

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 128 * 1024
TOKEN = secrets.token_urlsafe(32)
HTML = '''<!doctype html><html lang="ko"><meta charset="utf-8"><title>Aiven 로컬 연결 설정</title>
<style>body{font:16px/1.7 "Segoe UI",sans-serif;background:#101722;color:#eef3fa;max-width:650px;margin:60px auto;padding:25px}input{display:block;margin:10px 0 25px;padding:10px;width:100%;box-sizing:border-box}button{padding:12px 20px}small{color:#a3b4cb}</style>
<h1>Aiven 로컬 연결 설정</h1><p>기존 서비스의 접속 정보를 이 PC의 프로젝트에 저장합니다.</p>
<form method="post" enctype="multipart/form-data" autocomplete="off">
<input type="hidden" name="token" value="{{ token }}">
<label for="uri">Aiven 서비스 URI</label><input id="uri" name="uri" type="password" required autocomplete="off">
<label for="ca">Aiven CA 인증서</label><textarea id="ca" name="ca" rows="5" style="width:100%" required></textarea>
<button type="submit">로컬 연결 설정 저장</button></form>
<p><small>비밀번호는 .env에 저장되며 화면이나 터미널에 출력하지 않습니다. 기존 DB 데이터는 변경하지 않습니다.</small></p></html>'''


def quote(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


@app.route('/', methods=['GET', 'POST'])
def setup():
    if request.remote_addr != '127.0.0.1' or request.host != '127.0.0.1:5081':
        return 'Local access only', 403
    if request.method == 'GET':
        return render_template_string(HTML, token=TOKEN)
    if not secrets.compare_digest(request.form.get('token', ''), TOKEN):
        return 'Invalid token', 403
    if (ROOT / '.env').exists():
        return '기존 설정을 덮어쓰지 않습니다. 기존 .env를 확인하세요.', 409
    try:
        uri = urlparse(request.form['uri'])
        if uri.scheme != 'mysql' or not uri.hostname or not uri.username or not uri.password or not uri.port:
            raise ValueError()
        ca = request.form['ca'].encode('ascii')
        ssl.create_default_context(cadata=ca.decode('ascii'))
        values = dict(MYSQL_HOST=uri.hostname, MYSQL_PORT=str(uri.port),
                      MYSQL_USER=unquote(uri.username), MYSQL_PASSWORD=unquote(uri.password),
                      MYSQL_DATABASE=unquote(uri.path.lstrip('/')) or 'defaultdb',
                      MYSQL_CA_PATH='ca.pem', DEVICE_ID='jetson-orin-nano-01',
                      OUTBOX_PATH='jetson_log_outbox.sqlite3', HOST='127.0.0.1', PORT='5080', CAMERA_STREAM_URL='')
        (ROOT / 'ca.pem').write_bytes(ca)
        (ROOT / '.env').write_text('\n'.join(k+'='+quote(v) for k,v in values.items())+'\n', encoding='utf-8')
    except (ValueError, KeyError, UnicodeError, ssl.SSLError):
        return '서비스 URI 또는 CA 인증서를 확인하세요.', 400
    return redirect('/done')


@app.get('/done')
def done():
    return '<meta charset="utf-8"><h1>로컬 연결 설정 저장 완료</h1><p>접속 비밀번호는 화면에 표시하지 않았습니다. 설정 서버를 종료하세요.</p>'


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5081, debug=False)
