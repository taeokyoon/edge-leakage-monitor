"""시나리오 생성기 + 고정 임계값 baseline + 상태 머신을 MQTT 없이 빠르게 재생해 기대 결과를 확인한다.

설정은 실제 config/settings.yaml을 쓴다 (시연과 같은 판정이 나오는지 보기 위함).
"""
from datetime import datetime, timedelta

from leakmon.ai.baseline import fixed_threshold_score
from leakmon.config import load_settings
from leakmon.scenario import Scenario
from leakmon.state import CAUTION, FAULT, INSPECT, NORMAL, StateMachine

START = datetime(2026, 11, 1)
STEP = timedelta(minutes=24)  # ×1440 배속에서 1초 샘플 = 가상 24분
SETTINGS = load_settings()


def replay(name: str, days: float, seed: int = 0) -> list[tuple[timedelta, str]]:
    """시나리오를 days일 동안 재생하고 상태 전이 목록 [(시작 후 경과, 새 상태)]을 돌려준다."""
    scenario = Scenario(name, START, seed)
    machine = StateMachine.from_settings(SETTINGS["state"])
    normal_mA, alarm_mA = SETTINGS["baseline"]["normal_mA"], SETTINGS["baseline"]["alarm_mA"]
    transitions = []
    now = START
    while now < START + timedelta(days=days):
        reading = scenario.sample(now)
        score = None if reading is None else fixed_threshold_score(reading["leakage_mA"], normal_mA, alarm_mA)
        before = machine.state
        if machine.update(now, score) != before:
            transitions.append((now - START, machine.state))
        now += STEP
    return transitions


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
