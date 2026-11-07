"""제안 방법: 선형 회귀로 "정상이면 이 정도" 누설전류를 예측하고, 실제값과의 차이(잔차)로 이상 점수를 낸다.

흐름 (docs/ARCHITECTURE.md 6장)
1. 학습: 학습 기간의 정상 데이터로 누설전류 = a·습도 + b·가동여부 + c 를 맞춘다 → 계수와 잔차의 σ를 JSON에 저장
2. 판정: 점수 = (실제 - 예측) / (σ × sigma_scale)
   습도·가동 시간대로 설명되는 변화는 예측에 포함되므로 점수가 오르지 않는다 (STK-02)
"""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.linear_model import LinearRegression


def features(humidity_pct: float, ts: datetime) -> list[float]:
    """AI 입력: 습도, 가동 여부(08~18시 = 1). 순서는 학습 계수 순서와 같아야 한다."""
    running = 1.0 if 8 <= ts.hour < 18 else 0.0
    return [humidity_pct, running]


def train(rows: list[dict]) -> dict:
    """정상 데이터 행(ts, leakage_mA, humidity_pct)으로 학습. 데이터 끊김(None) 행은 뺀다."""
    rows = [r for r in rows if r["leakage_mA"] is not None]
    X = np.array([features(r["humidity_pct"], r["ts"]) for r in rows])
    y = np.array([r["leakage_mA"] for r in rows])
    reg = LinearRegression().fit(X, y)
    residuals = y - reg.predict(X)
    # sklearn 객체 대신 숫자만 저장 → Pi에서 버전 차이 없이 불러온다
    return {"coef": reg.coef_.tolist(), "intercept": float(reg.intercept_), "sigma": float(residuals.std())}


def predict(model: dict, humidity_pct: float, ts: datetime) -> float:
    """정상이라면 나와야 할 누설전류(mA)."""
    return model["intercept"] + float(np.dot(model["coef"], features(humidity_pct, ts)))


def residual_score(model: dict, leakage_mA: float, humidity_pct: float, ts: datetime, sigma_scale: float) -> float:
    """점수 1.0 = 잔차가 σ의 sigma_scale배. 예측보다 낮으면 음수 (정상 쪽)."""
    return (leakage_mA - predict(model, humidity_pct, ts)) / (model["sigma"] * sigma_scale)


def save(model: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=2), encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
