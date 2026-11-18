"""비교 방법: Isolation Forest (비지도). 학습 기간 데이터와 "얼마나 다른가"로만 판단한다 (docs/ARCHITECTURE.md 6장).

PC 평가 전용이라 모델 파일로 저장하지 않는다.
"""
from collections.abc import Callable

import numpy as np
from sklearn.ensemble import IsolationForest

from .model import features


def _x(row: dict) -> list[float]:
    return [row["leakage_mA"], *features(row["humidity_pct"], row["ts"])]


def train_scorer(rows: list[dict], t1: float, standard: bool = False) -> Callable[[list[dict]], list[float]]:
    """점수 함수(여러 행을 한 번에)를 돌려준다. 판정 기준 2가지 (논문에서 둘 다 보고: 비교 방법 보정 편향 방지)

    - standard=False (iforest): 학습 데이터에서 가장 이상했던 점이 점수 T1이 되도록 맞춘다
      decision_function은 음수일수록 이상 → 부호를 뒤집어 "클수록 이상"으로 바꾼다
    - standard=True (iforest_std): 원 논문(Liu et al., 2008)의 이상 점수 s = -score_samples 그대로 (0~1)
      T1 = 0.5이므로 "s > 0.5 = 이상"이라는 표준 기준과 같다 (sklearn contamination="auto"의 경계)
    한 행씩 부르면 느려서(호출당 수 ms) 행 목록을 한 번에 계산한다.
    """
    forest = IsolationForest(random_state=0).fit(np.array([_x(r) for r in rows]))
    worst_in_training = float((-forest.decision_function(np.array([_x(r) for r in rows]))).max())

    def score_all(batch: list[dict]) -> list[float]:
        if not batch:
            return []
        x = np.array([_x(r) for r in batch])
        if standard:
            return (-forest.score_samples(x)).tolist()
        return (-forest.decision_function(x) / worst_in_training * t1).tolist()
    return score_all
