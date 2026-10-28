"""센서 백엔드 선택. 모든 백엔드는 read(now) -> dict | None 하나만 제공한다.

now: 가상 시각 (시나리오 생성기가 이 시각의 신호를 만든다)
read() 반환값: {"leakage_mA": float, "humidity_pct": float, "source": str}
               읽기 실패(센서 장애)면 None
"""
from ..scenario import Scenario
from .sim import SimSensors


def make_sensors(hardware: str, scenario: Scenario):
    if hardware == "sim":
        return SimSensors(scenario)
    # Pi 실제 하드웨어(시나리오 → MCP4725 출력 → ADS1115·DHT20 읽기)는 5단계에서 추가
    raise NotImplementedError(f"하드웨어 백엔드 '{hardware}'는 아직 구현되지 않았습니다")
