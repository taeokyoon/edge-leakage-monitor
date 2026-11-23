"""3단계 작업 8: 방식 3종을 시나리오 7종 × seed 여러 개로 재생해 지표를 낸다 (SYS-17, docs/ARCHITECTURE.md 8장).

실행: python tools/evaluate.py [--seeds 10] [--days 14] [--out results/exp1_sil]
HIL (Pi에서): python tools/evaluate.py --hil [--out results/exp2_hil]
    같은 시나리오 값을 MCP4725 → 전선 → ADS1115로 통과시킨 측정값으로 똑같이 평가 (실험 2)
    → SIL 결과와 비교: python tools/compare_runs.py results/exp1_sil results/exp2_hil
HIL 재생 (PC에서): python tools/evaluate.py --hil-errors results/exp2_hil/dac_adc_errors.csv
    Pi가 기록한 (목표, 오차)로 Pi 측정값을 그대로 복원해 평가 → 방식을 추가해도 Pi를 다시 돌리지 않는다
    복원이 맞는지는 기존 방식의 결과가 Pi 결과(results/exp2_hil/runs.csv)와 같은지로 확인한다
결과: 화면 요약 + runs.csv(재생 1회 = 1행) + meta.json(커밋 ID·조건·설정) → 논문 실험 1·2 (docs/PAPER.md)
- 탐지율: 경보가 정답인 시나리오(gradual_rise, repeated_spike, sharp_rise)에서 경보가 뜬 비율
- 오경보율: 나머지 시나리오에서 경보가 뜬 비율 (humidity_rise = STK-02, short_spike = STK-03)
- 평균 탐지 시간: 이상 시작 → 첫 주의
- Precision·Recall·F1: 측정 하나하나를 "경보 상태 vs 정답 라벨"로 센 값
"""
import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from leakmon.config import load_settings
from leakmon.evaluation import METHODS, replay, save_results, summarize
from leakmon.scenario import ALARM_EXPECTED, SCENARIOS, generate

LABELS = {"baseline": "고정 임계값", "ewma": "EWMA 관리도 (학습 최대값 기준)",
          "ewma_std": "EWMA 관리도 (표준 λ=0.2, L=3)",
          "iforest": "Isolation Forest (학습 최대값 기준)", "iforest_std": "Isolation Forest (표준 s>0.5 기준)",
          "linear_nohum": "선형 회귀 + 잔차 (습도 제외, 제거 실험)", "linear": "선형 회귀 + 잔차 (제안)"}


def hours(td: timedelta) -> str:
    return f"{td / timedelta(hours=1):.1f}시간"


def count_missing(signals: dict) -> int:
    """누설전류 값이 없는(None) 샘플 수. HIL 측정 전후 차이 = 측정 실패 수."""
    return sum(r["leakage_mA"] is None for rows in signals.values() for r in rows)


def signals_from_pi_errors(signals: dict, errors_csv: Path, mA_per_V: float) -> None:
    """Pi 측정값 복원: 측정 = DAC 실제 출력(코드로 계산) + 기록된 오차. hil_signal()과 같은 순서로 기록돼 있다.

    측정 실패가 0회인 결과만 쓸 수 있다 (실패한 측정은 오차가 없거나 측정값이 버려져 순서가 어긋난다)
    """
    from leakmon.sensors.pi import DAC_VREF, mA_to_dac_code
    with open(errors_csv, encoding="utf-8") as f:
        pairs = [(float(r["target_mA"]), float(r["error_mA"])) for r in csv.DictReader(f)]
    i = 0
    for rows in signals.values():
        for r in rows:
            if r["leakage_mA"] is None:
                continue
            target, error = pairs[i]
            if abs(target - r["leakage_mA"]) > 1e-9:  # 순서가 어긋나면 다른 측정을 붙이게 된다
                raise ValueError(f"{i}번째 기록의 목표값 {target}이 신호 {r['leakage_mA']}와 다름 (조건·코드 불일치)")
            code = mA_to_dac_code(target, mA_per_V)
            r["leakage_mA"] = round(code / 4095 * DAC_VREF * mA_per_V + error, 3)
            i += 1
    if i != len(pairs):
        raise ValueError(f"기록 {len(pairs)}개 중 {i}개만 사용 (조건 불일치)")


def hil_signal(rows: list[dict], sensors, errors: list[tuple[float, float]]) -> list[dict]:
    """시나리오 값을 Pi DAC→ADC 경로로 통과시킨 측정값으로 바꾼다. 장치와 같은 규칙 (I2C 오류·자가진단 실패 = None).

    errors: (목표 mA, 오차 mA)를 쌓는다 → 출력 크기별 오차 그림 (논문 신호 경로 타당성)
    """
    out = []
    for r in rows:
        r = dict(r)
        if r["leakage_mA"] is not None:  # data_loss 끊김 구간은 그대로 None
            measured, error = sensors.dac_path(r["leakage_mA"])
            if error is not None:
                errors.append((r["leakage_mA"], error))
            r["leakage_mA"] = None if measured is None else round(measured, 3)
        out.append(r)
    return out


def main(seeds: int, days: float, out_dir: Path, hil: bool, hil_errors: Path | None = None) -> None:
    settings = load_settings()
    mode = "HIL (Pi DAC→ADC)" if hil else "HIL 재생 (Pi 측정값 복원)" if hil_errors else "SIL"
    print(f"{mode}: seed {seeds}개 × 시나리오 {len(SCENARIOS)}종 × {days}일 (가상 시간, 이상 시작 = 1일 00:00)\n")

    # 신호는 (시나리오, seed)마다 한 번만 만든다: 방식 3종이 같은 신호로 비교돼야 공정하고, HIL은 측정이 느리다
    signals = {(name, seed): generate(name, days, seed) for name in SCENARIOS for seed in range(seeds)}
    errors: list[tuple[float, float]] = []  # HIL: (목표, 측정 - 출력 오차) (DAC→ADC 오차 기록, 신호 경로 타당성)
    if hil:
        from leakmon.scenario import Scenario
        from leakmon.sensors.pi import PiSensors
        # dac_path만 쓰므로 시나리오는 자리 채우기용. 습도는 시나리오 합성값 그대로 (실측 습도는 재현 불가)
        sensors = PiSensors(Scenario("normal", datetime(2026, 11, 1)), settings["pi"])
        none_before = count_missing(signals)  # data_loss 시나리오의 원래 끊김 구간
        for i, key in enumerate(signals, 1):
            signals[key] = hil_signal(signals[key], sensors, errors)
            print(f"  측정 {i}/{len(signals)}: {key[0]} seed {key[1]}", end="\r")
        print()
        read_failures = count_missing(signals) - none_before  # I2C 오류·자가진단 실패 → 장치처럼 None 처리된 수

    if hil_errors:
        pi_meta = json.loads((hil_errors.parent / "meta.json").read_text(encoding="utf-8"))
        if pi_meta["conditions"].get("hil_read_failures") != 0:
            raise SystemExit("측정 실패가 있었던 HIL 결과는 복원할 수 없음")
        signals_from_pi_errors(signals, hil_errors, settings["pi"]["mA_per_V"])

    runs: list[dict] = []  # 결과 파일용 원자료: 방식 × 시나리오 × seed 1회 = 1행
    for method in METHODS:
        results = {name: [summarize(name, replay(name, days, settings, method, seed, signals[(name, seed)]))
                          for seed in range(seeds)]
                   for name in SCENARIOS}
        for name, rs in results.items():
            for seed, r in enumerate(rs):
                delay_h = round(r["delay"] / timedelta(hours=1), 2) if r["delay"] is not None else ""
                runs.append({"method": method, "scenario": name, "seed": seed, "alarm": r["alarm"],
                             "delay_h": delay_h, "tp": r["tp"], "fp": r["fp"], "fn": r["fn"]})
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

    conditions: dict = {"experiment": "exp2_hil" if hil else "exp2_hil_replay" if hil_errors else "exp1_sil",
                        "seeds": seeds, "days": days}
    if hil_errors:  # 측정값 출처: Pi 실행 정보
        conditions["source"] = {"file": str(hil_errors), "commit": pi_meta["commit"], "host": pi_meta["host"],
                                "run_at": pi_meta["run_at"]}
    if hil:
        abs_err = [abs(e) for _, e in errors]
        conditions["dac_adc_error_mA"] = {"count": len(errors), "mean_abs": round(sum(abs_err) / len(abs_err), 4),
                                          "max_abs": round(max(abs_err), 4)} if errors else None
        conditions["hil_read_failures"] = read_failures
        print(f"DAC→ADC 오차: {conditions['dac_adc_error_mA']}, 측정 실패 {read_failures}회")
    save_results(out_dir, runs, conditions, settings)
    if errors:  # 원자료: 오차 분포 그림용 (make_figures.py)
        with open(out_dir / "dac_adc_errors.csv", "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([("target_mA", "error_mA"), *errors])
    print(f"결과 저장: {out_dir}/runs.csv ({len(runs)}행), {out_dir}/meta.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="leakmon 방식별 평가")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--days", type=float, default=14)
    parser.add_argument("--out", type=Path, help="결과 저장 폴더 (기본 results/exp1_sil, --hil이면 results/exp2_hil)")
    parser.add_argument("--hil", action="store_true", help="Pi에서 실행: 신호를 실제 DAC→ADC로 통과시켜 평가")
    parser.add_argument("--hil-errors", type=Path, help="PC에서 Pi 측정값 복원 평가 (exp2_hil/dac_adc_errors.csv)")
    args = parser.parse_args()
    default_out = ("results/exp2_hil" if args.hil else
                   "results/exp2_hil_replay" if args.hil_errors else "results/exp1_sil")
    main(args.seeds, args.days, args.out or Path(default_out), args.hil, args.hil_errors)
