"""기존 Jetson FSM에 붙일 로그 호출 예시입니다. GPIO/모터를 구동하지 않습니다.

이 파일을 그냥 실행하면 실제 로그를 만들지 않습니다.
아래 on_fsm_event()를 기존 FSM의 상태 변경/오류 처리 지점에서 호출하세요.
"""

import logging
import sqlite3
from datetime import datetime

from aiven_logs import JetsonEventLogger


def on_fsm_event(logger: JetsonEventLogger, *, state: str, reason: str,
                 occurred_at: datetime, motor_stop_command_applied=None):
    """센서값/거리 임계값은 기존 FSM이 판단한 결과를 전달합니다.

    WARNING: HTML 노랑 / 실물 파랑 LED
    DANGER: HTML 빨강 / 실물 빨강 LED + 벨트 모터 정지
    ERROR: 카메라 끊김, 센서 읽기 실패 등
    NORMAL: 정상 복귀
    motor_stop_command_applied는 정지 명령 적용 여부입니다.
    물리적 정지 확인 센서가 없으면 실제 정지 검증으로 해석하면 안 됩니다.
    """
    try:
        return logger.record(
            state,
            reason,
            occurred_at=occurred_at,
            equipment_stopped=motor_stop_command_applied,
            stop_reason=reason if motor_stop_command_applied is True else None,
        )
    except (OSError, ValueError, sqlite3.Error):
        # 로그 디스크 장애가 FSM의 모터 정지 처리를 건너뛰게 해서는 안 됩니다.
        logging.exception("로컬 이벤트 로그 저장 실패")
        return None


# 기존 제어 프로그램에 추가할 흐름:
#
# from datetime import datetime, timezone
# logger = JetsonEventLogger()                 # 프로그램 시작 시 한 번 생성
# try:
#     while running:
#         ...                                 # 카메라/센서 읽기 및 FSM 판단
#         if state_changed or new_error:
#             event_time = datetime.now(timezone.utc)
#             ...                             # 기존 FSM의 LED/모터 명령 적용
#             on_fsm_event(
#                 logger, state=fsm_state, reason=actual_reason,
#                 occurred_at=event_time,
#                 motor_stop_command_applied=stop_command_applied,
#             )
# finally:
#     logger.close()                          # 미전송 항목은 디스크에 유지
#
# 매 프레임이 아니라 상태 전이/새 오류/정지/복귀 시 기록하세요.
