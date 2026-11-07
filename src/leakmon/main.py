"""디바이스 에이전트 진입점.

흐름 (샘플 1개마다): 센서 읽기 → 이상 점수 → 상태 머신 → SQLite 저장 → MQTT 발행
상태가 바뀌거나 순간 튐이 기록되면 alert도 발행·저장한다. 웹 대시보드의 ack는 cmd 토픽으로 받는다.
AI(linear): 처음 learn_days일은 학습 기간(수집만, 판정 없음) → 학습해 계수·σ를 JSON으로 저장 → 이후 판정 (SYS-15)

실행: python -m leakmon.main [--scenario gradual_rise]
"""
import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import storage
from .ai import model as ai_model
from .ai.baseline import fixed_threshold_score
from .config import load_devices, load_settings
from .mqtt_client import DeviceMqtt
from .scenario import SCENARIOS, Scenario
from .sensors import make_sensors
from .state import CAUTION, FAULT, INSPECT, NORMAL, StateMachine

ALERT_MESSAGES = {
    NORMAL: "정상 상태로 회복.",
    CAUTION: "누설전류 상승 추세 감지 — 주의 관찰.",
    INSPECT: "정상 운전 패턴 대비 누설전류 상승 추세 감지. 절연 열화 의심 — 현장 점검 권고.",
    FAULT: "센서 데이터 미수신 — 센서 또는 배선 확인.",
}


def run(scenario_name: str) -> None:
    settings = load_settings()
    device_id = settings["device_id"]
    device = load_devices()[device_id]
    location = {k: device[k] for k in ("building", "line", "equipment")}
    time_scale = settings["time_scale"]
    normal_mA, alarm_mA = settings["baseline"]["normal_mA"], settings["baseline"]["alarm_mA"]
    ai = settings["ai"]
    use_ai = ai["method"] == "linear"

    # 가상 시간은 오늘 00:00부터 시작 → 시나리오의 시간대(가동·습도 주기)가 실행마다 같게 재현됨
    virtual_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    # 실행 구분용 ID: 실행마다 가상 시간이 처음부터 다시 시작하므로 웹은 최신 실행만 보여 준다
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    sensors = make_sensors(settings["hardware"], Scenario(scenario_name, virtual_start))
    machine = StateMachine.from_settings(settings["state"])
    db = storage.connect(settings["db_path"])
    mqtt = DeviceMqtt(device_id, settings["mqtt"]["host"], settings["mqtt"]["port"])

    # 가상 시간 = 가상 시작 시각 + 경과 실제 시간 × 배속
    real_start = time.monotonic()
    learn_end = virtual_start + timedelta(days=ai["learn_days"])
    # shortcut: 학습 데이터는 메모리에 보관하고 실행마다 새로 학습 (저장된 JSON은 재사용 안 함).
    #           재시작 후에도 이어 쓰려면 시작할 때 model_path를 불러오도록 바꾼다
    learning_rows: list[dict] = []
    model = None
    print(f"[{device_id}] 시작 — 실행 {run_id}, 시나리오={SCENARIOS[scenario_name]}, "
          f"배속 ×{time_scale}, 하드웨어={settings['hardware']}, 판정={ai['method']}")

    try:
        while True:
            now_virtual = virtual_start + timedelta(seconds=(time.monotonic() - real_start) * time_scale)
            ts_virtual = now_virtual.isoformat(timespec="seconds")

            previous_state = machine.state  # ack로 바뀌는 경우도 alert가 나가도록 먼저 기록

            # 웹에서 온 ack는 여기(메인 루프)에서만 상태 머신에 반영
            if mqtt.ack_requested.is_set():
                mqtt.ack_requested.clear()
                if machine.ack():
                    print(f"[{ts_virtual}] 대시보드 ack 수신 → 정상")

            reading = sensors.read(now_virtual)
            learning = use_ai and model is None and now_virtual < learn_end
            expected_mA: float | None = normal_mA
            if reading is None:
                score = None
            elif not use_ai:
                score = fixed_threshold_score(reading["leakage_mA"], normal_mA, alarm_mA)
            elif learning:
                # 학습 기간: 수집만 하고 판정 없이 정상 유지 (SYS-15b)
                learning_rows.append({"ts": now_virtual, **reading})
                score, expected_mA = 0.0, None
            else:
                if model is None:  # 학습 기간이 끝난 첫 샘플: 이 샘플은 학습에 넣지 않는다 (이상이 섞일 수 있음)
                    model = ai_model.train(learning_rows)
                    ai_model.save(model, Path(ai["model_path"]))
                    print(f"[{ts_virtual}] 학습 완료 ({len(learning_rows)}개) → {ai['model_path']}: {model}")
                expected_mA = ai_model.predict(model, reading["humidity_pct"], now_virtual)
                score = ai_model.residual_score(model, reading["leakage_mA"], reading["humidity_pct"],
                                                now_virtual, ai["sigma_scale"])
            spikes_before = machine.spikes[-1:]
            state = machine.update(now_virtual, score)
            new_spike = bool(machine.spikes) and machine.spikes[-1:] != spikes_before

            if reading is not None:
                telemetry = {
                    "device_id": device_id, "run_id": run_id, "scenario": scenario_name,
                    "ts_virtual": ts_virtual, "time_scale": time_scale,
                    "leakage_mA": reading["leakage_mA"], "humidity_pct": reading["humidity_pct"],
                    "expected_mA": None if expected_mA is None else round(expected_mA, 3),
                    "anomaly_score": round(score, 3), "state": state, "source": reading["source"],
                    "learning": learning,
                }
                storage.save_telemetry(db, telemetry)
                mqtt.publish("telemetry", telemetry)
                # 로컬 표시 (OLED·LED는 5단계, 지금은 콘솔)
                print(f"[{ts_virtual} ×{time_scale}] {reading['leakage_mA']:.2f}mA "
                      f"습도 {reading['humidity_pct']:.1f}% 점수 {score:.2f} → {state}")

            if state != previous_state or new_spike:
                # 순간 튐은 상태 변화가 아니라 기록용 알림 (SYS-07). state는 현재 상태 그대로 둔다
                message = ALERT_MESSAGES[state] if state != previous_state else \
                    f"순간 튐 기록 (24시간 안에 {len(machine.spikes)}회, {machine.spike_count}회면 주의)"
                alert = {"device_id": device_id, "run_id": run_id, "location": location, "state": state,
                         "message": message, "ts_virtual": ts_virtual, "time_scale": time_scale}
                storage.save_alert(db, alert)
                mqtt.publish("alert", alert)
                print(f"[{ts_virtual}] 알림: {previous_state} → {state} — {message}")

            time.sleep(settings["sample_period_s"])
    except KeyboardInterrupt:
        pass
    finally:
        mqtt.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="leakmon 디바이스 에이전트")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal",
                        help="시나리오 (이상은 시작 후 가상 1일부터): " + ", ".join(f"{k}={v}" for k, v in SCENARIOS.items()))
    run(parser.parse_args().scenario)
