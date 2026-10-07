/* 화면은 FSM의 기록을 표시합니다. 여기에서 위험 기준이나 GPIO 동작을 결정하지 않습니다. */
const $ = (id) => document.getElementById(id);
const names = { NORMAL: '정상', WARNING: '경고', DANGER: '위험', ERROR: '오류' };
let records = [], demoRecords = [], demo = false, filter = 'ALL', fetching = false;
let range = null; // 기간 검색 { from, to } (한국 날짜). null이면 최근 100건
let dbVersion = 0; // DB 버튼을 누를 때마다 늘립니다. 누르기 전에 보낸 로그 응답은 버튼 상태를 바꾸지 않습니다.
let cameraStream = null, liveState = null, visionEnabled = false, jetsonSource = '', cameraManuallyStopped = false;

function kstParts(moment) {
  const parts = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).formatToParts(moment);
  const p = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}:${p.second}` };
}

function clock() {
  const p = kstParts(new Date());
  $('clock').textContent = `${p.date}  ${p.time} KST`;
}
clock(); setInterval(clock, 1000);

function emptyMessage(title, message) {
  const row = document.createElement('tr');
  const cell = document.createElement('td'); cell.className = 'empty'; cell.colSpan = 4;
  const strong = document.createElement('strong'); strong.textContent = title;
  cell.append(strong, message); row.append(cell); $('logList').replaceChildren(row);
}

// 신호등: 현재 상태에 맞는 불 하나만 켠다. 프로그램 종료(정상+정지)와 오류는 모두 끈다.
function setSignal(event) {
  const lit = !event || (event.severity === 'NORMAL' && event.equipment_stopped === true) ? null
    : { DANGER: 'lampRed', WARNING: 'lampYellow', NORMAL: 'lampGreen' }[event.severity];
  ['lampRed', 'lampYellow', 'lampGreen'].forEach((id) => $(id).classList.toggle('on', id === lit));
}

function showStatus(event) {
  setSignal(event);
  const card = $('statusCard'); card.className = 'status';
  if (!event) {
    $('statusIcon').textContent = '—'; $('statusTitle').textContent = '상태 확인 대기';
    $('statusReason').textContent = '장비에서 기록된 이벤트가 아직 없습니다.';
    $('statusMeta').textContent = '마지막 기록 기준으로 상태를 표시합니다.'; return;
  }
  card.classList.add(event.severity.toLowerCase());
  let title = names[event.severity] || '상태 미확인';
  if (event.equipment_stopped === true) title += ' · 작동정지';
  else if (event.severity === 'DANGER') title += ' · 정지 적용 미확인';
  $('statusTitle').textContent = title;
  $('statusIcon').textContent = event.severity === 'NORMAL' ? '✓' : '!';
  $('statusReason').textContent = event.stop_reason || event.event_description;
  $('statusMeta').textContent = `${demo ? '화면 시연' : '마지막 기록'} · ${event.event_date} ${event.event_time.slice(0, 8)} KST`;
}

function render() {
  const items = demo ? demoRecords : records;
  $('totalCount').textContent = items.length;
  $('warningCount').textContent = items.filter((e) => e.severity === 'WARNING').length;
  $('stopCount').textContent = items.filter((e) => e.equipment_stopped === true).length;
  showStatus(!demo && liveState ? liveState : items[0]);
  const visible = items.filter((e) => filter === 'ALL' || e.severity === filter);
  if (!visible.length) {
    emptyMessage('표시할 기록이 없습니다', items.length ? '선택한 상태의 이벤트가 없습니다.'
      : range && !demo ? '선택한 기간에 기록된 이벤트가 없습니다.' : '젯슨에서 발생한 날짜·시간과 이벤트 설명이 여기에 표시됩니다.');
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const event of visible) {
    const row = document.createElement('tr');
    const date = document.createElement('td'); date.className = 'date'; date.textContent = event.event_date;
    const time = document.createElement('td'); time.className = 'time'; time.textContent = event.event_time.slice(0, 8);
    const level = document.createElement('td'); level.className = 'level';
    const badge = document.createElement('span');
    const shutdownStop = event.severity === 'NORMAL' && event.equipment_stopped === true;
    badge.className = `log-badge ${shutdownStop ? 'stopped' : event.severity.toLowerCase()}`;
    // 프로그램 종료 기록은 NORMAL + 정지로 저장되므로 '정상·정지' 대신 '정지'로 표시합니다.
    badge.textContent = event.equipment_stopped !== true ? names[event.severity]
      : event.severity === 'NORMAL' ? '정지' : `${names[event.severity]}·정지`;
    level.append(badge);
    const content = document.createElement('td');
    const description = document.createElement('p'); description.className = 'log-description';
    // DB의 문자열을 HTML로 삽입하지 않습니다.
    description.textContent = event.event_description;
    content.append(description);
    if (event.stop_reason && event.stop_reason !== event.event_description) {
      const reason = document.createElement('p'); reason.className = 'stop-reason';
      reason.textContent = `정지 이유: ${event.stop_reason}`; content.append(reason);
    }
    row.append(date, time, level, content); fragment.append(row);
  }
  $('logList').replaceChildren(fragment);
}

async function loadLogs() {
  if (demo || fetching || location.protocol === 'file:') return;
  fetching = true;
  const asked = range, askedDb = dbVersion;
  try {
    const query = asked ? `limit=500&from=${asked.from}&to=${asked.to}` : 'limit=100';
    const response = await fetch(`/api/v1/logs?${query}`, { cache: 'no-store', signal: AbortSignal.timeout(10000) });
    const data = await response.json();
    // 응답을 기다리는 사이 기간이 바뀌거나 DB 버튼을 눌렀으면 버립니다(finally에서 다시 불러옴).
    if (demo || asked !== range || askedDb !== dbVersion) return;
    if (data.db_enabled === false) {
      showDbToggle(false);
      $('dbBadge').className = 'pill'; $('dbText').textContent = 'DB 연동 꺼짐';
      $('connectionNotice').textContent = 'DB 연동이 꺼져 있어 새 이벤트를 기록하지 않습니다. 아래 표는 끄기 전 기록입니다.';
      $('logFooter').textContent = 'DB 연동 꺼짐 · 기록·갱신 중지';
      if (!records.length) emptyMessage('DB 연동이 꺼져 있습니다', '위쪽 버튼으로 다시 켤 수 있습니다.');
      return;
    }
    if (!response.ok || !data.ok) throw new Error(data.error || 'DB 연결을 확인해 주세요.');
    records = data.items;
    showDbToggle(true);
    $('dbBadge').className = 'pill connected'; $('dbText').textContent = 'DB 연결됨';
    $('connectionNotice').textContent = 'Aiven MySQL의 발생 시각을 기준으로 최신 기록부터 표시합니다.';
    $('logFooter').textContent = !asked ? '한국 시각 · 최근 100건 · 5초마다 갱신'
      : `한국 시각 · ${asked.from} ~ ${asked.to} · 전체 ${data.total}건`
        + (data.total > data.count ? ` 중 최신 ${data.count}건 표시` : '') + ' · 5초마다 갱신';
    render();
  } catch (error) {
    if (demo) return;
    $('dbBadge').className = 'pill'; $('dbText').textContent = 'DB 연결 대기';
    $('connectionNotice').textContent = `로그 갱신 중단 · ${error.message}`;
    $('logFooter').textContent = 'DB 연결 대기 · 기존 기록은 유지됩니다';
    if (!records.length) {
      showStatus(liveState); emptyMessage('DB 연결을 기다리고 있습니다', '연결되면 실제 이벤트 기록이 여기에 표시됩니다.');
    }
  } finally {
    fetching = false;
    if (asked !== range || askedDb !== dbVersion) loadLogs();
  }
}

// 기간 검색: 버튼은 오늘까지의 기간을 달력 칸에 채우고, 달력으로 직접 고른 뒤 [검색]도 됩니다.
const presets = { today: {}, '7d': { days: 6 }, '1m': { months: 1 }, '3m': { months: 3 } };
function shiftDate(day, { days = 0, months = 0 }) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() - months); d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}
function markRange(key) {
  document.querySelectorAll('[data-range]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.range === key)));
}
document.querySelectorAll('[data-range]').forEach((button) => button.addEventListener('click', () => {
  const key = button.dataset.range;
  if (key === 'live') {
    range = null; $('rangeFrom').value = ''; $('rangeTo').value = '';
  } else {
    const to = kstParts(new Date()).date;
    range = { from: shiftDate(to, presets[key]), to };
    $('rangeFrom').value = range.from; $('rangeTo').value = range.to;
  }
  markRange(key); $('logFooter').textContent = '검색 중…'; loadLogs();
}));
$('rangeSearch').addEventListener('click', () => {
  const from = $('rangeFrom').value, to = $('rangeTo').value;
  if (!from || !to) { $('connectionNotice').textContent = '시작 날짜와 끝 날짜를 모두 고르세요.'; return; }
  if (from > to) { $('connectionNotice').textContent = '시작 날짜가 끝 날짜보다 늦습니다.'; return; }
  range = { from, to }; markRange(null); $('logFooter').textContent = '검색 중…'; loadLogs();
});

document.querySelectorAll('[data-filter]').forEach((button) => button.addEventListener('click', () => {
  filter = button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
  render();
}));
$('refreshLogs').addEventListener('click', () => demo ? render() : loadLogs());

// 시연은 브라우저 메모리에만 저장합니다. DB에 가짜 오류/정지 기록을 쓰지 않습니다.
document.querySelectorAll('[data-demo]').forEach((button) => button.addEventListener('click', () => {
  demo = true;
  const severity = button.dataset.demo, moment = new Date(), p = kstParts(moment);
  const descriptions = {
    NORMAL: '시연: 작업 구역이 정상 상태로 복귀했습니다.',
    WARNING: '시연: 작업자 접근 경고 · 벨트는 계속 작동합니다.',
    DANGER: '시연: 작업자 위험 접근으로 벨트 모터 정지 명령을 적용했습니다.',
    ERROR: '시연: 카메라 영상 수신이 끊겼습니다.',
  };
  demoRecords.unshift({ event_id: crypto.randomUUID(), occurred_at_utc: moment.toISOString(),
    event_date: p.date, event_time: p.time, severity, event_description: descriptions[severity],
    equipment_stopped: severity === 'DANGER',
    stop_reason: severity === 'DANGER' ? '작업자 위험 접근' : null });
  demoRecords = demoRecords.slice(0, 100);
  $('dbBadge').className = 'pill'; $('dbText').textContent = '화면 시연';
  $('connectionNotice').textContent = '시연 중 · 장비 미연결 · 이 기록은 실제 DB에 저장되지 않습니다.';
  $('logFooter').textContent = '화면 시연 기록 · DB 미저장'; render();
}));
$('exitDemo').addEventListener('click', () => {
  demo = false; demoRecords = []; render(); $('dbText').textContent = 'DB 확인 중';
  $('connectionNotice').textContent = '실제 이벤트 기록을 불러옵니다.'; loadLogs();
});

function stopCamera() {
  cameraManuallyStopped = true;
  if (cameraStream) cameraStream.getTracks().forEach((track) => track.stop());
  cameraStream = null; $('cameraVideo').srcObject = null; $('cameraVideo').hidden = true;
  $('cameraImage').removeAttribute('src'); $('cameraImage').hidden = true;
  $('cameraEmpty').hidden = false; $('cameraState').textContent = '연결 대기';
  $('cameraTag').textContent = '영상 없음'; $('disconnectCamera').disabled = true;
  $('cameraMessage').textContent = '카메라 연결을 기다리고 있습니다';
}
$('disconnectCamera').addEventListener('click', stopCamera);
$('connectCamera').addEventListener('click', async () => {
  stopCamera();
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('localhost 주소에서 카메라를 연결하세요.');
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    $('cameraVideo').srcObject = cameraStream; await $('cameraVideo').play();
    $('cameraVideo').hidden = false; $('cameraEmpty').hidden = true;
    $('cameraState').textContent = '이 PC의 카메라'; $('cameraTag').textContent = 'PC 카메라';
    $('disconnectCamera').disabled = false;
  } catch (error) { stopCamera(); $('cameraMessage').textContent = `카메라 연결 실패: ${error.message}`; }
});
window.addEventListener('pagehide', stopCamera);
function showDbToggle(enabled) {
  $('dbToggle').setAttribute('aria-pressed', String(enabled));
  $('dbToggle').textContent = enabled ? 'DB 연동 켜짐' : 'DB 연동 꺼짐';
}
// 종료·DB 버튼은 비밀번호가 필요합니다. 맞힌 비밀번호는 새로고침 전까지만 기억합니다.
let controlPassword = '';
async function protectedPost(url, action, body) {
  for (let attempt = 0; ; attempt++) {
    const headers = { 'X-Dashboard-Action': action, 'X-Dashboard-Password': encodeURIComponent(controlPassword) };
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(url, { method: 'POST', headers, body: body ? JSON.stringify(body) : undefined });
    if (response.status !== 401) return response;
    if (attempt === 2) throw new Error('비밀번호가 맞지 않습니다.');
    const typed = prompt(attempt ? '비밀번호가 틀렸습니다. 다시 입력하세요.' : '이 버튼은 비밀번호가 필요합니다. 대시보드 비밀번호를 입력하세요.');
    if (typed === null) throw new Error('비밀번호 입력을 취소했습니다.');
    controlPassword = typed;
  }
}
$('dbToggle').addEventListener('click', async () => {
  const next = $('dbToggle').getAttribute('aria-pressed') !== 'true';
  $('dbToggle').disabled = true;
  try {
    const response = await protectedPost('/api/db', 'db-toggle', { enabled: next });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    dbVersion++;
    showDbToggle(data.enabled);
    if (data.enabled) { $('dbText').textContent = 'DB 확인 중'; $('connectionNotice').textContent = 'DB 연동을 다시 켰습니다.'; }
    loadLogs();
  } catch (error) {
    alert(`DB 연동 변경 실패: ${error.message}`);
  } finally { $('dbToggle').disabled = false; }
});
$('shutdownServer').addEventListener('click', async () => {
  if (!confirm('대시보드 서버를 종료할까요?\n벨트 모터를 멈추고 카메라·GPIO 핀을 해제합니다.\n종료 후 rail_face.py를 실행할 수 있습니다.')) return;
  $('shutdownServer').disabled = true;
  try {
    const response = await protectedPost('/api/shutdown', 'shutdown');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    stopCamera();
    $('shutdownServer').textContent = '종료됨';
    $('cameraMessage').textContent = '프로그램을 종료했습니다. 몇 초 뒤 이 페이지를 새로고침하면 [다시 시작] 버튼이 나옵니다(launcher.py로 실행한 경우).';
  } catch (error) {
    $('shutdownServer').disabled = false;
    alert(`종료 요청 실패: ${error.message}`);
  }
});

async function configure() {
  if (location.protocol === 'file:') {
    $('dbText').textContent = 'HTML 미리보기';
    $('connectionNotice').textContent = '실제 DB 조회는 Python 서버를 실행한 뒤 http://127.0.0.1:5080에서 확인하세요.';
    emptyMessage('HTML 미리보기', '화면 시연에서 노란 경고와 빨간 위험·정지 표시를 볼 수 있습니다.');
    return;
  }
  try {
    const config = await (await fetch('/api/config')).json(); $('deviceName').textContent = config.device_id;
    showDbToggle((await (await fetch('/api/db')).json()).enabled);
    visionEnabled = config.vision_enabled;
    if (config.camera_stream_url) {
      const url = new URL(config.camera_stream_url, location.href);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('영상 주소는 http 또는 https여야 합니다.');
      jetsonSource = url.href;
      $('cameraImage').onload = () => {
        $('cameraImage').hidden = false; $('cameraEmpty').hidden = true;
        $('cameraState').textContent = 'Jetson 카메라'; $('cameraTag').textContent = 'Jetson 카메라';
        $('disconnectCamera').disabled = false;
      };
      $('cameraImage').onerror = () => { stopCamera(); cameraManuallyStopped = false; $('cameraMessage').textContent = '젯슨 영상 주소와 연결 상태를 확인하세요.'; };
      $('cameraImage').src = url.href;
    }
  } catch (error) { $('cameraMessage').textContent = `영상 설정 확인: ${error.message}`; }
  loadLogs(); setInterval(loadLogs, 5000);
  if (visionEnabled) { loadLiveState(); setInterval(loadLiveState, 1000); }
}

async function loadLiveState() {
  if (demo) return;
  try {
    const response = await fetch('/api/live_state', {cache:'no-store', signal:AbortSignal.timeout(3000)});
    if (!response.ok) throw new Error('비전 상태 수신 실패');
    const data = await response.json();
    if (demo || !data.enabled) return;
    if (data.event) {
      liveState = data.event;
      if (!data.camera_online && liveState.severity !== 'ERROR') {
        liveState = {...liveState, severity:'ERROR', event_description:'카메라 영상 수신 중단', stop_reason:null};
      }
      showStatus(liveState);
      $('statusMeta').textContent = `비전 수신 상태 · ${liveState.event_date} ${liveState.event_time.slice(0,8)} KST`;
    }
    if (!data.camera_online && !cameraStream) {
      $('cameraState').textContent = '젯슨 영상 수신 대기';
      $('cameraImage').hidden = true; $('cameraEmpty').hidden = false;
      $('cameraMessage').textContent = '젯슨 카메라 연결을 확인하세요';
      $('cameraTag').textContent = 'Jetson 카메라 끊김';
    } else if (data.camera_online && !cameraStream && !cameraManuallyStopped) {
      if (!$('cameraImage').getAttribute('src')) $('cameraImage').src = jetsonSource;
      $('cameraImage').hidden = false; $('cameraEmpty').hidden = true;
      $('cameraState').textContent = 'Jetson 카메라'; $('cameraTag').textContent = 'Jetson 카메라';
      $('disconnectCamera').disabled = false;
    }
  } catch (error) {
    $('cameraState').textContent = error.message;
    if (liveState) showStatus({...liveState, severity:'ERROR', event_description:'비전 상태 연결 끊김', stop_reason:null});
  }
}
configure();
