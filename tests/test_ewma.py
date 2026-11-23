"""개발자 단위 테스트: 비교 방법 EWMA 관리도 (ai/ewma.py)와 제거 실험(linear_nohum)."""
from leakmon.ai import ewma
from leakmon.config import load_settings
from leakmon.evaluation import replay, summarize

T1 = 0.5


def rows(values):
    return [{"leakage_mA": v} for v in values]


def test_standard_limit_is_textbook_formula():
    # μ=5, σ=1 → 관리 한계 거리 = 3·1·√(0.2/1.8) = 1.0. z가 한계에 닿으면 점수 = T1
    score = ewma.train_scorer(rows([4.0, 6.0]), T1, standard=True)
    limit = 3 * (0.2 / 1.8) ** 0.5
    x = 5 + limit / 0.2  # 첫 값 하나로 z = 0.2·x + 0.8·5 = 5 + limit
    assert abs(score(rows([x]))[0] - T1) < 1e-9


def test_learned_max_gives_no_alarm_on_training_data():
    # 학습 최대값 기준: 학습 데이터 자체의 점수는 T1을 넘지 않는다 (최대가 정확히 T1)
    train = rows([5.0, 5.2, 4.9, 5.5, 5.1, 4.8])
    scores = ewma.train_scorer(train, T1, standard=False)(train)
    assert max(scores) == T1


def test_ewma_follows_order_and_rises_with_drift():
    score = ewma.train_scorer(rows([5.0, 5.1, 4.9, 5.0]), T1, standard=False)
    s = score(rows([5.0, 6.0, 7.0, 8.0]))
    assert s == sorted(s) and s[-1] > T1


def test_ablation_without_humidity_false_alarms_on_humidity_rise():
    # 제거 실험: 습도 입력을 빼면 습도 상승을 열화로 오인 → 제안 방법(습도 포함)은 같은 신호에서 경보 없음
    settings = load_settings()
    assert summarize("humidity_rise", replay("humidity_rise", 14, settings, "linear_nohum", 0))["alarm"]
    assert not summarize("humidity_rise", replay("humidity_rise", 14, settings, "linear", 0))["alarm"]
