"""시나리오 재생: 같은 시나리오를 방식별로 상태 머신에 넣어 결과를 비교한다 (MQTT·DB 없이 빠르게).

방식: baseline = 고정 임계값 / ewma·ewma_std = EWMA 관리도 (학습 최대값·표준 L=3 기준)
      / iforest = Isolation Forest (학습 최대값 기준)
      / iforest_std = Isolation Forest (표준 s>0.5 기준) / linear = 선형 회귀 + 잔차 (제안 방법)
      / linear_nohum = 제안 방법에서 습도 입력만 뺀 것 (제거 실험: 습도 반영의 효과를 따로 보기 위함)
AI 방식은 처음 learn_days일이 학습 기간이다 (점수 0 = 판정 없이 정상 유지, 미수신은 그대로 넣어 센서장애는 잡는다).
테스트(tests/test_scenarios.py)와 평가 도구(tools/evaluate.py)가 같이 쓴다.
"""
import csv
import json
import platform
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from .ai import ewma
from .ai.baseline import fixed_threshold_score
from .ai.iforest import train_scorer
from .ai.model import residual_score, train
from .scenario import ALARM_EXPECTED, DEFAULT_PLANT, Plant, generate
from .state import StateMachine

METHODS = ("baseline", "ewma", "ewma_std", "iforest", "iforest_std", "linear_nohum", "linear")


def replay(name: str, days: float, settings: dict, method: str = "linear", seed: int = 0,
           rows: list[dict] | None = None, plant: Plant = DEFAULT_PLANT) -> list[dict]:
    """generate() 행마다 score·state를 붙여 돌려준다.

    rows: 이미 만든 신호 (HIL: generate() 값을 Pi DAC→ADC로 통과시킨 측정값). 없으면 generate()로 만든다 (SIL)
    plant: 가상 설비 특성 (강건성 실험). rows를 줄 때는 그 신호를 만든 plant를 같이 줘야 한다
    """
    if timedelta(days=settings["ai"]["learn_days"]) > plant.event_start:
        # 학습 기간에 이상 데이터가 섞이면 평가가 무효가 된다
        raise ValueError(f"learn_days는 시나리오의 정상 구간({plant.normal_days}일) 이하여야 함")
    # 복사: 방식들이 같은 신호를 재사용하므로 score·state를 붙일 때 원본을 건드리지 않는다
    rows = [dict(r) for r in rows] if rows is not None else generate(name, days, seed, plant=plant)
    learn_end = rows[0]["ts"] + timedelta(days=settings["ai"]["learn_days"])
    learning = [r for r in rows if r["ts"] < learn_end and r["leakage_mA"] is not None]

    score_all: Callable[[list[dict]], list[float]]
    if method == "baseline":
        b = settings["baseline"]
        score_all = lambda batch: [fixed_threshold_score(r["leakage_mA"], b["normal_mA"], b["alarm_mA"])  # noqa: E731
                                   for r in batch]
    elif method in ("ewma", "ewma_std"):
        score_all = ewma.train_scorer(learning, settings["state"]["t1"], standard=method == "ewma_std")
    elif method in ("iforest", "iforest_std"):
        score_all = train_scorer(learning, settings["state"]["t1"], standard=method == "iforest_std")
    elif method == "linear_nohum":
        # 습도를 모든 행에서 0으로 → 습도 계수가 0이 되어 "가동 여부만 아는 회귀"가 된다 (나머지는 제안 방법과 동일)
        model, scale = train([{**r, "humidity_pct": 0.0} for r in learning]), settings["ai"]["sigma_scale"]
        score_all = lambda batch: [residual_score(model, r["leakage_mA"], 0.0, r["ts"], scale)  # noqa: E731
                                   for r in batch]
    else:
        model, scale = train(learning), settings["ai"]["sigma_scale"]
        score_all = lambda batch: [residual_score(model, r["leakage_mA"], r["humidity_pct"], r["ts"], scale)  # noqa: E731
                                   for r in batch]

    # 점수: 미수신 = None, AI 학습 기간 = 0, 나머지 = 방식별 점수
    judged = [r for r in rows if r["leakage_mA"] is not None and (method == "baseline" or r["ts"] >= learn_end)]
    for r in rows:
        r["score"] = None if r["leakage_mA"] is None else 0.0
    for r, score in zip(judged, score_all(judged), strict=True):
        r["score"] = score

    machine = StateMachine.from_settings(settings["state"])
    for r in rows:
        r["state"] = machine.update(r["ts"], r["score"])
    return rows


def transitions(rows: list[dict]) -> list[tuple[timedelta, str]]:
    """상태가 바뀐 시점만 [(시작 후 경과, 새 상태)]로. 시작 상태는 정상 (SWR-ST-01)."""
    out, before = [], "normal"
    for r in rows:
        if r["state"] != before:
            out.append((r["ts"] - rows[0]["ts"], r["state"]))
            before = r["state"]
    return out


ALARM_STATES = ("caution", "inspect")


def summarize(name: str, rows: list[dict], plant: Plant = DEFAULT_PLANT) -> dict:
    """재생 1회의 평가값 (docs/ARCHITECTURE.md 8장)

    - alarm: 이상 시작 뒤 경보(주의 이상)가 한 번이라도 떴나
    - delay: 이상 시작부터 첫 경보까지 (경보가 정답인 시나리오만)
    - tp/fp/fn: 측정 하나하나를 "경보 상태인가 vs 라벨"로 센 값 (Precision·Recall용)
    """
    event_at = rows[0]["ts"] + plant.event_start
    alarm_rows = [r for r in rows if r["state"] in ALARM_STATES and r["ts"] > event_at]
    return {
        "alarm": bool(alarm_rows),
        "delay": alarm_rows[0]["ts"] - event_at if alarm_rows and name in ALARM_EXPECTED else None,
        "tp": sum(r["state"] in ALARM_STATES and r["anomaly"] for r in rows),
        "fp": sum(r["state"] in ALARM_STATES and not r["anomaly"] for r in rows),
        "fn": sum(r["state"] not in ALARM_STATES and r["anomaly"] for r in rows),
    }


def _git(*args: str) -> str:
    """저장소의 git 정보. git이 없거나 저장소가 아니면 "unknown"."""
    try:
        # SA-06 S603 오탐: args는 호출부의 고정 문자열뿐이고 shell=False → 주입 경로 없음.
        # SA-06 S607 수용: "git"은 OS마다 설치 위치가 달라 PATH 탐색을 의도함 (PATH 변조 = 이미 PC 장악 상태)
        out = subprocess.run(["git", *args], capture_output=True, text=True, check=True,  # noqa: S603, S607
                             cwd=Path(__file__).parent)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def save_results(out_dir: Path, runs: list[dict], conditions: dict, settings: dict) -> None:
    """실험 결과를 논문 근거로 남긴다 (docs/PAPER.md 6장 결과 저장 규칙).

    runs.csv : 재생 1회 = 1행 (원자료). 논문 표의 숫자는 이 파일에서 다시 계산할 수 있어야 한다
    meta.json: 어떤 코드(커밋)·조건·설정으로 돌렸는지
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "runs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(runs[0]))
        writer.writeheader()
        writer.writerows(runs)
    meta = {
        "commit": _git("rev-parse", "HEAD"),
        # 커밋 안 한 수정이 있으면 이 결과는 커밋 ID만으로 재현할 수 없다.
        # 결과 폴더(results/)는 뺀다: 결과를 다시 만들면 그 파일이 바뀌는 건 당연하고 코드 재현성과 무관
        "uncommitted_changes": _git("status", "--porcelain", "--untracked-files=no", "--",
                                    ":(top)", ":(top,exclude)results") not in ("", "unknown"),
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "host": platform.node(),
        "conditions": conditions,
        "settings": settings,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
