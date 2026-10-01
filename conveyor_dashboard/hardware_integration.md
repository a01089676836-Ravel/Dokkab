# L9110S 배선과 비전 상태 연결

## 반영한 입력

- 회로 그림: `reference/circuit_layout.png`. 사용자가 임시 그림이라고 설명했습니다.
- 실제 모터 드라이버: **L9110S**, 사용자 확인.
- 배선 기준: 사용자가 제공한 `Jetson.GPIO` 코드(`reference/L9110S_wiring_reference.txt`). 빨강 31, 파랑 15, A-1A 33, A-1B 29.
- 사용 모터: DC 벨트 모터. 33번은 모터 드라이버의 **디지털 입력**으로 사용합니다.

원그림에 표시된 `74HC00` 이름은 실제 부품 이름으로 사용하지 않습니다. LED 두 개와 모터 배치만 참고하고, 실제 핀 연결은 아래 사용자 배선 코드를 기준으로 합니다.

## 핀 연결

번호는 `GPIO.BOARD`, 즉 젯슨 40핀 헤더의 물리적 핀 번호입니다.

| 연결 대상 | BOARD 핀 | 코드 상수 |
|---|---:|---|
| 빨간 LED 제어 | 31 | RED |
| 파란 LED 제어 | 15 | BLUE |
| L9110S A-1A 입력 | 33 | A1A |
| L9110S A-1B 입력 | 29 | A1B |

- 모터의 두 선은 드라이버의 모터 출력 단자에 연결합니다.
- 젯슨 GND와 드라이버 GND는 공통으로 연결합니다.
- LED는 각각 전류 제한 저항을 포함합니다. 저항값과 극성은 실제 부품/배선으로 확인합니다.
- 드라이버·모터 전원과 3.3V 제어 신호의 입력 조건은 실제 사용 모듈의 정격을 따릅니다.
- 33번을 이전에 PWM으로 설정했다면 디지털 GPIO 출력으로 사용할 pinmux 상태를 확인합니다. 이 코드에서 PWM이나 서보 펄스는 생성하지 않습니다.

## 상태별 출력

| 비전 상태 | HTML 표시 | 빨간 LED(31) | 파란 LED(15) | A-1A(33) | A-1B(29) | 벨트 명령 |
|---|---|---|---|---|---|---|
| NORMAL | 정상, 초록 | LOW | HIGH | HIGH | LOW | 구동 |
| WARNING | 경고, 노랑 | LOW | HIGH | HIGH | LOW | 구동 유지 |
| DANGER | 위험, 빨강 | HIGH | LOW | LOW | LOW | 정지 |
| ERROR | 오류 | HIGH | LOW | LOW | LOW | 정지 |
| 프로그램 종료 | 연결 종료 | LOW | LOW | LOW | LOW | 출력 해제 |

파란 실물 LED는 제공한 `run()` 코드대로 정상·경고 모두 켜집니다. 정상과 경고의 구분은 HTML과 비전 상자에서 표시합니다.

## 연결 순서

`vision_runtime.SafetyController` → `team_hardware.apply_state()` → L9110S/LED 명령 → `aiven_logs` → HTML 로그입니다. HTML의 화면 시연 버튼은 장비를 구동하지 않습니다.

`apply_state()`가 정지 출력을 성공적으로 적용하면 `True`를 반환합니다. 이때 DB에 `equipment_stopped=True`와 해당 정지 이유가 기록됩니다. GPIO 명령 오류가 발생하면 정지 완료로 기록하지 않습니다. 정지 여부 필드는 명령 적용 결과이며 모터의 실제 회전·정지 센서 피드백은 아닙니다.

## 하드웨어를 연결해 실행할 때

실제 배선·전원·GPIO 설정을 확인한 뒤 젯슨 프로젝트 폴더에서 실행합니다.

```bash
source .venv/bin/activate
python -m pip install -r requirements-hardware.txt
python stop_jetson.py
python app.py --vision --camera 0 --hardware-module team_hardware
```

하드웨어 모드를 켜면 얼굴이 정상·경고 범위로 검출될 때 벨트 구동 명령이 적용됩니다. 위험 접근과 얼굴 미검출·카메라 오류는 정지 명령으로 연결됩니다. 종료 시 모터와 LED 출력을 해제합니다.

현재 설치에서는 하드웨어 모듈을 활성화하지 않았습니다. 이번 변경으로 실제 모터를 구동하지 않았습니다.

## 근거

- [NVIDIA Jetson.GPIO](https://github.com/NVIDIA/jetson-gpio): BOARD 번호, output/cleanup API, Orin pinmux 안내.
- [TI SN74HC00](https://www.ti.com/lit/ds/symlink/sn74hc00.pdf): 임시 그림의 74HC00이 NAND 논리 IC인 것을 확인하는 자료.
- L9110S 선택과 네 핀 번호는 사용자의 최신 확인 및 배선 코드가 기준입니다.
