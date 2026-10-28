"""웹 서버: MQTT 구독 → WebSocket으로 브라우저에 즉시 전달, SQLite 이력 조회, ack 명령 발행.

실행 (저장소 루트에서): uvicorn web.app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from paho.mqtt.enums import CallbackAPIVersion

from leakmon import storage
from leakmon.config import load_devices, load_settings
from leakmon.mqtt_client import topic
from leakmon.scenario import SCENARIOS

STATIC_DIR = Path(__file__).parent / "static"
settings = load_settings()
devices = load_devices()

browsers: set[WebSocket] = set()   # 현재 접속 중인 대시보드들
last_status: dict[str, str] = {}   # device_id → "online" / "offline" (새로 접속한 브라우저용)


async def broadcast(message: str) -> None:
    for ws in list(browsers):
        try:
            await ws.send_text(message)
        except Exception:
            browsers.discard(ws)  # 끊긴 브라우저는 목록에서 제거


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe("factory/leakage/#", qos=1)

    def on_message(client, userdata, msg):
        # paho 스레드에서 실행됨 → 예외가 나면 MQTT 루프가 멈추므로 잘못된 메시지는 버린다
        parts = msg.topic.split("/")  # factory/leakage/{device_id}/{kind}
        if len(parts) != 4 or parts[3] == "cmd":
            return
        device_id, kind = parts[2], parts[3]
        try:
            text = msg.payload.decode()
            data = text if kind == "status" else json.loads(text)
        except ValueError:
            return
        if kind == "status":
            last_status[device_id] = data
        message = json.dumps({"device_id": device_id, "kind": kind, "data": data}, ensure_ascii=False)
        # 브라우저 전송은 asyncio 루프의 일이므로 스레드 안전하게 넘긴다
        asyncio.run_coroutine_threadsafe(broadcast(message), loop)

    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="web")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(settings["mqtt"]["host"], settings["mqtt"]["port"])
    client.loop_start()
    app.state.mqtt = client
    yield
    client.disconnect()
    client.loop_stop()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def check_device(device_id: str) -> None:
    # 등록된 장치만 허용 (URL 값이 그대로 MQTT 토픽에 들어가므로 "+"/"#" 같은 값 차단)
    if device_id not in devices:
        raise HTTPException(404, f"등록되지 않은 장치: {device_id}")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/devices")
def get_devices():
    return {
        "time_scale": settings["time_scale"],
        "scenarios": SCENARIOS,
        "devices": [{**d, "status": last_status.get(d["device_id"], "unknown")} for d in devices.values()],
    }


@app.get("/api/devices/{device_id}/history")
def get_history(device_id: str, limit: int = Query(200, ge=1, le=2000)):
    check_device(device_id)
    conn = storage.connect(settings["db_path"])
    try:
        run_id = storage.latest_run_id(conn, device_id)  # 최신 실행만 (실행끼리 가상 시간이 겹침)
        return {
            "telemetry": storage.recent_rows(conn, "telemetry", device_id, run_id, limit),
            "alerts": storage.recent_rows(conn, "alerts", device_id, run_id, 50),
        }
    finally:
        conn.close()


@app.post("/api/devices/{device_id}/ack")
def ack(device_id: str):
    check_device(device_id)
    # 디바이스는 매번 새 세션으로 접속하므로, 오프라인일 때 보낸 cmd는 브로커가 보관하지 않고 사라진다
    if last_status.get(device_id) != "online":
        raise HTTPException(409, "디바이스가 오프라인이라 ack를 전달할 수 없습니다")
    command = {"command": "ack", "requested_by": "dashboard",
               "ts_real": datetime.now().isoformat(timespec="seconds")}
    info = app.state.mqtt.publish(topic(device_id, "cmd"), json.dumps(command), qos=1)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise HTTPException(503, "웹 서버와 MQTT 브로커의 연결이 끊겨 있습니다")
    return {"sent": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    browsers.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # 브라우저 → 서버 메시지는 없음, 연결 유지용
    except WebSocketDisconnect:
        browsers.discard(websocket)
