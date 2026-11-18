#!/usr/bin/env bash
# 실험 4: 보안 기능 검증 (docs/PAPER.md 6장). 보안 브로커를 띄우고 공격·대조 케이스 7개를 돌려 결과를 파일로 남긴다.
# 실행 (저장소 루트, broker/setup.sh로 인증서·계정을 만든 뒤): bash tools/exp4_security.sh
# 결과: results/exp4_security/ junit.xml(케이스별 합격) + pytest.log(케이스별 브로커 응답) + broker.log(거부 기록) + meta.txt
set -euo pipefail
OUT=results/exp4_security
mkdir -p "$OUT"
PY=${PY:-.venv/bin/python}

if ss -ltn 2>/dev/null | grep -q ':8883 '; then
  echo "8883 포트를 이미 쓰는 브로커가 있음 → 멈추고 다시 실행 (거부 기록을 이 실행의 로그로 남기기 위함)"; exit 1
fi
mosquitto -c broker/mosquitto.conf > "$OUT/broker_full.log" 2>&1 &
BROKER=$!
trap 'kill $BROKER 2>/dev/null || true' EXIT
for _ in $(seq 20); do ss -ltn | grep -q ':8883 ' && break; sleep 0.2; done

# -s: 케이스마다 브로커 응답(print)을 로그에 남긴다. 건너뜀이 있으면 브로커 문제라 실패로 본다
"$PY" -m pytest tests/test_security.py -v -s -rs --junitxml="$OUT/junit.xml" | tee "$OUT/pytest.log"
if grep -q 'skipped=\"[1-9]' "$OUT/junit.xml"; then echo "건너뛴 케이스 있음 → 브로커 확인"; exit 1; fi

kill $BROKER; wait $BROKER 2>/dev/null || true
# 논문 근거가 되는 줄만: 접속 거부, 발행 거부, TLS 오류
grep -E "Denied|not authorised|Client .* disconnected|OpenSSL Error|protocol error" "$OUT/broker_full.log" > "$OUT/broker.log" || true
rm "$OUT/broker_full.log"
{
  echo "commit: $(git rev-parse HEAD)"
  echo "uncommitted_changes: $([ -n "$(git status --porcelain --untracked-files=no -- ':(top)' ':(top,exclude)results')" ] && echo true || echo false)"
  echo "run_at: $(date -Iseconds)"
  echo "host: $(hostname)"
  echo "mosquitto: $(mosquitto -h 2>/dev/null | head -1)"
} > "$OUT/meta.txt"
echo "결과 저장: $OUT/"
