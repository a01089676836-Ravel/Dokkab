# Dokkab — 비전 AI 기반 컨베이어 작업자 접근 감지 및 안전 알림 시스템

Jetson Orin Nano + USB 웹캠으로 작업자의 얼굴을 검출하고 거리를 추정한다. 가까워지면 경고하고, 위험 거리에서는 컨베이어 벨트를 멈춘다. 모든 상태 변경은 Aiven MySQL에 기록되고 HTML 대시보드에서 실시간으로 볼 수 있다.

| 상태 | 조건 | 벨트 | LED |
|---|---|---|---|
| 정상 | 얼굴 없음 또는 200cm 초과 | 작동 | 꺼짐 |
| 경고 | 50cm 초과 ~ 200cm 이하 | 작동 유지 | 노랑 |
| 위험 | 50cm 이하 | **정지** | 빨강 |

## 빠른 실행 (Jetson)

```bash
cd ~/Dokkab
conveyor_dashboard/.venv/bin/python conveyor_dashboard/launcher.py   # 종료 후 웹에서 다시 시작 가능
# 런처 없이 직접 실행: conveyor_dashboard/.venv/bin/python convey_vision.py
```

- 대시보드: Jetson에서 `http://127.0.0.1:5080`, 같은 Wi-Fi의 다른 기기에서 `http://<Jetson IP>:5080`
- 인터넷 공개: 다른 터미널에서 `conveyor_dashboard/start_tunnel.sh` 실행 → 출력된 `https://….trycloudflare.com` 주소로 접속. 보기는 로그인 없이 되고, 종료·DB 버튼만 `.env`의 `DASHBOARD_PASSWORD`를 묻는다
- 종료: `Ctrl+C`, 카메라 창 `q`, 또는 대시보드 "대시보드 종료" 버튼. 런처로 실행했다면 종료 버튼 뒤 새로고침 → [다시 시작]으로 다시 켠다

처음 설치, 배선, 자주 나는 오류는 [Jetson 실행 코드 설명](docs/04_Jetson_실행코드.md)에 있다.

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `convey_vision.py` | 메인 프로그램 (카메라 → 검출 → 판단 → LED·벨트 → DB → 대시보드) |
| `conveyor_dashboard/` | Flask 대시보드, Aiven DB 기록, HTML 화면 |
| `evaluate_model.py` | 성능 평가 (Accuracy, Precision/Recall/F1, FPS) |
| `yunet_distance_calibration.py` | 정답 거리 보정 데이터 수집 도구 |
| `RYG_face_distance_detection.py` | 원본 YuNet 거리 표시 코드 |
| `optimization/` | TensorRT 변환 스크립트·엔진·벤치마크 |
| `docs/` | 최종 제출 문서 |

## 제출 문서

| 문서 | 내용 |
|---|---|
| [프로젝트 계획서 (수정본)](docs/00_프로젝트계획서_수정본.md) | 센서·서보 제외 등 변경 반영 |
| [문제정의서](docs/01_문제정의서.md) | 현장 문제, 사용자, 입력·출력, 성공 기준 |
| [데이터셋 설명서](docs/02_데이터셋_설명서.md) | 사전학습 모델 정보, 운영 로그 수집·라벨 기준·분할 |
| [모델 평가서](docs/03_모델_평가서.md) | 공개 지표, 상태 안정성, 오탐·미탐 사례, 현장 정량 평가(Accuracy·F1·FPS) 방법 |
| [Jetson 실행 코드](docs/04_Jetson_실행코드.md) | 처리 흐름, 배선, 설치·실행 |
| [DB 구조](docs/05_DB_구조.md) | 테이블, 기록 시점, 조회 방식 |
| [통합 시연](docs/06_통합_시연.md) | AI 판단 → 장치 반응 → DB 기록 시나리오 |
| [결과보고서](docs/07_결과보고서.md) | 결과(영역별 포함), 문제 해결, 한계, 개선 방향 |
| [최적화 모델](docs/10_최적화_모델.md) | YuNet ONNX → TensorRT FP32·FP16 엔진 변환 절차 (`optimization/`) |
| [벤치마크 결과](docs/11_벤치마크_결과.md) | 최적화 전후 지연·FPS·CPU·메모리·결과 일치율 |

## 한계와 개선 방향 (요약)

- 얼굴이 보여야 검출된다 → 사람 전체 검출 모델과 함께 사용
- 거리는 얼굴 크기 기반 추정값이다 → 보정 데이터로 오차 측정·재보정
- TensorRT FP16 엔진은 1.23배 빠르고 CPU 사용을 558% → 65%로 줄였지만, 메인 프로그램에는 아직 적용하지 않았다 → 적용 후 남는 CPU를 추가 모델에 사용

## 팀

전종현(팀장, 아키텍처·FSM·대시보드·통합), 송태원(회로·보드 연결), 장호영(비전 AI·거리 측정)
