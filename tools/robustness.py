"""실험 1b: 강건성 실험 (docs/PAPER.md 6장). 실험 1의 결론이 가상 설비 설정과 판정 임계값에 좌우되지 않는지 본다.

실행: python tools/robustness.py [--seeds 10] [--out results/exp1b_robustness]
그룹
- A 모델 불일치: 습도 영향 비선형(Dolník et al., 2022 경향), 노이즈 2·4배 → 제안 모델 구조와 다른 데이터
- B 학습 기간: 날씨 습도 변동(±10%p) 아래 정상 구간 = 학습 기간 1·3·7일, 습도 선형·비선형
- C 상승 속도: gradual_rise 하루 0.25·1.0mA (기본 0.5)
- D 임계값 조사: 고정 임계값 주의선, 제안 방법 sigma_scale을 바꿔 "오경보 vs 탐지 시간" 관계
결과: runs.csv(재생 1회 = 1행) + meta.json → tools/make_figures.py가 표·그림으로 만든다
"""
import argparse
import copy
from datetime import timedelta
from pathlib import Path

from leakmon.config import load_settings
from leakmon.evaluation import METHODS, replay, save_results, summarize
from leakmon.scenario import SCENARIOS, Plant

AFTER_EVENT_DAYS = 13  # 이상 시작 뒤 재생 기간 (실험 1과 같게: 14일 - 정상 1일)
WEATHER = 10.0

# (그룹, 조건 이름, 설비 특성, 학습 기간(일))
CONDITIONS = [
    ("A", "기본", Plant(), 1),
    ("A", "습도 비선형", Plant(humidity_model="nonlinear"), 1),
    ("A", "습도 영향 1/4", Plant(humidity_scale=0.25), 1),
    ("A", "습도 영향 없음", Plant(humidity_scale=0.0), 1),
    ("A", "노이즈 2배", Plant(noise_mA=0.10), 1),
    ("A", "노이즈 4배", Plant(noise_mA=0.20), 1),
    *[("B", f"{model} 학습 {days}일", Plant(humidity_model=model, weather_pct=WEATHER, normal_days=days), days)
      for model in ("linear", "nonlinear") for days in (1, 3, 7)],
    ("C", "상승 0.25mA/일", Plant(rise_mA_per_day=0.25), 1),
    ("C", "상승 1.0mA/일", Plant(rise_mA_per_day=1.0), 1),
]
# D: 고정 임계값 주의선(mA) → alarm_mA = normal + (주의선 - normal) / T1. 기본 주의선 6.75mA
CAUTION_LINES_mA = (5.5, 6.0, 6.75, 7.5, 8.25, 9.0)
SIGMA_SCALES = (4, 6, 8, 10, 14, 20)  # 기본 10


def run_condition(group: str, name: str, plant: Plant, settings: dict, methods: tuple[str, ...], seeds: int,
                  param: str = "") -> list[dict]:
    """한 조건을 방식 × 시나리오 × seed로 재생. 신호는 (시나리오, seed)마다 한 번 만들어 방식끼리 공유한다."""
    from leakmon.scenario import generate
    days = plant.normal_days + AFTER_EVENT_DAYS
    rows_out = []
    for scenario in SCENARIOS:
        for seed in range(seeds):
            signal = generate(scenario, days, seed, plant=plant)
            for method in methods:
                r = summarize(scenario, replay(scenario, days, settings, method, seed, signal, plant), plant)
                delay_h = round(r["delay"] / timedelta(hours=1), 2) if r["delay"] is not None else ""
                rows_out.append({"group": group, "condition": name, "param": param, "method": method,
                                 "scenario": scenario, "seed": seed, "alarm": r["alarm"], "delay_h": delay_h,
                                 "tp": r["tp"], "fp": r["fp"], "fn": r["fn"]})
    return rows_out


def main(seeds: int, out_dir: Path) -> None:
    base = load_settings()
    runs: list[dict] = []
    for group, name, plant, learn_days in CONDITIONS:
        settings = copy.deepcopy(base)
        settings["ai"]["learn_days"] = learn_days
        runs += run_condition(group, name, plant, settings, METHODS, seeds)
        print(f"  {group} {name} 완료")

    b = base["baseline"]
    for line in CAUTION_LINES_mA:
        settings = copy.deepcopy(base)
        settings["baseline"]["alarm_mA"] = b["normal_mA"] + (line - b["normal_mA"]) / base["state"]["t1"]
        runs += run_condition("D", "임계값 조사", Plant(), settings, ("baseline",), seeds, f"주의선 {line}mA")
    for scale in SIGMA_SCALES:
        settings = copy.deepcopy(base)
        settings["ai"]["sigma_scale"] = scale
        runs += run_condition("D", "임계값 조사", Plant(), settings, ("linear",), seeds, f"sigma_scale {scale}")
    print("  D 임계값 조사 완료")

    conditions = {"experiment": "exp1b_robustness", "seeds": seeds, "after_event_days": AFTER_EVENT_DAYS,
                  "groups": {g: [n for gg, n, _, _ in CONDITIONS if gg == g] for g in "ABC"},
                  "plants": {n: vars(p) | {"learn_days": d} for _, n, p, d in CONDITIONS},
                  "caution_lines_mA": CAUTION_LINES_mA, "sigma_scales": SIGMA_SCALES}
    save_results(out_dir, runs, conditions, base)
    print(f"결과 저장: {out_dir}/runs.csv ({len(runs)}행), {out_dir}/meta.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="강건성 실험 (실험 1b)")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--out", type=Path, default=Path("results/exp1b_robustness"))
    args = parser.parse_args()
    main(args.seeds, args.out)
