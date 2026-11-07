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


# ── 테스터 작성 명세 (docs/TEST.md 7장) ── 측정 시각 0, 24, 48 … 분 = 리스트 순서

def test_tc_st_02_01_caution_at_exactly_d1():
    """TC-ST-02-01: 0.6이 0~120분 → 96분까지 정상, 120분에 주의 (D1 경계)"""
    states = feed(make_machine(), [0.6] * 6)
    assert states == [NORMAL] * 5 + [CAUTION]


def test_tc_st_02_02_score_equal_t1_is_not_above():
    """TC-ST-02-02: 0.5(= T1)가 0~120분 → 계속 정상 ('점수 > T1'이므로)"""
    assert feed(make_machine(), [0.5] * 6) == [NORMAL] * 6


def test_tc_st_02_03_one_break_restarts_d1():
    """TC-ST-02-03: 48분에만 0.4 → 72분부터 다시 재서 168분까지 정상, 192분에 주의"""
    states = feed(make_machine(), [0.6, 0.6, 0.4] + [0.6] * 6)
    assert states == [NORMAL] * 8 + [CAUTION]


def enter_caution():
    """사전 조건: 0~120분에 0.6 → 120분에 주의 (TC-ST-02-01과 같음)"""
    m = make_machine()
    assert feed(m, [0.6] * 6)[-1] == CAUTION
    return m


def test_tc_st_04_01_recover_at_exactly_r():
    """TC-ST-04-01: 144분부터 0.3 → 240분까지 주의, 264분에 정상 (R 경계)"""
    states = feed(enter_caution(), [0.3] * 6, start_index=6)
    assert states == [CAUTION] * 5 + [NORMAL]


def test_tc_st_04_02_score_equal_t1_counts_as_recovery():
    """TC-ST-04-02: 144분부터 0.5(= T1) → 264분에 정상 ('점수 ≤ T1'이므로 포함)"""
    states = feed(enter_caution(), [0.5] * 6, start_index=6)
    assert states == [CAUTION] * 5 + [NORMAL]


def test_tc_st_04_03_one_spike_restarts_recovery():
    """TC-ST-04-03: 144~192분 0.3, 216분 0.6, 240분부터 0.3 → 336분까지 주의, 360분에 정상"""
    states = feed(enter_caution(), [0.3] * 3 + [0.6] + [0.3] * 6, start_index=6)
    assert states == [CAUTION] * 9 + [NORMAL]


def enter_inspect():
    """사전 조건: 120분 주의 → 1.5를 240분 유지 → 점검권고"""
    m = enter_caution()
    assert feed(m, [1.5] * 11, start_index=6)[-1] == INSPECT
    return m


def enter_fault():
    """사전 조건: 시작부터 점수 없음 → 72분(N=60분 초과)에 센서장애"""
    m = make_machine()
    assert feed(m, [None] * 4)[-1] == FAULT
    return m


def test_tc_st_06_01_ack_only_in_inspect():
    """TC-ST-06-01: 상태 4가지에서 ack 1번 → (반환값, ack 후 상태)"""
    expected = {  # 테스터 명세 그대로
        "정상": (make_machine, False, NORMAL),
        "주의": (enter_caution, False, CAUTION),
        "점검권고": (enter_inspect, True, NORMAL),  # 1차 실행 때 INSPECT로 적었다가 테스트 결함으로 수정 (DR-01)
        "센서장애": (enter_fault, False, FAULT),
    }
    for name, (setup, want_result, want_state) in expected.items():
        m = setup()
        assert (m.ack(), m.state) == (want_result, want_state), name


def test_tc_st_03_01_inspect_at_exactly_d2():
    """TC-ST-03-01: 120분 주의 후 144분부터 1.5 → 360분까지 주의, 384분에 점검권고 (D2 경계)"""
    states = feed(enter_caution(), [1.5] * 11, start_index=6)
    assert states == [CAUTION] * 10 + [INSPECT]


def test_tc_st_03_02_score_equal_t2_is_not_above():
    """TC-ST-03-02: 120분 주의 후 144분부터 1.0(= T2) → 384분이 지나도 주의 ('점수 > T2'이므로)"""
    states = feed(enter_caution(), [1.0] * 15, start_index=6)
    assert set(states) == {CAUTION}


def test_tc_st_08_01_fault_after_more_than_n():
    """TC-ST-08-01: 0분 0.3, 24·48·72분 점수 없음 → 48분까지 정상, 72분에 센서장애 (N=60분 앞뒤)"""
    states = feed(make_machine(), [0.3, None, None, None])
    assert states == [NORMAL] * 3 + [FAULT]


def enter_normal_with_data():
    """사전 조건: 0분에 점수 0.3 → 정상"""
    m = make_machine()
    feed(m, [0.3])
    return m


def test_tc_st_09_01_recover_to_state_before_fault():
    """TC-ST-09-01: 정상·주의·점검권고에서 센서장애 → 점수 0.3 복구 → 장애 직전 상태"""
    cases = {  # 이름: (사전 조건, 사전 조건이 끝난 측정 순번, 장애 직전 상태)
        "정상": (enter_normal_with_data, 0, NORMAL),
        "주의": (enter_caution, 5, CAUTION),
        "점검권고": (enter_inspect, 16, INSPECT),
    }
    for name, (setup, last_index, before) in cases.items():
        m = setup()
        states = feed(m, [None, None, None, 0.3], start_index=last_index + 1)
        assert states[2:] == [FAULT, before], name  # 72분째 센서장애, 다음 측정에서 복귀


def test_tc_st_01_01_starts_normal():
    """TC-ST-01-01: 막 시작한 상태 → 정상"""
    assert make_machine().state == NORMAL


def test_tc_st_11_01_fault_without_any_data():
    """TC-ST-11-01: 시작부터 0·24·48·72분 점수 없음 → 48분까지 정상, 72분에 센서장애"""
    assert feed(make_machine(), [None] * 4) == [NORMAL] * 3 + [FAULT]


def test_tc_st_10_01_fault_time_not_counted_in_recovery():
    """TC-ST-10-01: 120분 주의 → 144·168분 0.3 → 192~240분 없음 → 264분부터 0.3
    → 216분까지 주의, 240분 센서장애, 264분 주의 복귀, 360분까지 주의, 384분 정상 (264분부터 R을 다시 잼)"""
    states = feed(enter_caution(), [0.3, 0.3, None, None, None] + [0.3] * 6, start_index=6)
    assert states == [CAUTION] * 4 + [FAULT] + [CAUTION] * 5 + [NORMAL]


# ── 순간 튐 반복 (SWR-ST-12, TBD-01: 24시간 안에 3회 → 주의) ── 튐 1회 = [0.8, 0.1] (24분 초과 후 복귀)

def spikes_at(indexes, length):
    """indexes 순번에서 튐이 시작하는 점수 목록. 나머지는 0.1"""
    scores = [0.1] * length
    for i in indexes:
        scores[i] = 0.8
    return scores


def test_three_spikes_within_24h_go_caution():
    states = feed(make_machine(), spikes_at([0, 5, 10], 12))
    assert states[:11] == [NORMAL] * 11 and states[11] == CAUTION  # 세 번째 튐이 끝나는 순간 주의


def test_two_spikes_stay_normal():
    assert set(feed(make_machine(), spikes_at([0, 5], 40))) == {NORMAL}


def test_spikes_older_than_24h_are_forgotten():
    # 0분, 744분, 1488분 튐 → 세 번째가 기록될 때 첫 번째는 24시간(1440분)이 지나 빠짐 → 2회
    assert set(feed(make_machine(), spikes_at([0, 31, 62], 70))) == {NORMAL}


def test_caution_holds_while_spikes_repeat_then_recovers():
    # B안: 0·120·240분 튐 → 264분 주의. 점수는 곧 내려가지만 튐 3회가 24시간 안에 있는 동안 주의 유지.
    # 첫 튐(0분)이 1440분을 넘겨 빠지는 1464분(순번 61)에 2회가 되어 정상
    states = feed(make_machine(), spikes_at([0, 5, 10], 62))
    assert states[11:61] == [CAUTION] * 50 and states[61] == NORMAL
