"""시나리오 생성기: 가상 설비의 누설전류·습도를 가상 시각의 함수로 만든다 (docs/ARCHITECTURE.md §7).

- PC(sim): 이 값이 그대로 센서 측정값이 된다.
- Pi(5단계): 이 값을 MCP4725 DAC 전압으로 출력하고 ADS1115로 다시 읽는다.
같은 시나리오·같은 seed면 같은 데이터가 나오므로, 3단계 학습·평가 데이터도 여기서 만든다.

모든 시나리오는 시작 후 가상 1일은 정상 운전이고, 그 뒤에 이상(또는 정상 변동)이 시작된다.
(#7 인증 없는 장치·타 토픽 발행은 데이터가 아니라 보안 시나리오라 4단계에서 다룬다)
"""
import math
import random
from datetime import datetime, timedelta

# 가상 설비의 "실제" 정상 특성. 이상 탐지기 쪽 설정(settings.yaml baseline)과 일부러 분리한다
# (탐지기는 설비의 정답을 모르는 상태에서 판정해야 하므로)
BASE_mA = 4.5               # 기준 습도·비가동 시 누설전류
HUMIDITY_REF_pct = 55.0     # 기준 습도
HUMIDITY_COEF_mA = 0.08     # 습도 1%p당 누설전류 증가 (흡습으로 절연 저항 감소)
RUNNING_EXTRA_mA = 0.4      # 가동 시간대(08~18시) 추가분
EVENT_START = timedelta(days=1)  # 이상이 시작되는 시점

SCENARIOS = {
    "normal": "정상 운전",
    "gradual_rise": "완만한 상승",
    "humidity_rise": "습도 상승 (합성 정상 변동)",
    "short_spike": "짧은 순간 급등",
    "repeated_spike": "반복 순간 급등",
    "sharp_rise": "지속적 급상승",
    "data_loss": "데이터 끊김",
}

# 평가 라벨 (3단계 작업 2): 이상 시작(EVENT_START) 뒤에 경보(주의 이상)가 떠야 정답인 시나리오.
# 나머지에서 경보가 뜨면 오경보 (humidity_rise = STK-02, short_spike = STK-03). data_loss는 센서장애로 따로 본다
ALARM_EXPECTED = {"gradual_rise", "repeated_spike", "sharp_rise"}


def normal_leakage(humidity_pct: float, hour: float) -> float:
    """습도를 반영한 합성 정상 변동: 정상 설비라면 누설전류가 이 값 근처에 있다."""
    running = 8 <= hour < 18
    return BASE_mA + HUMIDITY_COEF_mA * (humidity_pct - HUMIDITY_REF_pct) + (RUNNING_EXTRA_mA if running else 0.0)


class Scenario:
    def __init__(self, name: str, start: datetime, seed: int = 0):
        if name not in SCENARIOS:
            raise ValueError(f"알 수 없는 시나리오: {name} (가능: {', '.join(SCENARIOS)})")
        self.name = name
        self.start = start
        # SA-04 오탐: 보안용이 아니라 시뮬레이션 재현용 난수 (seed 고정 → 노이즈까지 재현 가능)
        self.rng = random.Random(seed)  # noqa: S311

    def sample(self, now: datetime) -> dict | None:
        """가상 시각 now의 측정값. 데이터 끊김 구간이면 None (센서 장애)."""
        since_event = now - self.start - EVENT_START  # 음수면 아직 이상 시작 전
        hour = now.hour + now.minute / 60

        if self.name == "data_loss" and timedelta(0) <= since_event < timedelta(hours=3):
            return None

        humidity = self._humidity(hour, since_event) + self.rng.gauss(0, 0.3)
        leakage = normal_leakage(humidity, hour) + self._fault_mA(since_event) + self.rng.gauss(0, 0.05)
        return {"leakage_mA": round(leakage, 3), "humidity_pct": round(humidity, 1), "source": "sim"}

    def _humidity(self, hour: float, since_event: timedelta) -> float:
        daily = 5 * math.cos(2 * math.pi * (hour - 6) / 24)  # 새벽 6시 최고, 오후 6시 최저 (±5%p)
        if self.name == "humidity_rise" and since_event > timedelta(0):
            daily += 35 * min(since_event / timedelta(days=2), 1.0)  # 2일에 걸쳐 +35%p 오른 뒤 유지
        return HUMIDITY_REF_pct + daily

    def _fault_mA(self, since_event: timedelta) -> float:
        """절연 열화 등 이상으로 정상값 위에 더해지는 누설전류."""
        if since_event <= timedelta(0):
            return 0.0
        hours = since_event / timedelta(hours=1)
        if self.name == "gradual_rise":
            return 0.5 * hours / 24             # 하루 0.5mA씩 꾸준히 상승
        if self.name == "short_spike":
            return 6.0 if hours < 1 else 0.0    # 1시간만 +6mA 튀었다가 복귀
        if self.name == "repeated_spike":
            return 6.0 if (hours % 6) < 1 else 0.0  # 6시간마다 1시간 씩 +6mA 튐
        if self.name == "sharp_rise":
            return min(2.0 * hours, 7.0)        # 시간당 2mA 상승, 최대 +7mA에서 유지
        return 0.0


def generate(name: str, days: float, seed: int = 0, step: timedelta = timedelta(minutes=24),
             start: datetime = datetime(2026, 11, 1)) -> list[dict]:
    """시나리오를 step 간격으로 days일 재생한 데이터셋. 기본 step 24분 = 실제 1초 × 배속 1440 (SYS-01).

    각 행: ts(가상 시각), leakage_mA·humidity_pct(데이터 끊김이면 None), anomaly(평가 라벨)
    """
    scenario = Scenario(name, start, seed)
    rows = []
    now = start
    while now < start + timedelta(days=days):
        reading = scenario.sample(now) or {"leakage_mA": None, "humidity_pct": None}
        rows.append({"ts": now, "leakage_mA": reading["leakage_mA"], "humidity_pct": reading["humidity_pct"],
                     "anomaly": name in ALARM_EXPECTED and now - start > EVENT_START})
        now += step
    return rows
