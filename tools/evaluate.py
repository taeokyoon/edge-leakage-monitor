"""3단계 작업 8: 방식 3종을 시나리오 7종 × seed 여러 개로 재생해 지표를 낸다 (SYS-17, docs/ARCHITECTURE.md 8장).

실행: python tools/evaluate.py [--seeds 10] [--days 14]
- 탐지율: 경보가 정답인 시나리오(gradual_rise, repeated_spike, sharp_rise)에서 경보가 뜬 비율
- 오경보율: 나머지 시나리오에서 경보가 뜬 비율 (humidity_rise = STK-02, short_spike = STK-03)
- 평균 탐지 시간: 이상 시작 → 첫 주의
- Precision·Recall·F1: 측정 하나하나를 "경보 상태 vs 정답 라벨"로 센 값
"""
import argparse
from datetime import timedelta

from leakmon.config import load_settings
from leakmon.evaluation import METHODS, replay, summarize
from leakmon.scenario import ALARM_EXPECTED, SCENARIOS

LABELS = {"baseline": "고정 임계값", "iforest": "Isolation Forest", "linear": "선형 회귀 + 잔차 (제안)"}


def hours(td: timedelta) -> str:
    return f"{td / timedelta(hours=1):.1f}시간"


def main(seeds: int, days: float) -> None:
    settings = load_settings()
    print(f"seed {seeds}개 × 시나리오 {len(SCENARIOS)}종 × {days}일 (가상 시간, 이상 시작 = 1일 00:00)\n")
    for method in METHODS:
        results = {name: [summarize(name, replay(name, days, settings, method, seed)) for seed in range(seeds)]
                   for name in SCENARIOS}
        print(f"■ {LABELS[method]}")
        for name, rs in results.items():
            alarms = sum(r["alarm"] for r in rs)
            if name in ALARM_EXPECTED:
                delays = [r["delay"] for r in rs if r["delay"] is not None]
                avg = hours(sum(delays, timedelta()) / len(delays)) if delays else "-"
                print(f"  {name:15} 탐지 {alarms}/{seeds}  평균 탐지 시간 {avg}")
            else:
                print(f"  {name:15} 오경보 {alarms}/{seeds}")
        tp, fp, fn = (sum(r[k] for rs in results.values() for r in rs) for k in ("tp", "fp", "fn"))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        print(f"  Precision {precision:.2f}  Recall {recall:.2f}  F1 {f1:.2f}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="leakmon 방식별 평가")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--days", type=float, default=14)
    args = parser.parse_args()
    main(args.seeds, args.days)
