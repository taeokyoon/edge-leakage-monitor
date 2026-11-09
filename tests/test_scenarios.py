"""시나리오 생성기 + 고정 임계값 baseline + 상태 머신을 MQTT 없이 빠르게 재생해 기대 결과를 확인한다.

설정은 실제 config/settings.yaml을 쓴다 (시연과 같은 판정이 나오는지 보기 위함).
"""
from datetime import datetime, timedelta

from leakmon import evaluation
from leakmon.config import load_settings
from leakmon.evaluation import transitions
from leakmon.scenario import Scenario, generate
from leakmon.state import CAUTION, FAULT, INSPECT, NORMAL

START = datetime(2026, 11, 1)
STEP = timedelta(minutes=24)  # ×1440 배속에서 1초 샘플 = 가상 24분
SETTINGS = load_settings()


def replay(name: str, days: float, seed: int = 0) -> list[tuple[timedelta, str]]:
    """고정 임계값으로 재생한 상태 전이 목록 [(시작 후 경과, 새 상태)]"""
    return transitions(evaluation.replay(name, days, SETTINGS, "baseline", seed))


def replay_ai(name: str, days: float, seed: int = 0) -> list[tuple[timedelta, str]]:
    """AI(선형 회귀 + 잔차)로 재생. 처음 learn_days일은 학습 기간"""
    return transitions(evaluation.replay(name, days, SETTINGS, "linear", seed))


def states(transitions):
    return [s for _, s in transitions]


def test_1_normal_has_no_alert():
    assert replay("normal", 7) == []


def test_2_gradual_rise_reaches_inspect_after_days():
    t = replay("gradual_rise", 14)
    assert states(t)[0] == CAUTION and states(t)[-1] == INSPECT
    assert t[0][0] > timedelta(days=2)  # 완만하므로 고정 임계값은 며칠 뒤에야 감지 (3단계 AI와 비교 대상)
    # 임계선 근처에서는 하루 주기 정상 변동(가동·습도) 때문에 주의↔정상을 오간다 (baseline의 약점)
    assert states(t).count(CAUTION) > 1


def test_3_humidity_rise_is_false_alarm_for_baseline():
    # 습도만 올라간 정상 변동인데도 고정 임계값은 주의를 낸다 (= baseline의 오경보, AI가 줄여야 할 대상)
    t = replay("humidity_rise", 7)
    assert CAUTION in states(t) and INSPECT not in states(t)


def test_4_short_spike_is_suppressed():
    assert replay("short_spike", 3) == []


def test_5_sharp_rise_goes_inspect_within_a_day():
    t = replay("sharp_rise", 3)
    assert states(t) == [CAUTION, INSPECT]
    assert t[1][0] < timedelta(days=2)  # 이상 시작(1일) 후 하루 안에 점검권고


def test_6_data_loss_goes_fault_and_recovers():
    assert states(replay("data_loss", 3)) == [FAULT, NORMAL]


def test_same_seed_same_data():
    a = Scenario("gradual_rise", START, seed=1)
    b = Scenario("gradual_rise", START, seed=1)
    times = [START + STEP * i for i in range(50)]
    assert [a.sample(t) for t in times] == [b.sample(t) for t in times]


# ── 3단계 AI (선형 회귀 + 잔차): 같은 시나리오를 baseline과 나란히 비교 ──

def test_ai_1_normal_has_no_alert():
    assert replay_ai("normal", 7) == []


def test_ai_2_gradual_rise_detected_earlier_than_baseline():
    ai, base = replay_ai("gradual_rise", 14), replay("gradual_rise", 14)
    assert states(ai) == [CAUTION, INSPECT]  # 습도·시간대를 빼고 보므로 주의↔정상 반복이 없다
    assert ai[0][0] < base[0][0] - timedelta(days=2)  # baseline보다 2일 이상 먼저 주의


def test_ai_3_humidity_rise_is_not_false_alarm():
    # baseline은 주의(오경보)를 내지만 AI는 습도 영향을 예측에 포함하므로 경보 없음 (STK-02)
    assert replay_ai("humidity_rise", 7) == []


def test_ai_4_short_spike_is_suppressed():
    assert replay_ai("short_spike", 3) == []


def test_ai_5_sharp_rise_goes_inspect_within_a_day():
    t = replay_ai("sharp_rise", 3)
    assert states(t) == [CAUTION, INSPECT] and t[1][0] < timedelta(days=2)


def test_ai_6_data_loss_goes_fault_and_recovers():
    assert states(replay_ai("data_loss", 3)) == [FAULT, NORMAL]


def test_dataset_labels():
    # 평가 라벨: 경보가 정답인 시나리오만, 이상 시작(1일) 뒤부터 anomaly
    rows = generate("gradual_rise", 2, step=timedelta(hours=1))
    assert not any(r["anomaly"] for r in rows[:25]) and all(r["anomaly"] for r in rows[25:])
    assert not any(r["anomaly"] for r in generate("humidity_rise", 3))


def test_repeated_spike_goes_caution_and_stays():
    # 6시간마다 1시간 튐 → 세 번째 튐이 끝나면 주의, 튐이 계속되는 동안 주의 유지 (STK-11, SYS-07, B안)
    # baseline·AI 모두
    for t in (replay("repeated_spike", 7), replay_ai("repeated_spike", 7)):
        assert states(t) == [CAUTION] and t[0][0] < timedelta(days=2)


def test_measured_humidity_overrides_synthetic():
    """Pi dht20 모드: 실측 습도를 넘기면 그 값이 쓰이고, 난수 순서는 합성 습도일 때와 같다."""
    from datetime import datetime, timedelta

    from leakmon.scenario import HUMIDITY_COEF_mA, Scenario
    start = datetime(2026, 11, 9)
    a, b = Scenario("normal", start), Scenario("normal", start)
    now = start + timedelta(hours=3)
    synthetic = a.sample(now)
    measured = b.sample(now, humidity_pct=70.0)
    assert synthetic is not None and measured is not None
    assert measured["humidity_pct"] == 70.0
    expected_diff = HUMIDITY_COEF_mA * (70.0 - synthetic["humidity_pct"])
    assert abs((measured["leakage_mA"] - synthetic["leakage_mA"]) - expected_diff) < 0.01
