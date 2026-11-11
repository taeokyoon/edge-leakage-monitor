"""Pi 실제 하드웨어 센서 (5단계, docs/ARCHITECTURE.md A1 경로).

leakage_input = dac (기본, AI 지표 기준 경로):
    시나리오 목표값(mA) → 전압 → MCP4725(0x60) 출력 → 전선 → ADS1115(0x48) A1로 다시 읽음 → mA
    값은 시나리오가 정하지만, 측정값은 실제 DAC·배선·ADC를 통과한 값이다 (오차·노이즈가 실제로 섞임)
leakage_input = pot (수동 시연): 가변저항 전압을 ADS1115 A2로 읽음 → mA

humidity = scenario (기본): 시나리오 합성 습도. 재현 가능 → AI 성능 지표용
humidity = dht20: DHT20(0x38) 실측 습도를 시나리오에 넘겨, 그 습도에 맞는 정상 누설전류를 만든다 (시연용)
    → 입김으로 습도가 오르면 누설전류도 오르지만 AI 예상값도 같이 올라 경보가 안 뜬다 (STK-02 실물 시연)
    주의: 학습 기간에 습도가 거의 안 변하면 AI가 습도 계수를 못 배운다 → 학습 중 입김을 한 번 불어 줄 것

부품별 I2C 명령은 Pi에서 개별 테스트로 검증한 값 (DAC→ADC 오차 0.5% 미만).
센서 장애 → None을 돌려줘 상태 머신이 센서 장애(fault)로 판정하게 한다:
- I2C 오류 (I2C 선 빠짐 등): 실패한 부품 이름을 출력
- DAC 경로 자가진단 (dac 모드): 내보낸 값과 읽은 값이 loopback_tol_mA 이상 다르면 아날로그 배선 이상
  (A1 선이 빠지면 핀이 떠서 0이 아닌 엉뚱한 값(실측 약 3mA)이 읽히므로 "낮은 값" 규칙으로는 못 잡는다)
"""
import time
from datetime import datetime
from typing import Any

from ..scenario import Scenario

DAC_ADDR = 0x60      # MCP4725
ADC_ADDR = 0x48      # ADS1115 (ADDR → GND)
DHT_ADDR = 0x38      # DHT20
DAC_VREF = 3.3       # MCP4725 기준 전압 = VDD (Pi 3.3V)
ADC_FS = 4.096       # ADS1115 ±4.096V 범위
# ADS1115 설정 레지스터(0x01) 값: 단발 변환, ±4.096V, 128SPS. 상위 바이트가 입력 채널을 고른다
ADC_CONFIG = {"A1": [0xD3, 0x83], "A2": [0xE3, 0x83]}

DEFAULTS = {"i2c_bus": 1, "leakage_input": "dac", "humidity": "scenario", "mA_per_V": 5.0, "loopback_tol_mA": 0.5}


def mA_to_dac_code(mA: float, mA_per_V: float) -> int:
    volts = mA / mA_per_V
    return max(0, min(4095, round(volts / DAC_VREF * 4095)))  # DAC 범위를 넘으면 끝값으로 자른다


def adc_raw_to_volts(msb: int, lsb: int) -> float:
    raw = (msb << 8) | lsb
    if raw >= 0x8000:  # 2의 보수 (음수 전압)
        raw -= 0x10000
    return raw * ADC_FS / 32768


def dht20_humidity(data: list[int]) -> float:
    return ((data[1] << 12) | (data[2] << 4) | (data[3] >> 4)) / 2**20 * 100


class SensorError(OSError):
    """어느 부품에서 실패했는지 이름을 담은 I2C 오류."""


class PiSensors:
    def __init__(self, scenario: Scenario, pi_settings: dict | None = None, bus: Any = None):
        self.scenario = scenario
        self.cfg = {**DEFAULTS, **(pi_settings or {})}
        if bus is None:
            from smbus2 import SMBus  # Pi에서만 필요 → PC(sim)에서는 설치 안 해도 되게 여기서 import
            bus = SMBus(self.cfg["i2c_bus"])
        self.bus = bus

    def read(self, now: datetime) -> dict | None:
        try:
            measured = self._read_dht20() if self.cfg["humidity"] == "dht20" else None
            target = self.scenario.sample(now, measured)
            if target is None:  # data_loss 시나리오의 끊김 구간 → 장치도 측정 실패로 처리
                return None
            if self.cfg["leakage_input"] == "pot":
                volts = self._read_adc("A2")
            else:
                code = mA_to_dac_code(target["leakage_mA"], self.cfg["mA_per_V"])
                self._write_dac(code)
                time.sleep(0.005)  # DAC 출력 안정 대기
                volts = self._read_adc("A1")
            humidity = target["humidity_pct"]  # dht20 모드면 실측값이 그대로 들어 있다
        except OSError as e:  # I2C 응답 없음 (Errno 5/121 등) → 센서 장애. SensorError면 부품 이름 포함
            print(f"[pi] I2C 읽기 실패: {e}")
            return None
        leakage = volts * self.cfg["mA_per_V"]
        if self.cfg["leakage_input"] == "dac":
            sent = code / 4095 * DAC_VREF * self.cfg["mA_per_V"]  # DAC 범위로 잘린 경우까지 반영한 실제 출력값
            if abs(leakage - sent) > self.cfg["loopback_tol_mA"]:
                print(f"[pi] DAC→ADC 불일치: 출력 {sent:.2f}mA, 측정 {leakage:.2f}mA"
                      " — MCP4725 VOUT ↔ ADS1115 A1 배선 확인")
                return None
        return {"leakage_mA": round(leakage, 3), "humidity_pct": round(humidity, 1), "source": "pi"}

    def _write_dac(self, code: int) -> None:
        # MCP4725 fast write: [0000 D11..D8] [D7..D0]
        try:
            self.bus.write_i2c_block_data(DAC_ADDR, (code >> 8) & 0x0F, [code & 0xFF])
        except OSError as e:
            raise SensorError(f"MCP4725(0x60) — {e}") from e

    def _read_adc(self, channel: str) -> float:
        try:
            self.bus.write_i2c_block_data(ADC_ADDR, 0x01, ADC_CONFIG[channel])  # 변환 시작
            time.sleep(0.01)  # 128SPS → 변환 약 8ms
            msb, lsb = self.bus.read_i2c_block_data(ADC_ADDR, 0x00, 2)
        except OSError as e:
            raise SensorError(f"ADS1115(0x48) — {e}") from e
        return adc_raw_to_volts(msb, lsb)

    def _read_dht20(self) -> float:
        from smbus2 import i2c_msg
        try:
            self.bus.write_i2c_block_data(DHT_ADDR, 0xAC, [0x33, 0x00])  # 측정 시작
            time.sleep(0.08)
            msg = i2c_msg.read(DHT_ADDR, 7)
            self.bus.i2c_rdwr(msg)
        except OSError as e:
            raise SensorError(f"DHT20(0x38) — {e}") from e
        data = list(msg)
        if data[0] & 0x80:  # busy 비트: 아직 측정 중
            raise SensorError("DHT20(0x38) — 측정 미완료")
        return dht20_humidity(data)
