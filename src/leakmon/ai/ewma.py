"""비교 방법: EWMA 관리도 (통계적 공정 관리에서 쓰는 추세 감지). 운전 조건(습도·가동)은 모른다.

학습: 학습 기간 누설전류의 평균 μ, 표준편차 σ
판정: z = λ·x + (1-λ)·z_prev (z 시작값 = μ), 관리 한계 = μ + L·σ·√(λ/(2-λ)) (Montgomery 교재의 정상 상태 한계)
판정 기준 2가지 (Isolation Forest와 같은 이유: 비교 방법을 불리하게 보정했다는 지적 방지)
- standard=True (ewma_std): 교재 대표값 λ = 0.2, L = 3의 관리 한계에서 점수 T1(주의)
- standard=False (ewma): 학습 기간에서 가장 높았던 z가 점수 T1이 되도록 맞춘다 (학습 데이터에서는 경보 없음)
  하루 가동 패턴 때문에 표준 한계는 학습 기간 자체에서도 넘는다 (관리도의 독립성 가정이 깨짐)
"""
import statistics
from collections.abc import Callable

LAMBDA = 0.2
L = 3.0


def _ewma(values: list[float], start: float) -> list[float]:
    z, out = start, []
    for x in values:
        z = LAMBDA * x + (1 - LAMBDA) * z
        out.append(z)
    return out


def train_scorer(rows: list[dict], t1: float, standard: bool = True) -> Callable[[list[dict]], list[float]]:
    """점수 함수(시간 순서 행 목록을 한 번에)를 돌려준다. EWMA는 앞 값을 이어받으므로 순서대로 넣어야 한다."""
    values = [r["leakage_mA"] for r in rows]
    mean = statistics.fmean(values)
    if standard:
        limit = L * statistics.pstdev(values) * (LAMBDA / (2 - LAMBDA)) ** 0.5  # μ에서 관리 한계까지 거리
    else:
        limit = max(z - mean for z in _ewma(values, mean))  # 학습 기간 최대 이탈

    def score_all(batch: list[dict]) -> list[float]:
        return [(z - mean) / limit * t1 for z in _ewma([r["leakage_mA"] for r in batch], mean)]
    return score_all
