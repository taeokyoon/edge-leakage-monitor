"""수동 입력 센서의 입력 해석."""
import pytest

from leakmon.sensors.manual import parse_line


def test_leakage_only_keeps_humidity():
    assert parse_line("7.0", 50.0) == {"leakage_mA": 7.0, "humidity_pct": 50.0, "source": "manual"}


def test_leakage_and_humidity():
    assert parse_line("7.0 80", 50.0)["humidity_pct"] == 80.0


def test_x_means_data_loss():
    assert parse_line("x", 50.0) is None


@pytest.mark.parametrize("bad, error", [("abc", ValueError), ("", IndexError)])
def test_bad_input_raises(bad, error):
    with pytest.raises(error):
        parse_line(bad, 50.0)
