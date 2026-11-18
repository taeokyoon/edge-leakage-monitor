#!/usr/bin/env bash
# Pi에서 논문 실험 2·3을 한 번에 실행한다 (docs/PAPER.md 6장). 사람 실수(순서·옵션 빠짐)를 줄이려고 한 스크립트로 묶음.
# 준비: 저장소 루트에서 pip install -e .[pi], 배선 = MCP4725 VOUT ↔ ADS1115 A1, I2C 활성화
# 실행: bash tools/pi_experiments.sh        (실험 2는 DAC→ADC 측정 약 6만 회라 20~30분 걸림)
# 결과: results_pi_<날짜>.tgz → PC로 옮겨 저장소 루트에서 압축 해제 → python tools/make_figures.py
set -euo pipefail
PY=${PY:-python}

echo "[1/4] 사전 점검: DAC→ADC 경로 (2·5·10mA 출력 → 측정)"
"$PY" - <<'EOF'
from datetime import datetime
from leakmon.config import load_settings
from leakmon.scenario import Scenario
from leakmon.sensors.pi import PiSensors

sensors = PiSensors(Scenario("normal", datetime(2026, 11, 1)), load_settings()["pi"])
for mA in (2.0, 5.0, 10.0):
    measured, error = sensors.dac_path(mA)
    if measured is None:
        raise SystemExit(f"  {mA}mA: 실패 (오차 {error}) → 배선·I2C 확인 후 다시 실행")
    print(f"  {mA}mA → 측정 {measured:.3f}mA (오차 {error:+.4f}mA)")
EOF

echo "[2/4] 실험 2: HIL 평가"
"$PY" tools/evaluate.py --hil

echo "[3/4] SIL↔HIL 비교"
"$PY" tools/compare_runs.py results/exp1_sil results/exp2_hil | tee results/exp2_hil/compare.txt

echo "[4/4] 실험 3: 처리 성능"
"$PY" tools/bench.py --out results/bench_pi.json

ARCHIVE="results_pi_$(date +%Y%m%d_%H%M).tgz"
tar czf "$ARCHIVE" results/exp2_hil results/bench_pi.json
echo "완료: $ARCHIVE 를 PC 저장소 루트로 옮겨 'tar xzf $ARCHIVE' 후 python tools/make_figures.py"
