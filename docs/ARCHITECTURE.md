# 시스템 아키텍처 — leakmon

> 이 문서는 `docs/ARCHITECTURE.md`에 둔다. 프로젝트 맥락과 확정 결정은 루트의 `DEVELOP.md`를 따른다.

## 1. 전체 구성

```
┌──────────────────────── Raspberry Pi 4B (단일 디바이스) ────────────────────────┐
│                                                                                 │
│  [입력 계층]                                                                     │
│   MCP4725 DAC ──(A1)──┐                                                          │
│   가변저항 ─────(A2)──┼── ADS1115 ── I2C ──┐                                     │
│   CT 모듈(선택)─(A0)──┘                    │                                     │
│   DHT20 ───────────────────────── I2C ─────┤                                     │
│   버튼 3개 ─────────────────────── GPIO ───┤                                     │
│                                            ▼                                     │
│  [디바이스 에이전트]  sensors → features → ai → state → storage(SQLite)           │
│                                                   │                              │
│                                   ┌───────────────┼──────────────┐               │
│                                   ▼               ▼              ▼               │
│                              OLED·LED       MQTT publish     (scenario:          │
│                              로컬 표시      (TLS, ID/PW)      DAC 출력 제어)     │
│                                                   │                              │
│  [브로커]  Mosquitto (TLS 8883, 비밀번호, 토픽 ACL)                                │
│                                                   │                              │
│  [웹 서버]  FastAPI ── MQTT subscribe ── WebSocket push ── SQLite 이력 조회        │
│             (점검권고 ack: 브라우저 → FastAPI → MQTT cmd 발행 → 디바이스)         │
└───────────────────────────────────────────────────┬─────────────────────────────┘
                                                    │ Wi-Fi
                                         휴대폰 / PC 브라우저 (대시보드)

[PC 개발 환경]  LEAKMON_HW=sim — 모든 하드웨어를 시뮬레이터로 대체해 동일 코드 실행
[가상 설비]     tools/virtual_devices.py — 다른 라인 설비 여러 대가 MQTT로 발행 (배치도 채우기)
```

## 2. 데이터 흐름

1. 신호 생성: 시나리오 생성기가 MCP4725로 가상 센서 전압을 출력하거나, 가변저항을 수동 조작한다.
2. 측정: ADS1115(A1 또는 A2)와 DHT20을 주기적으로 읽어 1 샘플 생성 (가상 시간 타임스탬프 포함).
3. 특징 추출: 최근 윈도우의 평균, 변동성, 기울기, 습도 등.
4. AI 판정: 습도·시간대로 예상 누설전류를 예측 → 실제값과의 잔차 → 잔차의 추세로 이상 점수 산출.
5. 상태 결정: 정상 / 주의 / 점검권고 / 센서장애 (지속 시간 확인 후 확정).
6. 저장: SQLite에 원본값·판정·알림 기록.
7. 로컬 표시: OLED·LED 갱신.
8. 발행: MQTT로 telemetry(주기), alert(상태 변화 시) 발행.
9. 웹: FastAPI가 구독 → WebSocket으로 브라우저에 즉시 전달.
10. 수동 확인(ack): 대시보드의 확인 버튼 → FastAPI가 `cmd` 토픽에 발행 → 디바이스가 수신해 상태 머신에 반영.

## 3. 상태 머신

```
          이상점수 > T1 (지속 D1)          이상점수 > T2 (지속 D2)
  정상 ─────────────────────────▶ 주의 ─────────────────────────▶ 점검권고
   ▲  ◀──────── 회복 (지속 R) ───────┘                                │
   │                                                                │
   └──────────────── 수동 확인(ack, 웹 대시보드 → cmd 토픽) ──────────┘

  어느 상태에서든: 데이터 미수신 > N초 → 센서장애 (복구 시 이전 판정 재개)
```
- ack는 웹 대시보드에서만 받는다 (물리 버튼 없음). 디바이스는 `cmd` 토픽으로 수신한다.
- ack 후에도 이상 점수가 T1을 넘으면 정상을 거쳐 다시 주의로 전이된다 (지속 시간 D1 재적용).

T1, T2, D1, D2, R, N은 `config/settings.yaml`에서 정의한다.

## 4. MQTT 설계

| 토픽 | 발행자 | 내용 |
|---|---|---|
| `factory/leakage/{device_id}/telemetry` | 디바이스 | 주기적 측정·점수 |
| `factory/leakage/{device_id}/alert` | 디바이스 | 상태 변화 (주의, 점검권고, 센서장애, 회복) |
| `factory/leakage/{device_id}/status` | 디바이스 | 온라인/오프라인 (LWT) |
| `factory/leakage/{device_id}/cmd` | 웹 서버 | 디바이스 명령 (현재는 점검권고 ack만) |

- 사용자명 = `device_id` (예: `press-01`), 웹 서버는 별도 계정 (예: `web`)
- ACL (최소 권한)
  - 디바이스: `factory/leakage/<자기 device_id>/telemetry`, `/alert`, `/status` 발행 + `/cmd` 구독만 가능
  - 웹 서버: `factory/leakage/#` 구독 + `factory/leakage/+/cmd` 발행만 가능 (telemetry·alert 위조 불가)
  - 가상 설비(`tools/virtual_devices.py`)도 설비별 계정·ACL을 동일 규칙으로 등록한다
- 보안 시연: (1) 자격증명 없는 클라이언트 연결 실패 (2) `press-01`이 다른 설비 토픽에 발행 → 대시보드 미도착 + 브로커 로그의 거부 기록

### 페이로드 예시
```json
// telemetry
{
  "device_id": "press-01",
  "ts_virtual": "2026-11-03T14:00:00",
  "time_scale": 1440,
  "leakage_mA": 4.8,
  "humidity_pct": 62.1,
  "expected_mA": 4.5,
  "anomaly_score": 0.21,
  "state": "normal",
  "source": "dac"
}

// alert
{
  "device_id": "press-01",
  "location": {"building": "A동", "line": "2번 라인", "equipment": "프레스-01"},
  "state": "inspect",
  "message": "정상 운전 패턴 대비 누설전류 상승 추세 감지. 절연 열화 의심 — 현장 점검 권고.",
  "ts_virtual": "2026-11-21T09:00:00",
  "time_scale": 1440
}

// cmd (웹 서버 → 디바이스)
{
  "command": "ack",
  "requested_by": "dashboard",
  "ts_real": "2026-11-21T15:30:12"
}
```

## 5. 설정 파일

`config/devices.yaml` (자산 정보)
```yaml
- device_id: press-01
  building: A동
  line: 2번 라인
  equipment: 프레스-01
  map_xy: [120, 80]   # 대시보드 배치도 좌표
```

`config/settings.yaml` (동작 파라미터): 샘플 주기, 배속, 상태 임계값·지속 시간, MQTT 주소, 하드웨어 백엔드 등.

## 6. AI 접근

- 1차(MVP): 정상 데이터로 "예상 누설전류" 회귀 모델 학습 (입력: 습도, 시간대, 운전 상태) → 잔차의 이동 평균·기울기로 이상 점수.
- 비교 기준: 고정 임계값 방식 (`ai/baseline.py`). 동일 시나리오로 나란히 평가.
- 선택 비교: Isolation Forest (튀는 점 탐지에 강하고 완만한 상승에 약한 특성을 실험으로 확인).
- 학습은 PC에서, 추론은 Pi에서 저장된 모델로 실행.

## 7. 시연 시나리오 (DAC 기준, 평가 데이터와 동일)

| # | 시나리오 | 기대 결과 |
|---|---|---|
| 1 | 정상 운전 | 경보 없음 |
| 2 | 완만한 상승 | AI가 고정 임계값보다 먼저 감지 |
| 3 | 습도 상승에 따른 합성 정상 변동 | AI 경보 없음, 고정 임계값은 경보 |
| 4 | 짧은 순간 급등 | 지속 시간 확인으로 억제 |
| 5 | 지속적 급상승 | 점검권고 |
| 6 | 데이터 끊김 | 센서장애 |
| 7 | 인증 없는 장치 / 타 토픽 발행 | 연결 실패 / 발행 거부 |

## 8. 평가 지표

탐지율·Precision·Recall·F1, 정상 데이터 오경보율, 고정 임계값 대비 조기 탐지 시간·오경보 감소율,
감지→대시보드 표시 지연, Pi CPU·메모리 사용량, (선택) CT 보정 곡선.

## 9. 폴더 구조 (초안 — Claude Code가 검토 후 확정)

```
leakmon/
├── DEVELOP.md
├── README.md
├── pyproject.toml
├── .gitignore
├── docs/
│   └── ARCHITECTURE.md
├── config/
│   ├── devices.yaml
│   └── settings.yaml
├── src/leakmon/
│   ├── main.py              # 디바이스 에이전트 진입점
│   ├── sensors/             # ADS1115, DHT20, MCP4725 + sim 백엔드
│   ├── scenario/            # 시나리오 생성기 (배속, 습도 반영 합성 변동)
│   ├── ai/                  # features, model, baseline
│   ├── state.py             # 상태 머신
│   ├── storage.py           # SQLite
│   ├── mqtt_client.py       # 발행 + cmd 구독 (ack 수신)
│   └── local_ui/            # OLED, LED, 버튼
├── web/
│   ├── app.py               # FastAPI + WebSocket
│   └── static/              # index.html, app.js, 배치도
├── broker/
│   ├── mosquitto.conf
│   ├── acl.example
│   └── make_certs.sh        # 자체 서명 CA·서버 인증서 생성 (키는 커밋 금지)
├── tools/
│   ├── gen_dataset.py       # 합성 시나리오 데이터셋 생성
│   ├── evaluate.py          # 지표 산출 (AI vs baseline)
│   └── virtual_devices.py   # 가상 설비 발행기
├── tests/
├── data/                    # gitignore
└── models/                  # gitignore (학습된 모델)
```
