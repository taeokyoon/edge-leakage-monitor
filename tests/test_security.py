"""4단계 보안 시연 = 통합 테스트 (STK-07a~c, SYS-13a~c). 실제 브로커가 필요하다.

준비: bash broker/setup.sh → mosquitto -c broker/mosquitto.conf (저장소 루트에서, 다른 터미널)
시연: pytest -v -s tests/test_security.py   (브로커가 안 떠 있으면 건너뜀)
"""
import json
import socket
import threading

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from leakmon.config import load_settings
from leakmon.mqtt_client import make_client, topic

SETTINGS = load_settings()["mqtt"]
WAIT_S = 3


def broker_is_up() -> bool:
    try:
        socket.create_connection((SETTINGS["host"], SETTINGS["port"]), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not broker_is_up(), reason="보안 브로커(8883)가 실행 중이 아님")


def connect_result(client: mqtt.Client) -> str:
    """접속을 시도하고 브로커의 답(CONNACK)을 문자열로 돌려준다. TLS 단계에서 끊기면 예외 이름."""
    answer, done = [], threading.Event()
    client.on_connect = lambda c, u, f, rc, p: (answer.append(str(rc)), done.set())
    try:
        client.connect(SETTINGS["host"], SETTINGS["port"])
    except OSError as e:  # TLS 핸드셰이크 실패 등
        return type(e).__name__
    client.loop_start()
    done.wait(WAIT_S)
    client.loop_stop()
    client.disconnect()
    return answer[0] if answer else "응답 없음"


def test_1_plaintext_is_refused():
    # STK-07a: TLS 없이(평문) 접속하면 브로커와 대화 자체가 안 된다
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="plain")
    result = connect_result(client)
    print(f"\n  평문 접속 → {result}")
    assert result != "Success"


def test_2_wrong_password_is_refused():
    # STK-07b: TLS는 맞지만 비밀번호가 틀리면 거부
    result = connect_result(make_client("press-01", SETTINGS, password="wrong"))  # noqa: S106 (일부러 틀린 값)
    print(f"\n  틀린 비밀번호 → {result}")
    assert result == "Not authorized"


def test_3_registered_device_connects():
    # 대조군: 등록된 장치 계정 + TLS면 접속 성공
    result = connect_result(make_client("press-01", SETTINGS))
    print(f"\n  press-01 정상 접속 → {result}")
    assert result == "Success"


def published_to(kind_topic: str) -> list[str]:
    """web 계정으로 구독한 채로 press-01 계정이 kind_topic에 발행 → web이 받은 메시지와 브로커 답(PUBACK)"""
    received, subscribed = [], threading.Event()
    web = make_client("web", SETTINGS)
    web.on_connect = lambda c, u, f, rc, p: c.subscribe(kind_topic, qos=1)
    web.on_subscribe = lambda c, u, mid, rcs, p: subscribed.set()
    web.on_message = lambda c, u, msg: received.append(msg.payload.decode())
    web.connect(SETTINGS["host"], SETTINGS["port"])
    web.loop_start()
    subscribed.wait(WAIT_S)

    # MQTT v5로 발행해야 브로커가 거부 이유(PUBACK reason code)를 돌려준다
    puback, acked = [], threading.Event()
    device = make_client("press-01", SETTINGS, protocol=mqtt.MQTTv5)
    device.on_publish = lambda c, u, mid, rc, p: (puback.append(str(rc)), acked.set())
    device.connect(SETTINGS["host"], SETTINGS["port"])
    device.loop_start()
    device.publish(kind_topic, json.dumps({"state": "inspect", "message": "위조"}), qos=1)
    acked.wait(WAIT_S)
    threading.Event().wait(1)  # 전달될 시간
    for c in (device, web):
        c.loop_stop()
        c.disconnect()
    print(f"\n  press-01 → {kind_topic}: 브로커 답 {puback}, web 수신 {len(received)}건")
    return received


def test_4_device_can_publish_own_topic():
    # 대조군: 자기 토픽 발행은 대시보드(web)에 도착
    assert len(published_to(topic("press-01", "alert"))) == 1


def test_5_device_cannot_publish_other_device_topic():
    # STK-07c: press-01이 press-02인 척 alert를 보내도 브로커가 버려 대시보드에 도착하지 않음
    assert published_to(topic("press-02", "alert")) == []


def test_6_web_cannot_forge_telemetry():
    # 웹 서버 계정도 최소 권한: cmd만 발행 가능, telemetry 발행은 거부 (ACL)
    puback, acked = [], threading.Event()
    web = make_client("web", SETTINGS, protocol=mqtt.MQTTv5)
    web.on_publish = lambda c, u, mid, rc, p: (puback.append(str(rc)), acked.set())
    web.connect(SETTINGS["host"], SETTINGS["port"])
    web.loop_start()
    web.publish(topic("press-01", "telemetry"), "{}", qos=1)
    acked.wait(WAIT_S)
    web.loop_stop()
    web.disconnect()
    print(f"\n  web → press-01/telemetry: 브로커 답 {puback}")
    assert puback == ["Not authorized"]


def test_7_web_can_send_cmd_to_device():
    # 대조군: ack 경로(SYS-12a) — web은 cmd 발행 가능, 장치는 자기 cmd 구독 가능
    received, subscribed = [], threading.Event()
    device = make_client("press-01", SETTINGS)
    device.on_connect = lambda c, u, f, rc, p: c.subscribe(topic("press-01", "cmd"), qos=1)
    device.on_subscribe = lambda c, u, mid, rcs, p: (received.append(str(rcs[0])), subscribed.set())
    device.on_message = lambda c, u, msg: received.append(msg.payload.decode())
    device.connect(SETTINGS["host"], SETTINGS["port"])
    device.loop_start()
    subscribed.wait(WAIT_S)
    web = make_client("web", SETTINGS)
    web.connect(SETTINGS["host"], SETTINGS["port"])
    web.loop_start()
    web.publish(topic("press-01", "cmd"), json.dumps({"command": "ack"}), qos=1).wait_for_publish(WAIT_S)
    threading.Event().wait(1)
    for c in (web, device):
        c.loop_stop()
        c.disconnect()
    print(f"\n  web → press-01/cmd: 장치 구독 결과·수신 {received}")
    assert '{"command": "ack"}' in received
