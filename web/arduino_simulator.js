const LINE_COUNT = 12;
const DOT_COUNT = 24;
const LOG_EMPTY = "아직 수신한 payload가 없습니다.";

const simStatus = document.querySelector("#simStatus");
const latestPayload = document.querySelector("#latestPayload");
const latencyText = document.querySelector("#latencyText");
const payloadInput = document.querySelector("#payloadInput");
const applyPayloadButton = document.querySelector("#applyPayloadButton");
const demoPayloadButton = document.querySelector("#demoPayloadButton");
const pollLatestToggle = document.querySelector("#pollLatestToggle");
const previewSummary = document.querySelector("#previewSummary");
const valueSummary = document.querySelector("#valueSummary");
const simLedPreview = document.querySelector("#simLedPreview");
const simLog = document.querySelector("#simLog");

let demoTimer = null;
let pollTimer = null;
let lastReportPath = "";

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function fmt(value) {
  return value.toFixed(3);
}

function parsePayload(text) {
  const trimmed = String(text || "").trim();
  const parts = trimmed.split(",");
  if (parts.length !== 3 || parts[0] !== "v") {
    throw new Error("payload 형식은 v,<valence>,<arousal> 이어야 합니다.");
  }

  const valence = Number(parts[1]);
  const arousal = Number(parts[2]);
  if (!Number.isFinite(valence) || !Number.isFinite(arousal)) {
    throw new Error("valence와 arousal은 숫자여야 합니다.");
  }

  return {
    payload: `v,${fmt(clamp(valence, -1, 1))},${fmt(clamp(arousal, -1, 1))}`,
    valence: clamp(valence, -1, 1),
    arousal: clamp(arousal, -1, 1),
  };
}

function colorForValence(valence, brightness) {
  const hue = 212 - ((valence + 1) / 2) * 170;
  const saturation = 68 + Math.abs(valence) * 20;
  const lightness = 17 + brightness * 45;
  return `hsl(${hue.toFixed(1)} ${saturation.toFixed(1)}% ${lightness.toFixed(1)}%)`;
}

function appendLog(message) {
  const stamp = new Date().toLocaleTimeString("ko-KR", { hour12: false });
  const previous = simLog.textContent === LOG_EMPTY ? "" : `${simLog.textContent}\n`;
  simLog.textContent = `${previous}[${stamp}] ${message}`;
  simLog.scrollTop = simLog.scrollHeight;
}

function buildPreview() {
  const fragment = document.createDocumentFragment();
  for (let line = 0; line < LINE_COUNT; line += 1) {
    const row = document.createElement("div");
    row.className = "led-row";
    for (let dot = 0; dot < DOT_COUNT; dot += 1) {
      const led = document.createElement("span");
      led.className = "led-dot";
      led.dataset.line = String(line);
      led.dataset.dot = String(dot);
      row.appendChild(led);
    }
    fragment.appendChild(row);
  }
  simLedPreview.appendChild(fragment);
}

function renderLed(valence, arousal) {
  const energy = (arousal + 1) / 2;
  const activeDots = Math.max(1, Math.round(1 + energy * (DOT_COUNT - 1)));
  const brightness = 0.2 + energy * 0.8;

  simLedPreview.querySelectorAll(".led-dot").forEach((dot) => {
    const index = Number(dot.dataset.dot);
    const line = Number(dot.dataset.line);
    const linePhase = 1 - Math.abs(line - (LINE_COUNT - 1) / 2) / ((LINE_COUNT - 1) / 2);
    const isActive = index < activeDots;
    const localBrightness = brightness * (0.68 + linePhase * 0.32);
    const color = colorForValence(valence, localBrightness);

    dot.style.backgroundColor = isActive ? color : "#202632";
    dot.style.boxShadow = isActive ? `0 0 ${Math.round(5 + localBrightness * 14)}px ${color}` : "none";
    dot.style.opacity = isActive ? "1" : "0.32";
  });

  previewSummary.textContent = `${activeDots}/${DOT_COUNT} dots · brightness ${Math.round(brightness * 100)}%`;
  valueSummary.textContent = `valence ${fmt(valence)} / arousal ${fmt(arousal)}`;
}

function applyPayload(text, source = "manual", sentAt = null) {
  const parsed = parsePayload(text);
  const now = Date.now();
  const latency = sentAt ? Math.max(0, now - Number(sentAt)) : null;

  latestPayload.textContent = parsed.payload;
  payloadInput.value = parsed.payload;
  simStatus.textContent = `${source} payload 반영`;
  latencyText.textContent = latency === null ? "-" : `${latency} ms`;
  renderLed(parsed.valence, parsed.arousal);
  appendLog(`${source}: ${parsed.payload}${latency === null ? "" : ` (${latency} ms)`}`);
}

function stopDemo() {
  if (demoTimer) {
    clearInterval(demoTimer);
    demoTimer = null;
  }
  demoPayloadButton.textContent = "Demo";
}

function startDemo() {
  const startedAt = Date.now();
  demoPayloadButton.textContent = "Demo 정지";
  demoTimer = setInterval(() => {
    const seconds = (Date.now() - startedAt) / 1000;
    const valence = Math.sin(seconds * 0.8);
    const arousal = Math.sin(seconds * 1.3) * 0.7;
    applyPayload(`v,${fmt(valence)},${fmt(arousal)}`, "demo", Date.now());
  }, 120);
}

async function pollLatestReport() {
  try {
    const response = await fetch(`./e2e_latest.json?ts=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) {
      simStatus.textContent = "e2e_latest.json 대기 중";
      return;
    }

    const report = await response.json();
    const result = report.results?.[report.results.length - 1];
    if (!result || result.path === lastReportPath) {
      return;
    }

    lastReportPath = result.path;
    applyPayload(result.last_payload || "v,0.000,0.000", "e2e_latest", Date.now());
  } catch (error) {
    simStatus.textContent = "e2e_latest.json 읽기 실패";
  }
}

function setPolling(enabled) {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  if (enabled) {
    pollLatestReport();
    pollTimer = setInterval(pollLatestReport, 1500);
  }
}

applyPayloadButton.addEventListener("click", () => {
  stopDemo();
  try {
    applyPayload(payloadInput.value, "manual");
  } catch (error) {
    simStatus.textContent = "payload 오류";
    appendLog(error.message);
  }
});

demoPayloadButton.addEventListener("click", () => {
  if (demoTimer) {
    stopDemo();
    simStatus.textContent = "시뮬레이터 대기 중";
    return;
  }
  startDemo();
});

pollLatestToggle.addEventListener("change", () => {
  setPolling(pollLatestToggle.checked);
});

buildPreview();
applyPayload("v,0.000,0.000", "init");
