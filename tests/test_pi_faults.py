"""고장 주입 테스트: sensors/pi.py가 부품별 I2C 고장을 센서 장애(None)로 바꾸고, 어느 부품인지 알려주는가.

가짜 버스에 "이 주소(부품)는 응답 없음"을 지정해 고장을 주입한다 (실제 I2C 선 빠짐 = OSError 121).
DHT20은 smbus2.i2c_msg를 쓰므로 가짜 smbus2 모듈을 끼워 실제 _read_dht20() 코드를 그대로 실행한다.
"""
import sys
import types
from datetime import datetime

import pytest

from leakmon.scenario import Scenario
from leakmon.sensors.pi import ADC_ADDR, DAC_ADDR, DHT_ADDR, PiSensors
from test_pi_sensors import FakeBus

START = datetime(2026, 11, 8)
DHT20_50PCT = [0x1C, 0x80, 0x00, 0x00, 0, 0, 0]  # 습도 50% 응답 (test_pi_sensors의 공식 검증값과 같음)


class FakeMsg(list):
    """smbus2.i2c_msg.read() 흉내: i2c_rdwr()가 바이트를 채우고 list(msg)로 읽는다."""

    def __init__(self, addr: int, length: int):
        super().__init__([0] * length)
        self.addr = addr


@pytest.fixture(autouse=True)
def fake_smbus2(monkeypatch):
    """PC에는 smbus2가 없으므로 i2c_msg만 가진 가짜 모듈을 끼운다."""
    module = types.ModuleType("smbus2")
    module.i2c_msg = types.SimpleNamespace(read=FakeMsg)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "smbus2", module)


class FaultBus(FakeBus):
    """FakeBus + 고장 주입.

    fail: 응답 없는 주소(부품)들 / fail_times: 앞에서 몇 번만 실패 (일시 고장), None이면 계속 실패
    fail_rdwr: DHT20 결과 읽기 단계만 실패 / dht_data: DHT20이 돌려줄 7바이트
    """

    def __init__(self, fail: tuple[int, ...] = (), fail_times: int | None = None, fail_rdwr: bool = False,
                 dht_data: list[int] | None = None):
        super().__init__()
        self.fail, self.fail_times, self.fail_rdwr = fail, fail_times, fail_rdwr
        self.dht_data = dht_data or DHT20_50PCT

    def _check(self, addr: int) -> None:
        if addr not in self.fail or self.fail_times == 0:
            return
        if self.fail_times is not None:
            self.fail_times -= 1
        raise OSError(121, "Remote I/O error")

    def write_i2c_block_data(self, addr, reg, data):
        self._check(addr)
        super().write_i2c_block_data(addr, reg, data)

    def read_i2c_block_data(self, addr, reg, length):
        self._check(addr)
        return super().read_i2c_block_data(addr, reg, length)

    def i2c_rdwr(self, msg):
        if self.fail_rdwr:
            raise OSError(121, "Remote I/O error")
        self._check(msg.addr)
        msg[:] = self.dht_data


def make(bus: FaultBus, **cfg) -> PiSensors:
    return PiSensors(Scenario("normal", START), cfg, bus)


@pytest.mark.parametrize("part, addr", [("MCP4725(0x60)", DAC_ADDR), ("ADS1115(0x48)", ADC_ADDR)])
def test_dac_mode_single_part_fault(capsys, part, addr):
    """dac 모드: DAC만 / ADC만 고장 → 센서 장애, 출력에 고장 부품 이름."""
    assert make(FaultBus(fail=(addr,))).read(START) is None
    assert part in capsys.readouterr().out


def test_pot_mode_adc_fault(capsys):
    assert make(FaultBus(fail=(ADC_ADDR,)), leakage_input="pot").read(START) is None
    assert "ADS1115(0x48)" in capsys.readouterr().out


def test_dht20_real_read_path():
    """고장 없음: 실제 _read_dht20() 경로로 읽은 습도 50%가 그대로 쓰인다 (기존 테스트는 이 함수를 바꿔치기했음)."""
    got = make(FaultBus(), humidity="dht20").read(START)
    assert got is not None and got["humidity_pct"] == 50.0


@pytest.mark.parametrize("bus, message", [
    (FaultBus(fail=(DHT_ADDR,)), "DHT20(0x38)"),                   # 측정 시작 명령부터 응답 없음
    (FaultBus(fail_rdwr=True), "DHT20(0x38)"),                     # 시작은 성공, 결과 읽기에서 실패
    (FaultBus(dht_data=[0x80] + DHT20_50PCT[1:]), "측정 미완료"),  # busy 비트: 아직 측정 중
])
def test_dht20_fault(capsys, bus, message):
    assert make(bus, humidity="dht20").read(START) is None
    assert message in capsys.readouterr().out


def test_transient_fault_recovers_on_next_read():
    """일시 고장(접촉 불량 1회): 그 측정만 센서 장애, 다음 측정은 정상 → 상태 머신이 센서장애에서 복구할 수 있다."""
    sensors = make(FaultBus(fail=(DAC_ADDR,), fail_times=1))
    assert sensors.read(START) is None
    assert sensors.read(START) is not None
