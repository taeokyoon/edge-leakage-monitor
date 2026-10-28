from datetime import datetime, timedelta

from leakmon.state import CAUTION, FAULT, INSPECT, NORMAL, StateMachine

START = datetime(2026, 11, 1)
STEP = timedelta(minutes=24)  # ×1440 배속에서 1초 샘플 = 가상 24분


def make_machine():
    # 주의 D1=2h(샘플 5개), 점검권고 D2=4h(10개), 회복 R=2h, 센서장애 N=1h
    return StateMachine(t1=0.5, t2=1.0, d1=timedelta(hours=2), d2=timedelta(hours=4),
                        recover=timedelta(hours=2), fault_after=timedelta(hours=1))


def feed(machine, scores, start_index=0):
    """점수 목록을 STEP 간격으로 넣고 상태 이력을 돌려준다."""
    return [machine.update(START + STEP * (start_index + i), s) for i, s in enumerate(scores)]


def test_normal_stays_normal():
    assert set(feed(make_machine(), [0.1] * 30)) == {NORMAL}


def test_short_spike_is_suppressed():
    # 시나리오 4: 2h 미만의 순간 급등은 주의로 가지 않음
    states = feed(make_machine(), [0.1, 2.0, 2.0, 2.0, 0.1, 0.1])
    assert set(states) == {NORMAL}


def test_sustained_rise_goes_caution_then_inspect():
    m = make_machine()
    states = feed(m, [0.8] * 6)          # T1 초과 2h 지속 → 주의
    assert states[-1] == CAUTION
    states = feed(m, [1.5] * 11, 6)      # T2 초과 4h 지속 → 점검권고
    assert states[-1] == INSPECT


def test_caution_recovers_to_normal():
    m = make_machine()
    feed(m, [0.8] * 6)
    assert feed(m, [0.1] * 6, 6)[-1] == NORMAL


def test_inspect_needs_ack_and_d1_reapplies():
    m = make_machine()
    feed(m, [0.8] * 6 + [1.5] * 11)
    assert m.state == INSPECT
    assert feed(m, [0.1] * 10, 17)[-1] == INSPECT  # 점수가 내려가도 ack 전엔 유지
    assert m.ack() is True and m.state == NORMAL
    # ack 직후 점수가 높아도 D1(2h)을 다시 채워야 주의
    states = feed(m, [0.8] * 6, 27)
    assert states[:5] == [NORMAL] * 5 and states[5] == CAUTION


def test_ack_ignored_when_not_inspect():
    m = make_machine()
    assert m.ack() is False and m.state == NORMAL


def test_fault_and_recovery_to_previous_state():
    m = make_machine()
    feed(m, [0.8] * 6)
    assert m.state == CAUTION
    states = feed(m, [None] * 4, 6)      # 1h 초과 미수신 → 센서장애
    assert states[-1] == FAULT
    assert m.update(START + STEP * 10, 0.8) == CAUTION  # 복구 시 이전 판정 재개


def test_fault_gap_does_not_count_toward_duration():
    # 장애 전후 샘플 2개만으로 D1(2h)을 채운 것으로 보면 안 됨 (데이터 없는 시간은 지속 시간이 아님)
    m = make_machine()
    m.update(START, 0.8)
    feed(m, [None] * 12, 1)              # 4.8h 미수신 → 센서장애
    assert m.state == FAULT
    assert m.update(START + STEP * 13, 0.8) == NORMAL


def test_d2_counts_from_caution_entry():
    # 점수가 한 번에 T2를 넘어도, 점검권고는 주의 진입 후 D2(4h)가 지나야 함
    states = feed(make_machine(), [0.1] + [1.5] * 16)
    assert states[6] == CAUTION          # 1.5 시작(index 1) + D1 2h
    assert states[15] == CAUTION         # 주의 진입 + 3.6h → 아직 주의
    assert states[16] == INSPECT         # 주의 진입 + 4h
