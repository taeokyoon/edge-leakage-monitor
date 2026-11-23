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


def test_paper_pi_results_become_tables(tmp_path):
    """Pi 결과 파일(벤치 json, DAC 오차 csv)과 보안 로그가 표·그림으로 바뀐다 (Pi 실행 전 미리 확인)"""
    import json

    from make_figures import exp3_table, exp4_table, fig_dac_errors
    bench = tmp_path / "bench.json"
    bench.write_text(json.dumps({"host": "pi", "machine": "aarch64", "python": "3.11", "peak_rss_MB": 150.0,
                                 "judge_linear": {"median_ms": 0.1, "max_ms": 10.0, "repeat": 3}}))
    exp3_table(bench, {"sample_period_s": 1.0}, tmp_path)
    table = (tmp_path / "table_exp3_bench.csv").read_text(encoding="utf-8-sig")
    assert "판정 1회 - 제안 방법,0.1,10.0,3,1.000" in table and "최대 메모리 (MB),150.0" in table

    (tmp_path / "dac_adc_errors.csv").write_text("target_mA,error_mA\n5.0,0.01\n10.0,-0.02\n")
    fig_dac_errors(tmp_path, tmp_path)
    assert (tmp_path / "fig_exp2_dac_error.png").exists()

    log = "tests/test_security.py::test_2_wrong \n  틀린 비밀번호 → Not authorized\nPASSED\n"
    (tmp_path / "pytest.log").write_text(log, encoding="utf-8")
    exp4_table(tmp_path, tmp_path)
    assert "test_2_wrong,틀린 비밀번호 → Not authorized,PASSED" in (
        tmp_path / "table_exp4_security.csv").read_text(encoding="utf-8-sig")


def test_hil_read_failures_counted_without_original_gaps():
    # 원래 끊김 1개(None)는 실패로 세지 않고, I2C 오류로 None이 된 2개만 실패로 센다
    from evaluate import count_missing, hil_signal
    rows = [{"leakage_mA": 5.0}, {"leakage_mA": None}, {"leakage_mA": 6.0}]
    signals = {("x", 0): rows}
    before = count_missing(signals)
    signals[("x", 0)] = hil_signal(rows, make(FakeBus(broken=True)), [])
    assert count_missing(signals) - before == 2


def test_hil_replay_restores_pi_measurement(tmp_path):
    # Pi 측정값 = DAC 실제 출력(코드 단위로 잘린 값) + 기록 오차. 끊김(None)은 건너뛰고 순서대로 붙인다
    from evaluate import signals_from_pi_errors

    from leakmon.sensors.pi import DAC_VREF, mA_to_dac_code
    errors = tmp_path / "dac_adc_errors.csv"
    errors.write_text("target_mA,error_mA\n5.0,0.01\n6.0,-0.02\n")
    signals = {("x", 0): [{"leakage_mA": 5.0}, {"leakage_mA": None}, {"leakage_mA": 6.0}]}
    signals_from_pi_errors(signals, errors, 5.0)
    sent = mA_to_dac_code(5.0, 5.0) / 4095 * DAC_VREF * 5.0
    assert signals[("x", 0)][0]["leakage_mA"] == round(sent + 0.01, 3)
    assert signals[("x", 0)][1]["leakage_mA"] is None


def test_hil_replay_rejects_misaligned_record(tmp_path):
    # 기록의 목표값이 신호와 다르면(조건·코드가 다른 결과) 엉뚱한 측정을 붙이지 않고 멈춘다
    from evaluate import signals_from_pi_errors
    errors = tmp_path / "dac_adc_errors.csv"
    errors.write_text("target_mA,error_mA\n7.0,0.01\n")
    with pytest.raises(ValueError):
        signals_from_pi_errors({("x", 0): [{"leakage_mA": 5.0}]}, errors, 5.0)
