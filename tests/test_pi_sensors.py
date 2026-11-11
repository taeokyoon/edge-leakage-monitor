"""sensors/pi.py 단위 테스트: 실제 I2C 대신 가짜 버스(DAC 출력이 그대로 ADC A1에 들어가는 배선)를 쓴다.

Pi 실기 확인은 별도: LEAKMON_HW=pi python -m leakmon.main --scenario normal
"""
from datetime import datetime, timedelta

from leakmon.scenario import Scenario
from leakmon.sensors.pi import (ADC_ADDR, DAC_ADDR, PiSensors, adc_raw_to_volts, dht20_humidity,
                                mA_to_dac_code)

START = datetime(2026, 11, 8)


class FakeBus:
    """MCP4725 출력 전압이 ADS1115 A1에 연결된 배선 + A2에 고정 전압(가변저항)."""

    def __init__(self, pot_volts: float = 1.0, broken: bool = False, a1_floating_volts: float | None = None):
        self.dac_volts = 0.0
        self.a1_floating_volts = a1_floating_volts  # A1 선이 빠졌을 때 떠 있는 핀 전압
        self.pot_volts = pot_volts
        self.channel = "A1"
        self.broken = broken

    def write_i2c_block_data(self, addr, reg, data):
        if self.broken:
            raise OSError(121, "Remote I/O error")
        if addr == DAC_ADDR:
            self.dac_volts = (((reg & 0x0F) << 8) | data[0]) / 4095 * 3.3
        elif addr == ADC_ADDR and reg == 0x01:
            self.channel = "A1" if data[0] == 0xD3 else "A2"

    def read_i2c_block_data(self, addr, reg, length):
        if self.channel == "A1":
            volts = self.dac_volts if self.a1_floating_volts is None else self.a1_floating_volts
        else:
            volts = self.pot_volts
        raw = round(volts / 4.096 * 32768)
        return [(raw >> 8) & 0xFF, raw & 0xFF]


def test_dac_code_range_is_clamped():
    assert mA_to_dac_code(0, 5.0) == 0
    assert mA_to_dac_code(16.5, 5.0) == 4095
    assert mA_to_dac_code(100, 5.0) == 4095  # DAC 범위 초과 → 끝값
    assert mA_to_dac_code(-1, 5.0) == 0


def test_adc_raw_conversion_including_negative():
    assert adc_raw_to_volts(0x7F, 0xFF) == 32767 * 4.096 / 32768
    assert adc_raw_to_volts(0xFF, 0xFF) < 0  # 2의 보수


def test_dht20_formula_matches_verified_script():
    # 습도 50% = 2^19 → d1=0x80, d2=0x00, d3 상위 4비트 0
    assert dht20_humidity([0x1C, 0x80, 0x00, 0x00, 0, 0, 0]) == 50.0


def test_dac_to_adc_round_trip_follows_scenario():
    """A1 경로로 읽은 값이 시나리오 목표값과 DAC 분해능(약 0.004mA) 수준으로 일치."""
    scenario, reference = Scenario("sharp_rise", START), Scenario("sharp_rise", START)  # seed 고정 → 같은 값
    sensors = PiSensors(scenario, bus=FakeBus())
    for hours in range(0, 72, 6):
        now = START + timedelta(hours=hours)
        target = reference.sample(now)
        got = sensors.read(now)
        assert got is not None and target is not None
        assert got["source"] == "pi"
        assert abs(got["leakage_mA"] - target["leakage_mA"]) < 0.01
        assert got["humidity_pct"] == target["humidity_pct"]  # humidity: scenario


def test_pot_mode_reads_a2():
    sensors = PiSensors(Scenario("normal", START), {"leakage_input": "pot"}, bus=FakeBus(pot_volts=2.0))
    got = sensors.read(START)
    assert got is not None and abs(got["leakage_mA"] - 10.0) < 0.01  # 2.0V × 5mA/V


def test_i2c_error_is_sensor_fault():
    sensors = PiSensors(Scenario("normal", START), bus=FakeBus(broken=True))
    assert sensors.read(START) is None


def test_data_loss_scenario_returns_none():
    sensors = PiSensors(Scenario("data_loss", START), bus=FakeBus())
    assert sensors.read(START + timedelta(days=1, hours=1)) is None


def test_dht20_mode_uses_measured_humidity_for_leakage(monkeypatch):
    """dht20 모드: 실측 습도가 올라가면 누설전류도 습도 계수(0.08mA/%p)만큼 따라 오른다."""
    results = {}
    for humidity in (50.0, 60.0):
        sensors = PiSensors(Scenario("normal", START), {"humidity": "dht20"}, bus=FakeBus())
        monkeypatch.setattr(sensors, "_read_dht20", lambda h=humidity: h)
        got = sensors.read(START + timedelta(hours=3))
        assert got is not None and got["humidity_pct"] == humidity
        results[humidity] = got["leakage_mA"]
    assert abs((results[60.0] - results[50.0]) - 0.8) < 0.02  # seed가 같아 노이즈가 같으므로 차이 = 10%p × 0.08


def test_dht20_failure_is_sensor_fault(monkeypatch):
    sensors = PiSensors(Scenario("normal", START), {"humidity": "dht20"}, bus=FakeBus())

    def broken():
        raise OSError("DHT20 측정 미완료")
    monkeypatch.setattr(sensors, "_read_dht20", broken)
    assert sensors.read(START) is None


def test_unplugged_a1_is_sensor_fault(capsys):
    """Pi 실측(2026-11-09): A1 선을 빼면 떠 있는 핀이 약 3mA(0.6V)로 읽힘 → 자가진단으로 센서 장애."""
    sensors = PiSensors(Scenario("sharp_rise", START), bus=FakeBus(a1_floating_volts=0.6))
    assert sensors.read(START + timedelta(days=1, hours=6)) is None  # 출력 약 12mA, 측정 3mA
    assert "DAC→ADC 불일치" in capsys.readouterr().out


def test_loopback_tolerates_normal_error():
    """정상 오차(0.05mA 수준)는 통과."""
    bus = FakeBus()
    sensors = PiSensors(Scenario("normal", START), bus=bus)
    original = bus.read_i2c_block_data

    def slightly_off(addr, reg, length):  # ADC가 약 0.01V(= 0.05mA) 높게 읽는 경우
        msb, lsb = original(addr, reg, length)
        raw = ((msb << 8) | lsb) + 80
        return [(raw >> 8) & 0xFF, raw & 0xFF]
    bus.read_i2c_block_data = slightly_off
    assert sensors.read(START) is not None


def test_i2c_error_names_the_part(capsys):
    sensors = PiSensors(Scenario("normal", START), bus=FakeBus(broken=True))
    assert sensors.read(START) is None
    assert "MCP4725(0x60)" in capsys.readouterr().out
