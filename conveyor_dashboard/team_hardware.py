"""사용자가 확인한 L9110S DC 벨트 모터 + 빨강/경고/초록 LED 배선 (rail.py와 동일).

BOARD 15: 빨강 LED, 31: 경고 LED, 32: 초록 LED, 33: A-1A, 29: A-1B.
import만으로 GPIO를 설정하거나 모터를 구동하지 않습니다.
app.py --hardware-module team_hardware로 연결한 경우에만 동작합니다.
"""
import logging
import threading
import warnings

RED = 15
WARN = 31
GREEN = 32
A1A = 33
A1B = 29
PINS = (A1A, A1B, RED, WARN, GREEN)

_gpio = None
_ready = False
_owned = []
_lock = threading.RLock()
LOG = logging.getLogger(__name__)


def _initialize():
    global _gpio, _ready
    if _ready:
        return
    import Jetson.GPIO as GPIO
    _gpio = GPIO
    if GPIO.getmode() not in (None, GPIO.BOARD):
        raise RuntimeError('L9110S 배선은 GPIO.BOARD 번호를 사용합니다.')
    GPIO.setmode(GPIO.BOARD)
    GPIO.setwarnings(True)
    try:
        # 모터 입력부터 LOW로 설정합니다. 33번은 디지털 GPIO 출력입니다.
        for pin in PINS:
            with warnings.catch_warnings(record=True) as notices:
                warnings.simplefilter('always')
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            _owned.append(pin)
            for notice in notices:
                message = str(notice.message)
                LOG.warning('GPIO 설정 안내: %s', message)
                # root가 아니면 /dev/mem을 못 읽어 pinmux 점검만 건너뜁니다(핀 동작과 무관).
                # 핀이 입력으로 잡혀 있다는 실제 pinmux 경고일 때만 중단합니다.
                if 'pinmux' in message.lower() and 'could not open /dev/mem' not in message.lower():
                    raise RuntimeError(f'BOARD {pin}번의 GPIO pinmux 설정 확인이 필요합니다.')
        _ready = True
    except Exception:
        try:
            cleanup()
        except Exception:
            LOG.exception('GPIO 초기화 실패 후 출력 해제 실패')
        raise


def _motor_low():
    """한 핀에 오류가 나더라도 다른 모터 입력의 LOW 적용도 시도합니다."""
    errors = []
    for pin in (A1A, A1B):
        if pin in _owned:
            try:
                _gpio.output(pin, _gpio.LOW)
            except Exception as exc:
                errors.append(exc)
    if errors:
        raise RuntimeError('L9110S 모터 정지 출력 적용 실패') from errors[0]


def _show_led(pin):
    """상태 LED 하나만 켜고 나머지는 끕니다."""
    for led in (RED, WARN, GREEN):
        _gpio.output(led, _gpio.HIGH if led == pin else _gpio.LOW)


def run():
    """사용자 코드: 초록 ON / A-1A HIGH / A-1B LOW."""
    with _lock:
        _initialize()
        try:
            _show_led(GREEN)
            _gpio.output(A1B, _gpio.LOW)
            _gpio.output(A1A, _gpio.HIGH)
        except Exception:
            _motor_low()
            raise
        return False  # 구동 명령 적용. 물리적 회전 확인을 뜻하지 않습니다.


def stop(led=RED):
    """사용자 코드: A-1A/1B LOW / 지정한 LED(기본 빨강) ON."""
    with _lock:
        _initialize()
        # LED 표시보다 모터 정지 출력을 먼저 적용합니다.
        _motor_low()
        _show_led(led)
        return True  # 정지 명령 적용. 물리적 정지 확인을 뜻하지 않습니다.


def apply_state(state):
    """비전 FSM에서 호출하는 인터페이스입니다. 경고부터 벨트를 정지합니다."""
    if state == 'NORMAL':
        return run()
    if state == 'WARNING':
        return stop(WARN)
    if state in ('DANGER', 'ERROR'):
        return stop(RED)
    raise ValueError('알 수 없는 안전 상태: ' + str(state))


def cleanup():
    """사용한 네 핀만 해제합니다. 모터/LED LOW 적용을 각각 시도합니다."""
    global _ready
    with _lock:
        if _gpio is None or not _owned:
            return None
        errors = []
        motor_owned = A1A in _owned and A1B in _owned
        for pin in list(_owned):
            try:
                _gpio.output(pin, _gpio.LOW)
            except Exception as exc:
                errors.append(exc)
        try:
            _gpio.cleanup(list(_owned))
        except Exception as exc:
            errors.append(exc)
        finally:
            _owned.clear()
            _ready = False
        if errors:
            raise RuntimeError('L9110S/LED 출력 해제 실패') from errors[0]
        return True if motor_owned else None
