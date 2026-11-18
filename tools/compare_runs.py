"""SIL vs HIL 결과 비교 (back-to-back 테스트, 논문 실험 2).

실행: python tools/compare_runs.py results/exp1_sil results/exp2_hil
같은 (방식, 시나리오, seed) 재생끼리 짝지어, 경보 여부가 같은지와 탐지 시간 차이를 본다.
HIL 측정값에는 실제 DAC·ADC 오차가 섞이므로 완전히 같을 필요는 없지만, 경보 판단이 뒤집히면 원인 분석 대상이다.
"""
import csv
import sys
from pathlib import Path


def load(folder: Path) -> dict[tuple[str, str, str], dict]:
    with open(folder / "runs.csv", encoding="utf-8") as f:
        return {(r["method"], r["scenario"], r["seed"]): r for r in csv.DictReader(f)}


def compare(sil: dict, hil: dict) -> dict[str, dict]:
    """방식별: 짝지은 재생 수, 경보 여부 일치 수, 뒤집힌 재생 목록, 탐지 시간 차이(HIL - SIL) 최대 절댓값."""
    missing = sil.keys() ^ hil.keys()
    if missing:  # 조건(seed·days)이 다른 두 결과를 비교하면 무효
        raise ValueError(f"두 결과의 재생 목록이 다름 (예: {sorted(missing)[:3]})")
    out: dict[str, dict] = {}
    for key in sorted(sil):
        s, h = sil[key], hil[key]
        m = out.setdefault(key[0], {"runs": 0, "same_alarm": 0, "flipped": [], "max_delay_diff_h": 0.0})
        m["runs"] += 1
        if s["alarm"] == h["alarm"]:
            m["same_alarm"] += 1
        else:
            m["flipped"].append(f"{key[1]} seed {key[2]} (SIL {s['alarm']} → HIL {h['alarm']})")
        if s["delay_h"] and h["delay_h"]:
            m["max_delay_diff_h"] = max(m["max_delay_diff_h"], abs(float(h["delay_h"]) - float(s["delay_h"])))
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("사용법: python tools/compare_runs.py <SIL 결과 폴더> <HIL 결과 폴더>")
    for method, m in compare(load(Path(sys.argv[1])), load(Path(sys.argv[2]))).items():
        print(f"■ {method}: 경보 여부 일치 {m['same_alarm']}/{m['runs']},"
              f" 탐지 시간 차이 최대 {m['max_delay_diff_h']:.2f}시간")
        for f in m["flipped"]:
            print(f"  뒤집힘: {f}")
