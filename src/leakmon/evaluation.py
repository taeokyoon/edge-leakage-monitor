"""시나리오 재생: 같은 시나리오를 방식별로 상태 머신에 넣어 결과를 비교한다 (MQTT·DB 없이 빠르게).

방식: baseline = 고정 임계값 / iforest = Isolation Forest / linear = 선형 회귀 + 잔차 (제안 방법)
AI 방식은 처음 learn_days일이 학습 기간이다 (점수 0 = 판정 없이 정상 유지, 미수신은 그대로 넣어 센서장애는 잡는다).
테스트(tests/test_scenarios.py)와 평가 도구(tools/evaluate.py)가 같이 쓴다.
"""
from collections.abc import Callable
from datetime import timedelta

from .ai.baseline import fixed_threshold_score
from .ai.iforest import train_scorer
from .ai.model import residual_score, train
from .scenario import ALARM_EXPECTED, EVENT_START, generate
from .state import StateMachine

METHODS = ("baseline", "iforest", "linear")


def replay(name: str, days: float, settings: dict, method: str = "linear", seed: int = 0) -> list[dict]:
    """generate() 행마다 score·state를 붙여 돌려준다."""
    if timedelta(days=settings["ai"]["learn_days"]) > EVENT_START:
        # 학습 기간에 이상 데이터가 섞이면 평가가 무효가 된다 (시나리오는 1일까지만 정상)
        raise ValueError(f"learn_days는 시나리오의 정상 구간({EVENT_START.days}일) 이하여야 함")
    rows = generate(name, days, seed)
    learn_end = rows[0]["ts"] + timedelta(days=settings["ai"]["learn_days"])
    learning = [r for r in rows if r["ts"] < learn_end and r["leakage_mA"] is not None]

    score_all: Callable[[list[dict]], list[float]]
    if method == "baseline":
        b = settings["baseline"]
        score_all = lambda batch: [fixed_threshold_score(r["leakage_mA"], b["normal_mA"], b["alarm_mA"])  # noqa: E731
                                   for r in batch]
    elif method == "iforest":
        score_all = train_scorer(learning, settings["state"]["t1"])
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


def summarize(name: str, rows: list[dict]) -> dict:
    """재생 1회의 평가값 (docs/ARCHITECTURE.md 8장)

    - alarm: 이상 시작 뒤 경보(주의 이상)가 한 번이라도 떴나
    - delay: 이상 시작부터 첫 경보까지 (경보가 정답인 시나리오만)
    - tp/fp/fn: 측정 하나하나를 "경보 상태인가 vs 라벨"로 센 값 (Precision·Recall용)
    """
    event_at = rows[0]["ts"] + EVENT_START
    alarm_rows = [r for r in rows if r["state"] in ALARM_STATES and r["ts"] > event_at]
    return {
        "alarm": bool(alarm_rows),
        "delay": alarm_rows[0]["ts"] - event_at if alarm_rows and name in ALARM_EXPECTED else None,
        "tp": sum(r["state"] in ALARM_STATES and r["anomaly"] for r in rows),
        "fp": sum(r["state"] in ALARM_STATES and not r["anomaly"] for r in rows),
        "fn": sum(r["state"] not in ALARM_STATES and r["anomaly"] for r in rows),
    }
