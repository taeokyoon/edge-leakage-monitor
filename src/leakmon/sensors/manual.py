"""수동 입력 센서: 터미널에 입력한 값을 다음 입력 전까지 계속 측정값으로 돌려준다 (상태 전이 직접 관찰용).

입력 예: "7.0" (누설전류 mA, 습도는 그대로), "7.0 80" (누설전류 + 습도 %), "x" (데이터 끊김 → 센서장애 확인용)
"""
import threading
from datetime import datetime


def parse_line(text: str, humidity_pct: float) -> dict | None:
    """입력 한 줄 → 측정값. "x"면 None(데이터 미수신). 숫자가 아니면 ValueError, 빈 줄이면 IndexError."""
    parts = text.split()
    if parts == ["x"]:
        return None
    leakage = float(parts[0])
    humidity = float(parts[1]) if len(parts) > 1 else humidity_pct
    return {"leakage_mA": leakage, "humidity_pct": humidity, "source": "manual"}


class ManualSensors:
    def __init__(self, leakage_mA: float = 4.5, humidity_pct: float = 50.0):
        self.humidity_pct = humidity_pct
        self.latest: dict | None = parse_line(f"{leakage_mA} {humidity_pct}", humidity_pct)
        # 메인 루프가 sleep 중에도 입력을 받도록 별도 스레드에서 읽는다 (daemon: Ctrl+C로 같이 종료)
        threading.Thread(target=self._read_keyboard, daemon=True).start()

    def _read_keyboard(self) -> None:
        while True:
            try:
                line = input()
            except EOFError:
                return
            try:
                self.latest = parse_line(line, self.humidity_pct)
            except (ValueError, IndexError):
                print(f"입력 무시: '{line}' (예: 7.0 / 7.0 80 / x)")
                continue
            if self.latest is not None:
                self.humidity_pct = self.latest["humidity_pct"]

    def read(self, now: datetime) -> dict | None:
        return self.latest
