"""개발자 단위 테스트: HIL 평가용 dac_path()와 SIL·HIL 비교 도구 (tools/compare_runs.py)."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

from leakmon.scenario import Scenario
from leakmon.sensors.pi import PiSensors
from test_pi_sensors import FakeBus

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from compare_runs import compare  # noqa: E402


def make(bus: FakeBus) -> PiSensors:
    return PiSensors(Scenario("normal", datetime(2026, 11, 1)), None, bus)


def test_dac_path_returns_measured_value_and_small_error():
    measured, error = make(FakeBus()).dac_path(8.0)
    assert measured is not None and abs(measured - 8.0) < 0.01
    assert error is not None and abs(error) < 0.01


def test_dac_path_i2c_error_is_none_none():
    assert make(FakeBus(broken=True)).dac_path(8.0) == (None, None)


def test_dac_path_unplugged_wire_keeps_error_for_record():
    measured, error = make(FakeBus(a1_floating_volts=0.6)).dac_path(12.0)  # 측정 3mA, 출력 12mA
    assert measured is None and error is not None and error < -8


def run(alarm: str, delay: str = "") -> dict:
    return {"alarm": alarm, "delay_h": delay}


def test_compare_counts_flipped_alarm_and_delay_diff():
    sil = {("linear", "sharp_rise", "0"): run("True", "2.0"), ("linear", "normal", "0"): run("False")}
    hil = {("linear", "sharp_rise", "0"): run("True", "2.4"), ("linear", "normal", "0"): run("True")}
    m = compare(sil, hil)["linear"]
    assert (m["runs"], m["same_alarm"]) == (2, 1)
    assert m["flipped"] == ["normal seed 0 (SIL False → HIL True)"]
    assert abs(m["max_delay_diff_h"] - 0.4) < 1e-9


def test_compare_rejects_different_run_sets():
    with pytest.raises(ValueError):
        compare({("linear", "normal", "0"): run("False")}, {("linear", "normal", "1"): run("False")})


def test_paper_tables_recompute_metrics_from_runs(tmp_path):
    """논문 표는 runs.csv만으로 다시 계산된다: tp/fp/fn 합 → Precision·Recall·F1"""
    from make_figures import exp1_tables, prf
    from leakmon.evaluation import METHODS
    from leakmon.scenario import SCENARIOS
    runs = [{"method": m, "scenario": s, "seed": str(seed), "alarm": "True", "delay_h": "2.0",
             "tp": "3", "fp": "1", "fn": "0"} for m in METHODS for s in SCENARIOS for seed in (0, 1)]
    assert prf(runs[:1]) == (0.75, 1.0, 2 * 0.75 / 1.75)
    exp1_tables(runs, tmp_path)
    metrics = (tmp_path / "table_exp1_metrics.csv").read_text(encoding="utf-8-sig").splitlines()
    assert metrics[1].startswith("고정 임계값,0.75,1.00,0.86")


def test_paper_stats_no_difference_is_reported_not_tested(tmp_path):
    """모든 seed에서 차이가 0이면 Wilcoxon을 돌리지 않고 "차이 없음"으로 적는다 (검정 불가 오류 방지)"""
    from make_figures import exp1_stats
    from leakmon.scenario import SCENARIOS
    runs = [{"method": m, "scenario": s, "seed": str(seed), "alarm": "True", "delay_h": "5.0",
             "tp": "3", "fp": "1", "fn": "0"} for m in ("baseline", "linear") for s in SCENARIOS for seed in range(10)]
    exp1_stats(runs, tmp_path)
    text = (tmp_path / "table_exp1_stats.csv").read_text(encoding="utf-8-sig")
    assert text.count("차이 없음") == 4
