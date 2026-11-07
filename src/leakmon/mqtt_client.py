"""디바이스 쪽 MQTT: telemetry/alert/status 발행 + cmd 구독 (docs/ARCHITECTURE.md §4).

보안 (4단계, SYS-13a~c): TLS로 암호화하고 브로커 인증서를 CA로 확인, 계정은 사용자명 = device_id.
발행 가능한 토픽은 브로커 ACL(broker/acl)이 정한다.
"""
import json
import threading

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .config import load_secrets


def topic(device_id: str, kind: str) -> str:
    return f"factory/leakage/{device_id}/{kind}"


def make_client(username: str, mqtt_settings: dict, password: str | None = None, **kwargs) -> mqtt.Client:
    """TLS + 사용자명/비밀번호가 설정된 클라이언트 (connect 전 상태). 비밀번호를 안 주면 secrets.yaml에서 읽는다."""
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=username, **kwargs)
    client.username_pw_set(username, password if password is not None else load_secrets()[username])
    client.tls_set(ca_certs=mqtt_settings["ca_file"])  # 브로커 인증서가 이 CA로 서명되지 않았으면 접속 실패
    return client


class DeviceMqtt:
    def __init__(self, device_id: str, mqtt_settings: dict):
        self.device_id = device_id
        # cmd(ack)는 paho 네트워크 스레드에서 도착하고 상태 머신은 메인 루프가 돌리므로,
        # 플래그로만 넘겨 메인 루프에서 처리한다 (두 스레드가 상태 머신을 동시에 건드리지 않게)
        self.ack_requested = threading.Event()

        self.client = make_client(device_id, mqtt_settings)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        # 비정상 종료 시 브로커가 대신 "offline"을 발행 (LWT)
        self.client.will_set(topic(device_id, "status"), "offline", qos=1, retain=True)
        self.client.connect(mqtt_settings["host"], mqtt_settings["port"])
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:  # 비밀번호 오류 등. paho가 계속 재접속을 시도한다
            print(f"[{self.device_id}] 브로커 접속 거부: {reason_code}")
            return
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
