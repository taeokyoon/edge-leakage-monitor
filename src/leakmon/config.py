"""config/*.yaml 읽기. 디바이스 에이전트와 웹 서버가 같이 쓴다."""
import os
from pathlib import Path

import yaml

# src/leakmon/config.py 기준 두 단계 위 = 저장소 루트 (editable 설치 전제)
ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"


def load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    # PC는 LEAKMON_HW=sim, Pi는 LEAKMON_HW=pi (환경변수가 파일보다 우선)
    settings["hardware"] = os.environ.get("LEAKMON_HW", settings["hardware"])
    settings["db_path"] = str(ROOT_DIR / settings["db_path"])
    return settings


def load_devices() -> dict:
    """device_id → 자산 정보(동/라인/설비) 딕셔너리."""
    with open(CONFIG_DIR / "devices.yaml", encoding="utf-8") as f:
        return {d["device_id"]: d for d in yaml.safe_load(f)}
