"""비교 방법: Isolation Forest (비지도). 학습 기간 데이터와 "얼마나 다른가"로만 판단한다 (docs/ARCHITECTURE.md 6장).

PC 평가 전용이라 모델 파일로 저장하지 않는다.
"""
from collections.abc import Callable

import numpy as np
from sklearn.ensemble import IsolationForest

from .model import features


def _x(row: dict) -> list[float]:
    return [row["leakage_mA"], *features(row["humidity_pct"], row["ts"])]


def train_scorer(rows: list[dict], t1: float) -> Callable[[list[dict]], list[float]]:
    """학습 데이터에서 가장 이상했던 점이 점수 T1이 되도록 맞춘 점수 함수(여러 행을 한 번에)를 돌려준다.

    decision_function은 음수일수록 이상 → 부호를 뒤집어 "클수록 이상"으로 바꾼다.
    한 행씩 부르면 느려서(호출당 수 ms) 행 목록을 한 번에 계산한다.
    """
    forest = IsolationForest(random_state=0).fit(np.array([_x(r) for r in rows]))
    worst_in_training = float((-forest.decision_function(np.array([_x(r) for r in rows]))).max())

    def score_all(batch: list[dict]) -> list[float]:
        if not batch:
            return []
        return (-forest.decision_function(np.array([_x(r) for r in batch])) / worst_in_training * t1).tolist()
    return score_all
