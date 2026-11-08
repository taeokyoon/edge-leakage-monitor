"""SA-02 수정 확인: 테이블 화이트리스트가 assert 없이도 동작하는지."""
import pytest

from leakmon import storage


def test_recent_rows_allows_known_tables(tmp_path):
    conn = storage.connect(str(tmp_path / "t.db"))
    assert storage.recent_rows(conn, "telemetry", "press-01", "run-1", 10) == []
    assert storage.recent_rows(conn, "alerts", "press-01", "run-1", 10) == []


def test_recent_rows_rejects_unknown_table(tmp_path):
    conn = storage.connect(str(tmp_path / "t.db"))
    with pytest.raises(ValueError):
        storage.recent_rows(conn, "telemetry; DROP TABLE alerts", "press-01", "run-1", 10)
