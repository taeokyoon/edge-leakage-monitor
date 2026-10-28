"""디바이스 쪽 MQTT: telemetry/alert/status 발행 + cmd 구독 (docs/ARCHITECTURE.md §4).

TLS·ID/비밀번호·ACL은 4단계에서 추가한다.
"""
import json
import threading

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion


def topic(device_id: str, kind: str) -> str:
    return f"factory/leakage/{device_id}/{kind}"


class DeviceMqtt:
    def __init__(self, device_id: str, host: str, port: int):
        self.device_id = device_id
        # cmd(ack)는 paho 네트워크 스레드에서 도착하고 상태 머신은 메인 루프가 돌리므로,
        # 플래그로만 넘겨 메인 루프에서 처리한다 (두 스레드가 상태 머신을 동시에 건드리지 않게)
        self.ack_requested = threading.Event()

        self.client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=device_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        # 비정상 종료 시 브로커가 대신 "offline"을 발행 (LWT)
        self.client.will_set(topic(device_id, "status"), "offline", qos=1, retain=True)
        self.client.connect(host, port)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        # 재접속해도 구독·온라인 표시가 다시 되도록 on_connect에서 처리
        client.subscribe(topic(self.device_id, "cmd"), qos=1)
        client.publish(topic(self.device_id, "status"), "online", qos=1, retain=True)

    def _on_message(self, client, userdata, msg):
        try:
            command = json.loads(msg.payload).get("command")
        except (ValueError, AttributeError):
            return  # 형식이 잘못된 명령은 무시
        if command == "ack":
            self.ack_requested.set()

    def publish(self, kind: str, payload: dict) -> None:
        qos = 1 if kind == "alert" else 0  # 알림은 유실되면 안 되므로 QoS 1
        self.client.publish(topic(self.device_id, kind), json.dumps(payload, ensure_ascii=False), qos=qos)

    def close(self) -> None:
        # 정상 종료 시 LWT가 안 나가므로 offline을 직접 발행
        info = self.client.publish(topic(self.device_id, "status"), "offline", qos=1, retain=True)
        # 브로커가 끊겨 발행 자체가 실패했으면 기다리지 않는다 (wait_for_publish가 예외를 던져 정리가 건너뛰어짐)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            info.wait_for_publish(2)
        self.client.disconnect()
        self.client.loop_stop()
