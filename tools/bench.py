"""처리 성능 측정 (논문: 온디바이스 AI 주장 근거, DEVELOP.md 5단계 남은 일 3).

실행 (Pi에서): python tools/bench.py [--repeat 20] [--out results/bench_pi.json]
측정 대상 = 장치가 실제로 하는 일
- 학습 1회: 학습 기간(learn_days) 정상 데이터로 선형 회귀 / Isolation Forest 학습
- 판정 1회: 측정 1개 → 점수 → 상태 머신 갱신 (24분마다 1번 하는 일)
- 학습 7일분: 강건성 실험(실험 1b)에서 습도 범위를 덮으려면 학습을 늘려야 함 → 늘려도 장치에서 가능한지
- 최대 메모리(RSS): Pi 4B 2GB에서 다른 서비스(브로커·웹)와 같이 돌 여유가 있는지
- (Pi에서만) 센서 1회: DAC 출력 → ADC 측정 (장치가 측정마다 하는 I2C 왕복)
반복 측정해 중앙값·최대를 남긴다 (첫 실행은 import·캐시 때문에 느릴 수 있어 중앙값을 대표값으로 씀)
"""
import argparse
import json
import platform
import resource
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

from leakmon.ai.iforest import train_scorer
from leakmon.ai.model import residual_score, train
from leakmon.config import load_settings
from leakmon.scenario import generate
from leakmon.state import StateMachine


def timed_ms(fn, repeat: int) -> dict:
    """fn을 repeat번 실행한 시간(ms)의 중앙값·최대."""
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    return {"median_ms": round(statistics.median(times), 3), "max_ms": round(max(times), 3), "repeat": repeat}


def pi_sensors(settings: dict):
    """Pi(I2C 센서 연결)면 PiSensors, PC면 None. smbus2가 없거나 I2C 버스가 없으면 PC로 본다."""
    try:
        from leakmon.scenario import Scenario
        from leakmon.sensors.pi import PiSensors
        return PiSensors(Scenario("normal", datetime(2026, 11, 1)), settings["pi"])
    except (ImportError, OSError):
        return None


def main(repeat: int, out: Path) -> None:
    settings = load_settings()
    rows = generate("normal", days=2, seed=0)
    learn_end = rows[0]["ts"] + timedelta(days=settings["ai"]["learn_days"])
    learning = [r for r in rows if r["ts"] < learn_end and r["leakage_mA"] is not None]
    sample = next(r for r in rows if r["ts"] >= learn_end)  # 학습 뒤 첫 측정 1개
    model, scale = train(learning), settings["ai"]["sigma_scale"]
    iforest = train_scorer(learning, settings["state"]["t1"])
    machine = StateMachine.from_settings(settings["state"])

    def judge_linear() -> None:
        score = residual_score(model, sample["leakage_mA"], sample["humidity_pct"], sample["ts"], scale)
        machine.update(sample["ts"], score)

    def judge_iforest() -> None:
        machine.update(sample["ts"], iforest([sample])[0])

    week = generate("normal", days=7, seed=0)  # 학습 7일분 (420개)

    result = {
        "host": platform.node(), "machine": platform.machine(), "python": platform.python_version(),
        "learning_samples": len(learning),
        "train_linear": timed_ms(lambda: train(learning), repeat),
        "train_iforest": timed_ms(lambda: train_scorer(learning, settings["state"]["t1"]), repeat),
        "judge_linear": timed_ms(judge_linear, repeat),
        "judge_iforest": timed_ms(judge_iforest, repeat),
        "learning_samples_7d": len(week),
        "train_linear_7d": timed_ms(lambda: train(week), repeat),
        "train_iforest_7d": timed_ms(lambda: train_scorer(week, settings["state"]["t1"]), repeat),
    }
    sensors = pi_sensors(settings)
    if sensors is not None:
        result["sensor_dac_adc"] = timed_ms(lambda: sensors.dac_path(5.0), repeat)
    # ru_maxrss: 리눅스는 KB 단위. 이 프로세스(파이썬 + numpy + sklearn + 모델)가 쓴 최대 메모리
    result["peak_rss_MB"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"저장: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="학습·판정 처리 시간 측정")
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--out", type=Path, default=Path("results/bench.json"))
    args = parser.parse_args()
    main(args.repeat, args.out)
