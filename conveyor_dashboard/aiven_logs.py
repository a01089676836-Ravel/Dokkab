"""Jetson 이벤트 -> 로컬 전송 대기함 -> Aiven MySQL.

FSM에서 record()만 호출합니다. DB 접속은 별도 스레드에서 수행하므로
네트워크 대기 때문에 LED/벨트 모터 제어 루프가 멈추지 않습니다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
KST = timezone(timedelta(hours=9))
SEVERITIES = {"NORMAL", "WARNING", "DANGER", "ERROR"}
LOG = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """아직 DB 접속 정보를 설정하지 않았을 때 발생합니다."""


def local_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


@contextmanager
def mysql_connection():
    """CA 인증서와 서버 이름을 검증하는 암호화 연결입니다."""
    required = ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ConfigurationError(".env에 필요한 항목: " + ", ".join(missing))
    ca_path = local_path(os.getenv("MYSQL_CA_PATH", "ca.pem"))
    if not ca_path.is_file():
        raise ConfigurationError("Aiven에서 받은 CA 인증서 파일이 필요합니다.")
    try:
        port = int(os.environ["MYSQL_PORT"])
    except ValueError as exc:
        raise ConfigurationError("MYSQL_PORT는 숫자여야 합니다.") from exc
    if not 1 <= port <= 65535:
        raise ConfigurationError("MYSQL_PORT는 1~65535여야 합니다.")
    connection = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=port,
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.getenv("MYSQL_DATABASE", "defaultdb"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        ssl_ca=str(ca_path),
        ssl_verify_cert=True,
        ssl_verify_identity=True,
        connect_timeout=5,
        read_timeout=5,
        write_timeout=5,
        autocommit=True,
    )
    try:
        yield connection
    finally:
        connection.close()


def init_db():
    """노트북의 CREATE TABLE 흐름으로 로그 테이블만 생성합니다."""
    with mysql_connection() as db, db.cursor() as cursor:
        cursor.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))


def make_event(severity, description, *, equipment_stopped=None,
               stop_reason=None, occurred_at=None, device_id=None):
    """실제 이벤트 발생 시점에 호출합니다. 날짜/시간은 서버 수신 때 바꾸지 않습니다."""
    if severity not in SEVERITIES:
        raise ValueError("상태는 NORMAL, WARNING, DANGER, ERROR 중 하나여야 합니다.")
    if not isinstance(description, str) or not description.strip() or len(description) > 4000:
        raise ValueError("이벤트 설명은 1~4000자여야 합니다.")
    if equipment_stopped is not None and not isinstance(equipment_stopped, bool):
        raise ValueError("equipment_stopped는 True, False, None 중 하나입니다.")
    if stop_reason is not None and (not isinstance(stop_reason, str) or len(stop_reason) > 4000):
        raise ValueError("정지 이유는 4000자 이내 문자열이어야 합니다.")
    if equipment_stopped is True and not (stop_reason and stop_reason.strip()):
        raise ValueError("정지 이벤트에는 stop_reason(정지 이유)이 필요합니다.")
    moment = occurred_at or datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("발생 시각에는 시간대가 포함된 datetime이 필요합니다.")
    device = device_id or os.getenv("DEVICE_ID", "jetson-orin-nano-01")
    if not isinstance(device, str) or not device.strip() or len(device) > 100:
        raise ValueError("장치 이름은 1~100자여야 합니다.")
    return {
        "event_id": str(uuid.uuid4()),
        "device_id": device.strip(),
        "occurred_at_utc": moment.astimezone(timezone.utc).isoformat(timespec="milliseconds"),
        "severity": severity,
        "event_description": description.strip(),
        "equipment_stopped": equipment_stopped,
        "stop_reason": stop_reason.strip() if stop_reason else None,
    }


def insert_event(event):
    """UUID를 유지한 재전송은 중복 저장하지 않습니다. SQL에는 매개변수를 사용합니다."""
    occurred = datetime.fromisoformat(event["occurred_at_utc"])
    local = occurred.astimezone(KST)
    values = (
        event["event_id"], event["device_id"],
        occurred.astimezone(timezone.utc).replace(tzinfo=None),
        local.date(), local.time().replace(tzinfo=None), event["severity"],
        event["event_description"], event["equipment_stopped"], event["stop_reason"],
        datetime.now(timezone.utc).replace(tzinfo=None),
    )
    with mysql_connection() as db, db.cursor() as cursor:
        cursor.execute("""
            INSERT INTO conveyor_event_logs
            (event_id, device_id, occurred_at_utc, event_date, event_time, severity,
             event_description, equipment_stopped, stop_reason, received_at_utc)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE event_id = event_id
        """, values)


def read_events(limit=100, device_id=None):
    """도착 순서가 아니라 실제 발생 시각이 최신인 로그를 위에 표시합니다."""
    limit = max(1, min(int(limit), 500))
    where = " WHERE device_id = %s" if device_id else ""
    params = (device_id, limit) if device_id else (limit,)
    with mysql_connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT * FROM conveyor_event_logs" + where +
                       " ORDER BY occurred_at_utc DESC, id DESC LIMIT %s", params)
        rows = cursor.fetchall()
    for row in rows:
        moment = row["occurred_at_utc"].replace(tzinfo=timezone.utc)
        local = moment.astimezone(KST)
        row["occurred_at_utc"] = moment.isoformat(timespec="milliseconds")
        row["event_date"] = local.strftime("%Y-%m-%d")
        row["event_time"] = local.strftime("%H:%M:%S.%f")[:-3]
        row["received_at_utc"] = row["received_at_utc"].replace(tzinfo=timezone.utc).isoformat()
        if row["equipment_stopped"] is not None:
            row["equipment_stopped"] = bool(row["equipment_stopped"])
    return rows


class JetsonEventLogger:
    """인터넷 장애/프로그램 재시작 후에도 전송 대기 로그를 재전송합니다.

    한 Jetson 제어 프로그램에서 하나만 생성하세요. record()는 로컬 디스크에
    저장하고 바로 반환합니다. 클라우드 전송은 worker가 담당합니다.
    """

    def __init__(self, outbox_path=None):
        self.path = local_path(outbox_path or os.getenv("OUTBOX_PATH", "jetson_log_outbox.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._flush_lock = threading.Lock()
        # HTML의 DB 연동 버튼으로 켜고 끕니다. 꺼져 있으면 기록하지 않고 전송도 쉽니다.
        self.enabled = threading.Event()
        self.enabled.set()
        with self._outbox() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS pending_events (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                queued_at TEXT NOT NULL)""")
        self._worker = threading.Thread(target=self._run, name="aiven-log-writer", daemon=True)
        self._worker.start()

    @contextmanager
    def _outbox(self):
        db = sqlite3.connect(self.path, timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, severity, description, **kwargs):
        if not self.enabled.is_set():
            return None
        event = make_event(severity, description, **kwargs)
        with self._outbox() as db:
            db.execute("INSERT INTO pending_events VALUES (?, ?, ?)", (
                event["event_id"], json.dumps(event, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ))
        self._wake.set()
        return event["event_id"]

    def pending_count(self):
        with self._outbox() as db:
            return db.execute("SELECT COUNT(*) FROM pending_events").fetchone()[0]

    def flush(self, limit=100):
        """성공한 항목만 대기함에서 지웁니다. 실패한 항목은 원래 시각 그대로 남깁니다."""
        sent = 0
        with self._flush_lock:
            with self._outbox() as db:
                rows = db.execute("SELECT event_id, payload FROM pending_events "
                                  "ORDER BY queued_at, event_id LIMIT ?", (limit,)).fetchall()
            for event_id, payload in rows:
                if not self.enabled.is_set():
                    break  # 전송 도중 DB 연동을 끄면 남은 항목은 보내지 않습니다.
                insert_event(json.loads(payload))
                with self._outbox() as db:
                    db.execute("DELETE FROM pending_events WHERE event_id = ?", (event_id,))
                sent += 1
        return sent

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            if not self.enabled.is_set():
                self._wake.wait(timeout=5)
                continue
            try:
                sent = self.flush()
                if sent == 100:
                    continue
            except (pymysql.MySQLError, OSError, ValueError):
                # 접속 비밀번호/호스트 등은 출력하지 않습니다.
                LOG.warning("Aiven 전송 실패: 로그를 로컬 대기함에 보관하고 재시도합니다.")
            self._wake.wait(timeout=5)

    def close(self):
        self._stop.set()
        self._wake.set()
        self._worker.join(timeout=6)
        # 미전송 항목은 삭제하지 않습니다. 다음 실행 시 다시 보냅니다.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aiven 로그 테이블 생성 / 전송 대기 로그 조회")
    parser.add_argument("--init-db", action="store_true")
    args = parser.parse_args()
    if args.init_db:
        try:
            init_db()
            print("Aiven MySQL: conveyor_event_logs 테이블 준비 완료")
        except (pymysql.MySQLError, OSError, ValueError) as exc:
            print("DB 연결 실패:", str(exc) if isinstance(exc, ConfigurationError) else type(exc).__name__)
            raise SystemExit(1)
    else:
        parser.print_help()
