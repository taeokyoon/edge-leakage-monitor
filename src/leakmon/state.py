"""상태 머신 (docs/ARCHITECTURE.md §3).

정상 → 주의 → 점검권고 로 올라가고, 각 전이는 조건이 "지속 시간" 동안 유지돼야 확정된다.
(순간 노이즈로 경보가 울리지 않게 하기 위함)
점검권고는 웹 대시보드의 ack로만 정상으로 내려온다.
데이터가 일정 시간 안 들어오면 어느 상태에서든 센서장애, 데이터가 다시 오면 이전 상태로 복귀.
모든 시각(now)은 가상 시간 datetime이다.
"""
from datetime import datetime, timedelta

NORMAL = "normal"
CAUTION = "caution"
INSPECT = "inspect"
FAULT = "fault"


def _since(started_at: datetime | None, condition: bool, now: datetime) -> datetime | None:
    """조건이 처음 참이 된 시각을 기억한다. 조건이 깨지면 None으로 초기화."""
    if not condition:
        return None
    return started_at or now


class StateMachine:
    def __init__(self, t1: float, t2: float, d1: timedelta, d2: timedelta,
                 recover: timedelta, fault_after: timedelta):
        self.t1, self.t2 = t1, t2
        self.d1, self.d2 = d1, d2
        self.recover = recover
        self.fault_after = fault_after

        self.state = NORMAL
        self.state_before_fault = NORMAL
        self.last_data_at: datetime | None = None
        # 각 조건이 언제부터 계속 참이었는지 (None = 지금은 거짓)
        self.above_t1_since: datetime | None = None
        self.above_t2_since: datetime | None = None
        self.below_t1_since: datetime | None = None

    @classmethod
    def from_settings(cls, s: dict) -> "StateMachine":
        return cls(s["t1"], s["t2"],
                   timedelta(minutes=s["d1_min"]), timedelta(minutes=s["d2_min"]),
                   timedelta(minutes=s["recover_min"]), timedelta(minutes=s["fault_after_min"]))

    def update(self, now: datetime, score: float | None) -> str:
        """샘플 하나를 반영하고 현재 상태를 돌려준다. score=None은 데이터 미수신."""
        if self.last_data_at is None:
            self.last_data_at = now  # 시작 직후부터 미수신이어도 센서장애를 잡기 위해

        # 1) 데이터 미수신: 일정 시간 넘으면 센서장애
        if score is None:
            if self.state != FAULT and now - self.last_data_at > self.fault_after:
                self.state_before_fault = self.state
                self.state = FAULT
            return self.state

        # 2) 데이터 수신: 센서장애였으면 이전 판정으로 복귀
        #    데이터가 없던 시간은 지속 시간이 아니므로 타이머는 지금부터 다시 잰다
        self.last_data_at = now
        if self.state == FAULT:
            self.state = self.state_before_fault
            self.above_t1_since = self.above_t2_since = self.below_t1_since = None

        # 3) 조건별 지속 시작 시각 갱신
        self.above_t1_since = _since(self.above_t1_since, score > self.t1, now)
        self.above_t2_since = _since(self.above_t2_since, score > self.t2, now)
        self.below_t1_since = _since(self.below_t1_since, score <= self.t1, now)

        # 4) 지속 시간을 채웠을 때만 전이
        if self.state == NORMAL and self._held(self.above_t1_since, self.d1, now):
            self.state = CAUTION
            # D2는 "주의 상태에서" 유지된 시간이므로 주의 진입 시점부터 다시 잰다
            self.above_t2_since = now if score > self.t2 else None
        elif self.state == CAUTION:
            if self._held(self.above_t2_since, self.d2, now):
                self.state = INSPECT
            elif self._held(self.below_t1_since, self.recover, now):
                self.state = NORMAL
        return self.state

    def ack(self) -> bool:
        """점검권고 수동 확인. 정상으로 돌리고 지속 시간을 처음부터 다시 잰다."""
        if self.state != INSPECT:
            return False
        self.state = NORMAL
        self.above_t1_since = self.above_t2_since = None  # D1 재적용
        return True

    @staticmethod
    def _held(since: datetime | None, duration: timedelta, now: datetime) -> bool:
        return since is not None and now - since >= duration
