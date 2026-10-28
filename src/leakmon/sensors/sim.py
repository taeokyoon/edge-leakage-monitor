"""PC 개발용 가상 센서: 시나리오 생성기의 값을 그대로 측정값으로 돌려준다."""
from datetime import datetime

from ..scenario import Scenario


class SimSensors:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario

    def read(self, now: datetime) -> dict | None:
        return self.scenario.sample(now)
