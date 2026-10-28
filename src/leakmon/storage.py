"""SQLite 저장소. 디바이스 에이전트가 쓰고, 웹 서버가 이력 조회용으로 읽는다."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    run_id TEXT,
    ts_virtual TEXT NOT NULL,
    leakage_mA REAL,
    humidity_pct REAL,
    expected_mA REAL,
    anomaly_score REAL,
    state TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    run_id TEXT,
    ts_virtual TEXT NOT NULL,
    state TEXT NOT NULL,
    message TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL: 디바이스가 쓰는 동안에도 웹 서버가 읽을 수 있게
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    # 1단계에서 만든 DB에는 run_id 열이 없으므로 추가 (기존 행은 run_id가 비어 화면에 안 나옴)
    for table in ("telemetry", "alerts"):
        if "run_id" not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT")
    return conn


def save_telemetry(conn: sqlite3.Connection, t: dict) -> None:
    conn.execute(
        "INSERT INTO telemetry (device_id, run_id, ts_virtual, leakage_mA, humidity_pct, expected_mA, anomaly_score, state)"
        " VALUES (:device_id, :run_id, :ts_virtual, :leakage_mA, :humidity_pct, :expected_mA, :anomaly_score, :state)", t)
    conn.commit()


def save_alert(conn: sqlite3.Connection, a: dict) -> None:
    conn.execute("INSERT INTO alerts (device_id, run_id, ts_virtual, state, message)"
                 " VALUES (:device_id, :run_id, :ts_virtual, :state, :message)", a)
    conn.commit()


def latest_run_id(conn: sqlite3.Connection, device_id: str) -> str | None:
    """가장 최근 실행의 run_id (실행마다 가상 시간이 처음부터 다시 시작하므로 실행끼리 섞으면 안 됨)."""
    row = conn.execute("SELECT run_id FROM telemetry WHERE device_id = ? ORDER BY id DESC LIMIT 1",
                       (device_id,)).fetchone()
    return row["run_id"] if row else None


def recent_rows(conn: sqlite3.Connection, table: str, device_id: str, run_id: str, limit: int) -> list[dict]:
    """해당 실행의 최근 limit개를 시간 순서(오래된 것 먼저)로 돌려준다."""
    assert table in ("telemetry", "alerts")  # 테이블 이름은 SQL 파라미터로 못 넘기므로 화이트리스트
    rows = conn.execute(f"SELECT * FROM {table} WHERE device_id = ? AND run_id = ? ORDER BY id DESC LIMIT ?",
                        (device_id, run_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]
