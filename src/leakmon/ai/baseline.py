"""고정 기준값 방식 이상 점수 (AI 모델과 비교할 baseline)."""


def fixed_threshold_score(leakage_mA: float, normal_mA: float, alarm_mA: float) -> float:
    """정상값이면 0, 경보 기준값이면 1이 되도록 정규화한 점수.

    shortcut: 습도·운전 조건을 반영하지 않음, 3단계에서 AI 잔차 점수와 나란히 평가
    """
    return (leakage_mA - normal_mA) / (alarm_mA - normal_mA)
