import Jetson.GPIO as GPIO

RED = 31     # 빨간 LED
BLUE = 15    # 파란 LED
A1A = 33     # L9110 A-1A
A1B = 29     # L9110 A-1B

GPIO.setmode(GPIO.BOARD)
GPIO.setup([RED, BLUE], GPIO.OUT, initial=GPIO.LOW)
GPIO.setup([A1A, A1B], GPIO.OUT, initial=GPIO.LOW)


def run():
    """파란 LED 켜짐 + 벨트 작동"""
    GPIO.output(RED, GPIO.LOW)
    GPIO.output(BLUE, GPIO.HIGH)
    GPIO.output(A1A, GPIO.HIGH)
    GPIO.output(A1B, GPIO.LOW)


def stop():
    """빨간 LED 켜짐 + 벨트 정지"""
    GPIO.output(BLUE, GPIO.LOW)
    GPIO.output(RED, GPIO.HIGH)
    GPIO.output(A1A, GPIO.LOW)
    GPIO.output(A1B, GPIO.LOW)


try:
    run()
    print("b: 파란 LED + 벨트 작동 / r: 빨간 LED + 벨트 정지 / q: 종료")
    while True:
        key = input("> ").strip().lower()
        if key == "b":
            run()
        elif key == "r":
            stop()
        elif key == "q":
            break

except KeyboardInterrupt:
    pass
finally:
    GPIO.output(A1A, GPIO.LOW)
    GPIO.output(A1B, GPIO.LOW)
    GPIO.output(RED, GPIO.LOW)
    GPIO.output(BLUE, GPIO.LOW)
    GPIO.cleanup()
    print("종료")