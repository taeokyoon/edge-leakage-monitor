"""논문 그림·표 생성 (docs/PAPER.md 6장). 결과 파일(runs.csv·meta.json·bench json)만 읽어 숫자를 다시 계산한다.

실행: python tools/make_figures.py [--out results/paper]
입력 (있는 것만 사용)
- results/exp1_sil/   실험 1 (필수) → 시나리오·지표 표, 통계 검정 표, 탐지 시간·시계열 그림
- results/exp1b_robustness/ 실험 1b → 강건성 표, 임계값 조사(오경보 vs 탐지 시간) 그림
- results/exp2_hil/   실험 2 (Pi 원본) → DAC→ADC 오차 그림, 일치표 (재생 결과가 없을 때)
- results/exp2_hil_replay/ 실험 2 재생 (Pi 측정값 복원, 방식 추가분 포함) → SIL↔HIL 일치표
- results/bench_pi.json 실험 3 → 처리 시간·메모리 표
- results/exp4_security/ 실험 4 → 보안 케이스 표
출력: 표 = CSV(UTF-8 BOM, 한글 Word·엑셀에서 바로 열림), 그림 = PNG 300dpi
"""
import argparse
import csv
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 화면 없이 파일로만 저장
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from scipy.stats import wilcoxon  # noqa: E402

from compare_runs import compare, load  # noqa: E402  (tools/ 폴더의 기존 비교 함수 재사용)
from leakmon.ai.model import predict, train  # noqa: E402
from leakmon.config import load_settings  # noqa: E402
from leakmon.evaluation import METHODS, replay  # noqa: E402
from leakmon.scenario import ALARM_EXPECTED, EVENT_START, SCENARIOS  # noqa: E402

LABELS = {"baseline": "고정 임계값", "ewma": "EWMA (학습 최대값)", "ewma_std": "EWMA (표준 L=3)",
          "iforest": "IF (학습 최대값)", "iforest_std": "IF (표준 s>0.5)",
          "linear_nohum": "제안 방법 (습도 제외)", "linear": "제안 방법"}
SCENARIO_LABELS = {"normal": "정상", "gradual_rise": "완만한 상승", "humidity_rise": "습도 상승",
                   "short_spike": "순간 급등 1회", "repeated_spike": "반복 급등", "sharp_rise": "급상승",
                   "data_loss": "데이터 끊김"}
COLORS = {"baseline": "#d62728", "ewma": "#8c564b", "ewma_std": "#e377c2", "iforest": "#ff7f0e",
          "iforest_std": "#9467bd", "linear_nohum": "#17becf", "linear": "#1f77b4"}
# 한글 글꼴 후보 (WSL은 Windows 글꼴, Pi·리눅스는 나눔). 없으면 기본 글꼴 → 한글이 네모로 나옴
FONT_CANDIDATES = ["/mnt/c/Windows/Fonts/malgun.ttf", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]


def use_korean_font() -> None:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            font_manager.fontManager.addfont(path)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=path).get_name()
            plt.rcParams["axes.unicode_minus"] = False  # 한글 글꼴엔 유니코드 마이너스 기호가 없어 깨짐
            return
    print("경고: 한글 글꼴 없음 → 그림의 한글이 깨질 수 있음")


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"표: {path}")


def prf(runs: list[dict]) -> tuple[float, float, float]:
    tp, fp, fn = (sum(int(r[k]) for r in runs) for k in ("tp", "fp", "fn"))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def mean_std(values: list[float]) -> str:
    if not values:
        return "-"
    return f"{statistics.mean(values):.1f} ± {statistics.stdev(values):.1f}" if len(values) > 1 else f"{values[0]:.1f}"


def exp1_tables(runs: list[dict], out: Path) -> None:
    """표 A: 시나리오 × 방식 (탐지/오경보 횟수, 평균 탐지 시간) / 표 B: 방식별 Precision·Recall·F1"""
    seeds = sorted({r["seed"] for r in runs}, key=int)
    table_a = []
    for name in SCENARIOS:
        row = {"시나리오": SCENARIO_LABELS[name], "구분": "탐지" if name in ALARM_EXPECTED else "오경보"}
        for m in METHODS:
            rs = [r for r in runs if r["method"] == m and r["scenario"] == name]
            count = f"{sum(r['alarm'] == 'True' for r in rs)}/{len(rs)}"
            if name in ALARM_EXPECTED:
                count += f" ({mean_std([float(r['delay_h']) for r in rs if r['delay_h']])}h)"
            row[LABELS[m]] = count
        table_a.append(row)
    write_csv(out / "table_exp1_scenarios.csv", table_a)

    table_b = []
    for m in METHODS:
        rs = [r for r in runs if r["method"] == m]
        p, rc, f1 = prf(rs)
        # seed별 F1의 평균±표준편차: 합산 F1이 특정 seed에 좌우되지 않는지 (재현 안정성)
        f1_by_seed = [prf([r for r in rs if r["seed"] == s])[2] for s in seeds]
        false_alarms = sum(r["alarm"] == "True" for r in rs if r["scenario"] not in ALARM_EXPECTED)
        f1_spread = f"{statistics.mean(f1_by_seed):.2f} ± {statistics.stdev(f1_by_seed):.2f}"
        table_b.append({"방식": LABELS[m], "Precision": f"{p:.2f}", "Recall": f"{rc:.2f}", "F1": f"{f1:.2f}",
                        "F1 (seed별 평균±표준편차)": f1_spread,
                        "오경보 재생 수": f"{false_alarms}/{len(seeds) * (len(SCENARIOS) - len(ALARM_EXPECTED))}"})
    write_csv(out / "table_exp1_metrics.csv", table_b)

    # 논문 본문용 압축 표 (8쪽 제한): 방식별 핵심 지표만 한 표에
    table_main = []
    for m in METHODS:
        rs = [r for r in runs if r["method"] == m]
        hum = [r for r in rs if r["scenario"] == "humidity_rise"]
        false_alarms = sum(r["alarm"] == "True" for r in rs if r["scenario"] not in ALARM_EXPECTED)
        delays = {n: mean_std([float(r["delay_h"]) for r in rs if r["scenario"] == n and r["delay_h"]])
                  for n in ("gradual_rise", "sharp_rise")}
        table_main.append({"방식": LABELS[m],
                           "오경보 재생": f"{false_alarms}/{len(seeds) * (len(SCENARIOS) - len(ALARM_EXPECTED))}",
                           "습도 상승 오경보": f"{sum(r['alarm'] == 'True' for r in hum)}/{len(hum)}",
                           "완만한 상승 탐지 (h)": delays["gradual_rise"], "급상승 탐지 (h)": delays["sharp_rise"],
                           "F1": f"{prf(rs)[2]:.2f}"})
    write_csv(out / "table_exp1_main.csv", table_main)


def fig_delay(runs: list[dict], out: Path) -> None:
    """그림: 경보가 정답인 시나리오별 평균 탐지 시간 (고정 임계값, EWMA, 습도 제외, 제안 방법)

    EWMA 표준 기준·IF는 넣지 않는다: 표준 기준은 정상에서도 상시 경보라 탐지 시간이 무의미하고,
    IF 학습 최대값 기준은 탐지된 5회만의 평균이라 비교가 공정하지 않다 → 표(탐지 횟수·F1)로 비교
    """
    methods = ("baseline", "ewma", "linear_nohum", "linear")
    fig, ax = plt.subplots(figsize=(5.5, 3.0))
    width = 0.8 / len(methods)
    alarm_scenarios = [s for s in SCENARIOS if s in ALARM_EXPECTED]
    for i, m in enumerate(methods):
        means, stds = [], []
        for name in alarm_scenarios:
            d = [float(r["delay_h"]) for r in runs if r["method"] == m and r["scenario"] == name and r["delay_h"]]
            means.append(statistics.mean(d) if d else 0)
            stds.append(statistics.stdev(d) if len(d) > 1 else 0)
        xs = [j + (i - (len(methods) - 1) / 2) * width for j in range(len(alarm_scenarios))]
        bars = ax.bar(xs, means, width, yerr=stds, capsize=2, label=LABELS[m], color=COLORS[m])
        ax.bar_label(bars, fmt="%.1f", fontsize=7, padding=2)
    ax.set_xticks(range(len(alarm_scenarios)), [SCENARIO_LABELS[s] for s in alarm_scenarios])
    ax.set_yscale("log")
    ax.set_ylabel("평균 탐지 시간 (가상 시간, h)")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "fig_exp1_delay.png", dpi=300)
    plt.close(fig)
    print(f"그림: {out / 'fig_exp1_delay.png'}")


def fig_timeseries(settings: dict, out: Path, seed: int = 0) -> None:
    """그림: 습도 상승(오경보 사례)·완만한 상승(조기 탐지 사례)의 측정값·예측값과 방식별 경보 구간"""
    cases = ["humidity_rise", "gradual_rise"]
    fig, axes = plt.subplots(2, 2, figsize=(7.5, 4.5), sharex="col", gridspec_kw={"height_ratios": [2, 1]})
    for col, name in enumerate(cases):
        by_method = {m: replay(name, 14, settings, m, seed) for m in METHODS}
        rows = by_method["linear"]
        t0 = rows[0]["ts"]
        days = [(r["ts"] - t0).total_seconds() / 86400 for r in rows]
        learn_end = t0 + EVENT_START
        model = train([r for r in rows if r["ts"] < learn_end])

        top = axes[0][col]
        top.plot(days, [r["leakage_mA"] for r in rows], color="gray", lw=0.6, label="측정값")
        top.plot(days, [predict(model, r["humidity_pct"], r["ts"]) for r in rows], color=COLORS["linear"],
                 lw=0.8, ls="--", label="제안 방법 예측값 (정상)")
        top.axhline(settings["baseline"]["normal_mA"] + settings["state"]["t1"]
                    * (settings["baseline"]["alarm_mA"] - settings["baseline"]["normal_mA"]),
                    color=COLORS["baseline"], lw=0.8, ls=":", label="고정 임계값 주의선")
        top.axvline(EVENT_START.days, color="black", lw=0.6)
        top.set_title(f"({'ab'[col]}) {SCENARIO_LABELS[name]} (seed {seed})", fontsize=9)
        top.set_ylabel("누설전류 (mA)")
        top.legend(fontsize=6, loc="upper left")

        # 아래: 방식별 경보(주의·점검권고) 구간을 가로 띠로
        bottom = axes[1][col]
        for i, m in enumerate(METHODS):
            alarm = [1 if r["state"] in ("caution", "inspect") else 0 for r in by_method[m]]
            bottom.fill_between(days, i, [i + 0.8 * a for a in alarm], step="post", color=COLORS[m])
        bottom.set_yticks([i + 0.4 for i in range(len(METHODS))], [LABELS[m] for m in METHODS], fontsize=6)
        bottom.set_ylim(0, len(METHODS))
        bottom.axvline(EVENT_START.days, color="black", lw=0.6)
        bottom.set_xlabel("가상 시간 (일)")
    fig.tight_layout()
    fig.savefig(out / "fig_exp1_timeseries.png", dpi=300)
    plt.close(fig)
    print(f"그림: {out / 'fig_exp1_timeseries.png'}")


def per_seed(runs: list[dict], method: str, scenario: str, key: str = "delay_h") -> list[float]:
    """seed 순서로 정렬한 값 목록 (짝지은 검정용: 같은 seed = 같은 신호)"""
    rs = sorted((r for r in runs if r["method"] == method and r["scenario"] == scenario), key=lambda r: int(r["seed"]))
    return [float(r[key]) for r in rs]


# 제안 방법과 짝지어 검정할 방식: 현장 관행(고정 임계값), 가장 강한 비교(EWMA), 제거 실험(습도 제외)
STATS_RIVALS = ("baseline", "ewma", "linear_nohum")


def exp1_stats(runs: list[dict], out: Path) -> None:
    """표: 제안 방법과 다른 방식의 차이가 우연인지 (Wilcoxon 부호순위 검정, 같은 seed끼리 짝지음, 양측)

    seed 10개라 정규분포 가정을 못 하므로 비모수 검정을 쓴다. 모든 seed에서 차이가 0이면 검정 불가 → "차이 없음"
    """
    rows = []
    seeds = sorted({r["seed"] for r in runs}, key=int)
    for rival in (m for m in STATS_RIVALS if any(r["method"] == m for r in runs)):
        targets = [(f"탐지 시간: {SCENARIO_LABELS[n]}", per_seed(runs, rival, n), per_seed(runs, "linear", n))
                   for n in SCENARIOS if n in ALARM_EXPECTED]
        targets.append(("F1 (seed별)",
                        [prf([r for r in runs if r["method"] == rival and r["seed"] == s])[2] for s in seeds],
                        [prf([r for r in runs if r["method"] == "linear" and r["seed"] == s])[2] for s in seeds]))
        for label, base, prop in targets:
            diffs = [p - b for b, p in zip(base, prop, strict=True)]
            p_value = "차이 없음" if not any(diffs) else f"{wilcoxon(base, prop).pvalue:.4f}"
            # 탐지 시간은 짧을수록, F1은 클수록 제안이 나음
            better = sum(d < 0 for d in diffs) if label.startswith("탐지") else sum(d > 0 for d in diffs)
            rows.append({"비교 방식": LABELS[rival], "비교 항목": label,
                         "비교 방식 평균": f"{statistics.mean(base):.2f}",
                         "제안 방법 평균": f"{statistics.mean(prop):.2f}", "짝 수(seed)": len(diffs),
                         "제안이 나은 seed 수": better,
                         "p값 (Wilcoxon, 양측)": p_value})
    write_csv(out / "table_exp1_stats.csv", rows)


FALSE_ALARM_SCENARIOS = [s for s in SCENARIOS if s not in ALARM_EXPECTED]


def robustness_table(runs: list[dict], out: Path) -> None:
    """표: 조건(A·B·C) × 방식의 F1, 오경보 재생 수, 습도 상승 오경보, 완만한 상승 평균 탐지 시간"""
    rows = []
    conditions = list(dict.fromkeys((r["group"], r["condition"]) for r in runs if r["group"] != "D"))
    for group, cond in conditions:
        for m in METHODS:
            rs = [r for r in runs if r["group"] == group and r["condition"] == cond and r["method"] == m]
            fa = [r for r in rs if r["scenario"] in FALSE_ALARM_SCENARIOS]
            hum = [r for r in rs if r["scenario"] == "humidity_rise"]
            delays = [float(r["delay_h"]) for r in rs if r["scenario"] == "gradual_rise" and r["delay_h"]]
            rows.append({"그룹": group, "조건": cond, "방식": LABELS[m], "F1": f"{prf(rs)[2]:.2f}",
                         "오경보 재생": f"{sum(r['alarm'] == 'True' for r in fa)}/{len(fa)}",
                         "습도 상승 오경보": f"{sum(r['alarm'] == 'True' for r in hum)}/{len(hum)}",
                         "완만한 상승 탐지": f"{len(delays)}/{sum(r['scenario'] == 'gradual_rise' for r in rs)}",
                         "완만한 상승 탐지 시간 (h)": mean_std(delays)})
    write_csv(out / "table_exp1b_robustness.csv", rows)

    # 논문 본문용 요약: 조건 × 주요 방식의 F1만
    summary_methods = [m for m in ("baseline", "ewma", "linear_nohum", "linear") if any(r["method"] == m for r in runs)]
    summary = []
    for group, cond in conditions:
        row = {"조건": cond}
        for m in summary_methods:
            rs = [r for r in runs if r["group"] == group and r["condition"] == cond and r["method"] == m]
            row[LABELS[m]] = f"{prf(rs)[2]:.2f}"
        summary.append(row)
    write_csv(out / "table_exp1b_summary.csv", summary)


def fig_tradeoff(runs: list[dict], out: Path) -> None:
    """그림: 임계값을 바꿔 가며 본 "오경보 재생 수 vs 완만한 상승 탐지 시간" (왼쪽 아래일수록 좋음)

    한 임계값에서만 이긴 게 아니라, 어떤 임계값을 골라도 제안 방법이 더 좋은 위치에 있는지 보여 준다
    """
    fig, ax = plt.subplots(figsize=(5.5, 3.5))
    for m in ("baseline", "linear"):
        params = list(dict.fromkeys(r["param"] for r in runs if r["group"] == "D" and r["method"] == m))
        xs, ys = [], []
        for prm in params:
            rs = [r for r in runs if r["group"] == "D" and r["method"] == m and r["param"] == prm]
            fa = sum(r["alarm"] == "True" for r in rs if r["scenario"] in FALSE_ALARM_SCENARIOS)
            d = [float(r["delay_h"]) for r in rs if r["scenario"] == "gradual_rise" and r["delay_h"]]
            missed = sum(r["scenario"] == "gradual_rise" for r in rs) - len(d)
            xs.append(fa)
            ys.append(statistics.mean(d) if d else float("nan"))
            label = prm.split()[-1] + (f" (미탐 {missed})" if missed else "")
            ax.annotate(label, (xs[-1], ys[-1]), fontsize=6, textcoords="offset points", xytext=(4, 3))
        ax.plot(xs, ys, marker="o", color=COLORS[m],
                label=f"{LABELS[m]} ({'주의선 mA' if m == 'baseline' else 'sigma_scale'})")
    ax.set_xlabel(f"오경보 재생 수 (/{len(FALSE_ALARM_SCENARIOS) * 10})")
    ax.set_ylabel("완만한 상승 평균 탐지 시간 (h)")
    ax.set_yscale("log")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "fig_exp1b_tradeoff.png", dpi=300)
    plt.close(fig)
    print(f"그림: {out / 'fig_exp1b_tradeoff.png'}")


def exp2_table(sil_dir: Path, hil_dir: Path, out: Path) -> None:
    """표: 방식별 SIL↔HIL 경보 일치율, 탐지 시간 차이 최대 + DAC→ADC 오차"""
    rows = [{"방식": LABELS[m], "경보 여부 일치": f"{v['same_alarm']}/{v['runs']}",
             "탐지 시간 차이 최대 (h)": f"{v['max_delay_diff_h']:.2f}", "뒤집힌 재생": "; ".join(v["flipped"]) or "-"}
            for m, v in compare(load(sil_dir), load(hil_dir)).items()]
    write_csv(out / "table_exp2_agreement.csv", rows)
    err = json.loads((Path("results/exp2_hil") / "meta.json").read_text(encoding="utf-8"))["conditions"]
    print(f"DAC→ADC 오차 (mA): {err.get('dac_adc_error_mA')}")


BENCH_LABELS = {"train_linear": "학습 1일 - 제안 방법", "train_iforest": "학습 1일 - Isolation Forest",
                "judge_linear": "판정 1회 - 제안 방법", "judge_iforest": "판정 1회 - Isolation Forest",
                "train_linear_7d": "학습 7일 - 제안 방법", "train_iforest_7d": "학습 7일 - Isolation Forest",
                "sensor_dac_adc": "센서 1회 (DAC→ADC)"}


def exp3_table(bench: Path, settings: dict, out: Path) -> None:
    """표: 학습·판정·센서 1회 처리 시간, 측정 주기(실제 시간 1초) 대비 비율, 최대 메모리"""
    data = json.loads(bench.read_text(encoding="utf-8"))
    period_ms = settings["sample_period_s"] * 1000
    rows = [{"항목": BENCH_LABELS.get(k, k), "중앙값 (ms)": v["median_ms"], "최대 (ms)": v["max_ms"],
             "반복": v["repeat"], "측정 주기 대비 최대 (%)": f"{v['max_ms'] / period_ms * 100:.3f}"}
            for k, v in data.items() if isinstance(v, dict) and "median_ms" in v]
    rows.append({"항목": "최대 메모리 (MB)", "중앙값 (ms)": data.get("peak_rss_MB", "-"), "최대 (ms)": "-",
                 "반복": "-", "측정 주기 대비 최대 (%)": "-"})
    write_csv(out / "table_exp3_bench.csv", rows)
    print(f"  측정 장치: {data.get('host')} ({data.get('machine')}, Python {data.get('python')})")


def fig_dac_errors(hil_dir: Path, out: Path) -> None:
    """그림: 출력 크기(목표 mA)별 DAC→ADC 오차 분포 → HIL 신호 경로가 판정에 영향을 줄 만큼 틀리지 않는지"""
    with open(hil_dir / "dac_adc_errors.csv", encoding="utf-8") as f:
        pairs = [(float(r["target_mA"]), float(r["error_mA"])) for r in csv.DictReader(f)]
    fig, ax = plt.subplots(figsize=(5.5, 3.0))
    ax.scatter([t for t, _ in pairs], [e for _, e in pairs], s=1, alpha=0.3, color=COLORS["linear"])
    ax.axhline(0, color="black", lw=0.6)
    ax.set_xlabel("목표 누설전류 (mA)")
    ax.set_ylabel("DAC→ADC 오차 (mA)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "fig_exp2_dac_error.png", dpi=300)
    plt.close(fig)
    print(f"그림: {out / 'fig_exp2_dac_error.png'} ({len(pairs)}개)")


def exp4_table(sec_dir: Path, out: Path) -> None:
    """표: 보안 케이스별 브로커 응답과 판정. pytest.log에서 케이스 이름 다음 줄(브로커 응답 출력)을 읽는다"""
    rows, case = [], None
    for line in (sec_dir / "pytest.log").read_text(encoding="utf-8").splitlines():
        if line.startswith("tests/test_security.py::"):
            case = {"케이스": line.split("::")[1].strip(), "브로커 응답": "", "판정": ""}
        elif case is not None and line.strip() in ("PASSED", "FAILED", "SKIPPED"):
            case["판정"] = line.strip()
            rows.append(case)
            case = None
        elif case is not None and line.strip():
            case["브로커 응답"] = line.strip()
    write_csv(out / "table_exp4_security.csv", rows)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    use_korean_font()
    settings = load_settings()
    sil = Path("results/exp1_sil")
    meta = json.loads((sil / "meta.json").read_text(encoding="utf-8"))
    print(f"실험 1 출처: 커밋 {meta['commit'][:8]}, 실행 {meta['run_at']}")
    if meta["uncommitted_changes"]:
        print("경고: 커밋 안 한 수정이 있는 상태의 결과 → 논문 최종 숫자는 커밋 후 다시 돌린 결과로")
    with open(sil / "runs.csv", encoding="utf-8") as f:
        runs = list(csv.DictReader(f))
    exp1_tables(runs, out)
    exp1_stats(runs, out)
    fig_delay(runs, out)
    fig_timeseries(settings, out)
    robust = Path("results/exp1b_robustness/runs.csv")
    if robust.exists():
        with open(robust, encoding="utf-8") as f:
            robust_runs = list(csv.DictReader(f))
        robustness_table(robust_runs, out)
        fig_tradeoff(robust_runs, out)
    else:
        print("실험 1b 결과 없음 (python tools/robustness.py 실행 필요) → 건너뜀")
    # 일치표: Pi 측정값 복원 재생(방식 추가분 포함)이 있으면 그것을, 없으면 Pi 원본 결과를 쓴다
    replay_dir = Path("results/exp2_hil_replay")
    hil_runs = replay_dir if (replay_dir / "runs.csv").exists() else Path("results/exp2_hil")
    if (hil_runs / "runs.csv").exists():
        print(f"실험 2 일치표 입력: {hil_runs}")
        exp2_table(sil, hil_runs, out)
        if (Path("results/exp2_hil") / "dac_adc_errors.csv").exists():
            fig_dac_errors(Path("results/exp2_hil"), out)
    else:
        print("실험 2 결과 없음 (Pi에서 evaluate.py --hil 실행 필요) → 건너뜀")
    if Path("results/bench_pi.json").exists():
        exp3_table(Path("results/bench_pi.json"), settings, out)
    else:
        print("실험 3 결과 없음 (Pi에서 bench.py --out results/bench_pi.json 실행 필요) → 건너뜀")
    if Path("results/exp4_security/pytest.log").exists():
        exp4_table(Path("results/exp4_security"), out)
    else:
        print("실험 4 결과 없음 (bash tools/exp4_security.sh 실행 필요) → 건너뜀")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="논문 그림·표 생성")
    parser.add_argument("--out", type=Path, default=Path("results/paper"))
    main(parser.parse_args().out)
