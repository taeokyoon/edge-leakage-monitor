// 대시보드 흐름:
// 1) /api/devices로 장치 목록을 받아 장치별 패널을 만든다
// 2) /api/devices/{id}/history로 과거 데이터를 채운다
// 3) /ws WebSocket으로 telemetry·alert·status를 실시간 반영한다

const STATE_LABELS = { normal: "정상", caution: "주의", inspect: "점검권고", fault: "센서장애" };
let scenarioLabels = {}; // 시나리오 이름표 (서버 /api/devices에서 받음, leakmon/scenario.py가 원본)
const MAX_POINTS = 200;  // 차트에 유지할 최대 점 개수
const panels = {};       // device_id → { el, chart, state, status, runId }

async function sendAck(deviceId) {
  const res = await fetch(`/api/devices/${deviceId}/ack`, { method: "POST" });
  if (!res.ok) alert((await res.json()).detail);  // 전달 실패를 조용히 넘기지 않고 알려줌
}

function createPanel(device) {
  const el = document.getElementById("device-template").content.firstElementChild.cloneNode(true);
  el.querySelector(".equipment").textContent = device.equipment;
  el.querySelector(".location").textContent = `${device.building} / ${device.line} / ${device.device_id}`;
  el.querySelector(".ack").onclick = () => sendAck(device.device_id);
  // 화면에 먼저 붙여야 canvas가 실제 문서 소속이 된다
  // (template에서 복제한 요소는 화면 밖 문서 소속이라 Chart.js가 스타일을 못 읽고 실패함)
  document.getElementById("devices").appendChild(el);

  const chart = new Chart(el.querySelector("canvas"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "누설전류 (mA)", data: [], borderColor: "#1565c0", pointRadius: 0 },
        { label: "예상값 (mA)", data: [], borderColor: "#9e9e9e", borderDash: [4, 4], pointRadius: 0 },
      ],
    },
    options: { animation: false, scales: { x: { ticks: { maxTicksLimit: 6 } } } },
  });

  panels[device.device_id] = { el, chart };
  setConnection(device.device_id, device.status);
}

function setConnection(deviceId, status) {
  const panel = panels[deviceId];
  const conn = panel.el.querySelector(".conn");
  conn.textContent = status === "online" ? "● 온라인" : status === "offline" ? "● 오프라인" : "";
  conn.className = `conn ${status}`;
  panel.status = status;
  updateAckButton(panel);
}

function setState(deviceId, state) {
  const panel = panels[deviceId];
  const badge = panel.el.querySelector(".badge");
  badge.textContent = STATE_LABELS[state] || state;
  badge.className = `badge ${state}`;
  panel.state = state;
  updateAckButton(panel);
}

function updateAckButton(panel) {
  // 점검권고이고 디바이스가 온라인일 때만 표시 (오프라인이면 ack가 전달되지 않음)
  panel.el.querySelector(".ack").hidden = !(panel.state === "inspect" && panel.status === "online");
}

function addTelemetry(t) {
  const panel = panels[t.device_id];
  const { el, chart } = panel;
  // 디바이스가 새로 실행되면 가상 시간이 처음부터 다시 시작하므로 이전 실행의 차트·알림을 지운다
  if (panel.runId && panel.runId !== t.run_id) {
    chart.data.labels = [];
    chart.data.datasets.forEach((ds) => (ds.data = []));
    el.querySelector(".alerts").replaceChildren();
  }
  panel.runId = t.run_id;
  el.querySelector(".scenario").textContent = scenarioLabels[t.scenario] || t.scenario;
  el.querySelector(".leakage").textContent = `${t.leakage_mA.toFixed(2)} mA`;
  el.querySelector(".expected").textContent = `${t.expected_mA.toFixed(2)} mA`;
  el.querySelector(".humidity").textContent = `${t.humidity_pct.toFixed(1)} %`;
  el.querySelector(".score").textContent = t.anomaly_score.toFixed(2);
  el.querySelector(".ts").textContent = t.ts_virtual.replace("T", " ");
  setState(t.device_id, t.state);

  chart.data.labels.push(t.ts_virtual.slice(5, 16).replace("T", " "));  // "MM-DD HH:MM"
  chart.data.datasets[0].data.push(t.leakage_mA);
  chart.data.datasets[1].data.push(t.expected_mA);
  if (chart.data.labels.length > MAX_POINTS) {
    chart.data.labels.shift();
    chart.data.datasets.forEach((ds) => ds.data.shift());
  }
}

function addAlert(deviceId, alert) {
  const li = document.createElement("li");
  const label = STATE_LABELS[alert.state] || alert.state;
  li.textContent = `[가상 ${alert.ts_virtual.replace("T", " ")}] ${label} — ${alert.message}`;
  panels[deviceId].el.querySelector(".alerts").prepend(li);  // 최신이 위로
}

async function init() {
  const { time_scale, scenarios, devices } = await (await fetch("/api/devices")).json();
  scenarioLabels = scenarios;
  document.getElementById("scale").textContent =
    `배속 ×${time_scale} (시연 ${(1440 / time_scale).toFixed(0)}분 = 가상 운전 1일) · 표시 시각은 모두 가상 시간`;

  for (const device of devices) {
    createPanel(device);
    const history = await (await fetch(`/api/devices/${device.device_id}/history`)).json();
    history.telemetry.forEach(addTelemetry);
    history.alerts.forEach((a) => addAlert(device.device_id, a));
    // 센서장애 중엔 telemetry가 없으므로, 마지막 알림이 마지막 telemetry보다 나중이면 알림의 상태가 현재 상태
    const lastAlert = history.alerts.at(-1);
    const lastTelemetry = history.telemetry.at(-1);
    if (lastAlert && (!lastTelemetry || lastAlert.ts_virtual > lastTelemetry.ts_virtual)) {
      setState(device.device_id, lastAlert.state);
    }
    panels[device.device_id].chart.update("none");
  }

  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = (event) => {
    const { device_id, kind, data } = JSON.parse(event.data);
    if (!panels[device_id]) return;  // 등록되지 않은 장치는 무시
    if (kind === "telemetry") {
      addTelemetry(data);
      panels[device_id].chart.update("none");
    } else if (kind === "alert") {
      addAlert(device_id, data);
      setState(device_id, data.state);
    } else if (kind === "status") {
      setConnection(device_id, data);
    }
  };
  ws.onclose = () => setTimeout(reloadWhenServerUp, 3000);  // 서버 재시작 시 자동 재접속
}

function reloadWhenServerUp() {
  // 서버가 응답할 때만 새로고침 (바로 reload하면 서버가 아직 꺼져 있을 때 브라우저 오류 화면에서 멈춤)
  fetch("/api/devices")
    .then(() => location.reload())
    .catch(() => setTimeout(reloadWhenServerUp, 3000));
}

init();
