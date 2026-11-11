# edge-leakage-monitor

설비의 누설전류 추세를 감시해 정상 / 주의 / 점검권고 / 센서장애를 판정하고, 웹 대시보드로 보여 주는 시스템 (저전압 PoC)

- 구조: 디바이스 에이전트(센서 → 판정 → 저장 → MQTT 발행) → 보안 브로커(TLS) → 웹 서버(대시보드)
- 현재 단계: PC에서 가상 센서로 실행 (실제 하드웨어 연결은 5단계)
- 상세 문서: `DEVELOP.md`(진행 기록), `docs/SRS.md`(요구사항), `docs/TEST.md`(검증 기록), `docs/ARCHITECTURE.md`(설계)

## 1. 준비 (최초 1회)

Linux 또는 WSL(Ubuntu) 기준, 저장소 루트(`edge-leakage-monitor/`)에서 실행한다.

1. 프로그램 설치 (Python 3.11 이상, Mosquitto 브로커, openssl)
   ```bash
   sudo apt install python3-venv mosquitto openssl
   ```
2. 저장소 받기
   ```bash
   git clone https://github.com/taeokyoon/edge-leakage-monitor.git
   cd edge-leakage-monitor
   ```
3. 가상환경 만들고 패키지 설치
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   ```
4. 인증서와 계정 만들기
   ```bash
   bash broker/setup.sh
   ```
   - 만들어지는 파일(`broker/certs/`, `broker/passwd`, `config/secrets.yaml`)은 비밀이라 git에 올라가지 않는다
   - 이미 있으면 그대로 둔다 (다시 실행해도 안전)

## 2. 실행 (터미널 3개)

세 터미널 모두 저장소 루트에서 실행한다. 순서대로 띄운다.

1. 터미널 1: 보안 브로커 (TLS 8883 포트, 계정·권한 검사)
   ```bash
   mosquitto -c broker/mosquitto.conf
   ```
2. 터미널 2: 웹 서버
   ```bash
   .venv/bin/uvicorn web.app:app --host 0.0.0.0 --port 8000
   ```
3. 터미널 3: 디바이스 에이전트. 아래 두 모드 중 하나를 고른다
   - 자동 모드: 시나리오 생성기가 값을 넣는다 (판정은 AI)
     ```bash
     .venv/bin/python -m leakmon.main --scenario sharp_rise
     ```
   - 수동 모드: 내가 터미널에 입력한 값을 넣는다 (판정은 고정 임계값)
     ```bash
     .venv/bin/python -m leakmon.main --manual
     ```
4. 브라우저에서 `http://localhost:8000` 접속

종료: 각 터미널에서 `Ctrl+C`

## 3. 자동 모드: 시나리오

`--scenario` 뒤에 아래 이름 중 하나를 넣는다 (생략하면 `normal`).

- 시간: 실제 1초 = 가상 24분 (시연 1분 = 가상 1일)
- 처음 가상 1일(실제 약 1분)은 AI 학습 기간이라 화면에 "학습 중"으로 표시되고 판정하지 않는다
- 이상 현상은 시작 후 가상 1일부터 나타난다

1.
   - 이름 - `normal`
   - 내용 - 정상 운전
   - 기대 결과 - 정상 유지
2.
   - 이름 - `gradual_rise`
   - 내용 - 누설전류가 천천히 상승
   - 기대 결과 - 주의 → 점검권고
3.
   - 이름 - `humidity_rise`
   - 내용 - 습도가 올라 누설전류가 정상 범위 안에서 변동
   - 기대 결과 - 정상 유지 (오경보가 나면 안 됨)
4.
   - 이름 - `short_spike`
   - 내용 - 짧은 순간 급등 1회
   - 기대 결과 - 정상 유지 (순간 튐으로 기록만)
5.
   - 이름 - `repeated_spike`
   - 내용 - 순간 급등 반복
   - 기대 결과 - 24시간 안에 3회 이상이면 주의
6.
   - 이름 - `sharp_rise`
   - 내용 - 지속적인 급상승
   - 기대 결과 - 주의 → 점검권고
7.
   - 이름 - `data_loss`
   - 내용 - 센서 데이터 끊김
   - 기대 결과 - 센서장애, 데이터가 돌아오면 이전 상태로 복귀

점검권고 뒤 "수리 → 회복"까지 보기 (시험용 옵션)

```bash
.venv/bin/python -m leakmon.main --scenario sharp_rise --repair-on-ack
```

- 옵션 없이 실행: 대시보드에서 ack를 눌러도 시나리오 값은 높은 그대로라 정상 → 주의 → 점검권고가 다시 나온다 (수리하지 않고 확인만 한 경우, 의도된 동작)
- 옵션을 붙여 실행: ack를 받으면 수리했다고 가정하고 시나리오의 이상 성분을 없앤다 → 정상 유지
- 이 옵션은 시험 장비 쪽 기능이다. 제품 화면에 "수리" 버튼을 두지 않는 이유: 실제 설비에서는 측정값을 정상으로 덮어써 진짜 누설을 숨기게 된다

## 4. 수동 모드: 값을 직접 넣어 상태 바꾸기

터미널 3에 값을 입력하고 Enter. 입력한 값은 다음 입력 전까지 계속 측정값으로 쓰인다.

- 입력 형식
  - `7.0` → 누설전류 7.0mA (습도는 그대로)
  - `7.0 80` → 누설전류 7.0mA, 습도 80%
  - `x` → 데이터 끊김
- 판정 기준: 점수 = (입력값 − 4.5) ÷ 4.5
  - 6.75mA 초과 (점수 0.5 초과) → 주의 기준
  - 9.0mA 초과 (점수 1.0 초과) → 점검권고 기준

따라 해 보기 (각 단계에서 표시된 시간만큼 기다린다)

1.
   - 입력 - `7.0`
   - 기다릴 시간 - 약 6초 (가상 120분)
   - 기대 결과 - 정상 → 주의
2.
   - 입력 - `4.5`
   - 기다릴 시간 - 약 6초 (가상 120분)
   - 기대 결과 - 주의 → 정상
3.
   - 입력 - `7.0`, 주의가 되면 `9.5`
   - 기다릴 시간 - 약 11초 (가상 240분)
   - 기대 결과 - 주의 → 점검권고
4.
   - 입력 - `4.5`
   - 기다릴 시간 - 아무리 기다려도
   - 기대 결과 - 점검권고 유지 (값이 내려가도 자동으로 풀리지 않음)
5.
   - 입력 - 대시보드의 "점검 확인 (ack)" 버튼 클릭
   - 기다릴 시간 - 바로
   - 기대 결과 - 점검권고 → 정상
6.
   - 입력 - `x`
   - 기다릴 시간 - 약 3초 (가상 60분)
   - 기대 결과 - 센서장애
7.
   - 입력 - `4.5`
   - 기다릴 시간 - 바로
   - 기대 결과 - 센서장애 직전 상태로 복귀

## 5. 검사 (CI와 같은 명령)

GitHub에 push하면 `.github/workflows/ci.yml`이 아래를 자동으로 실행한다. 로컬에서도 같은 명령으로 확인할 수 있다.

```bash
.venv/bin/ruff check .                                   # 정적 분석: 코드 규칙, 보안 위험 패턴
.venv/bin/mypy src                                       # 정적 분석: 타입 오류
.venv/bin/pytest --cov=leakmon --cov=web                 # 자동 테스트 + 분기 커버리지 (전체 하한선 49%)
.venv/bin/coverage report --include=src/leakmon/state.py --fail-under=100   # 판정 로직 100% 확인
```

추가 확인 (선택)

```bash
.venv/bin/pytest -v -s tests/test_security.py   # 보안 시연: 브로커(터미널 1)가 떠 있을 때만 실행됨
.venv/bin/python tools/evaluate.py              # AI 방식 3종 성능 비교 (시나리오 7개 × seed 10개)
```

## 6. 자주 나는 문제

1.
   - 증상 - `ModuleNotFoundError: No module named 'paho'`
   - 원인 - 가상환경이 아닌 시스템 Python으로 실행함
   - 해결 - 명령 앞에 `.venv/bin/`을 붙이거나 `source .venv/bin/activate` 후 실행
2.
   - 증상 - `config/secrets.yaml`이 없다는 오류
   - 원인 - 계정을 아직 만들지 않음
   - 해결 - `bash broker/setup.sh` 실행
3.
   - 증상 - 브로커 로그에 `SSL routines::http request`
   - 원인 - 암호화(TLS) 포트 8883에 일반 연결(평문)이나 브라우저가 접속함
   - 해결 - 장치와 웹 서버는 설정대로 TLS로 접속하므로 무시해도 됨. 브라우저 주소는 브로커가 아니라 웹 서버(8000)
4.
   - 증상 - 보안 테스트 7개가 `skipped`
   - 원인 - 브로커가 떠 있지 않음
   - 해결 - 터미널 1에서 브로커를 띄운 뒤 다시 실행
