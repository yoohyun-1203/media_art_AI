const LINE_COUNT = 12;
const DOT_COUNT = 24;
const BAUD_RATE = 115200;

const connectButton = document.querySelector("#connectButton");
const sendButton = document.querySelector("#sendButton");
const demoButton = document.querySelector("#demoButton");
const serialStatus = document.querySelector("#serialStatus");
const commandText = document.querySelector("#commandText");
const valenceSlider = document.querySelector("#valenceSlider");
const arousalSlider = document.querySelector("#arousalSlider");
const valenceValue = document.querySelector("#valenceValue");
const arousalValue = document.querySelector("#arousalValue");
const ledPreview = document.querySelector("#ledPreview");
const previewSummary = document.querySelector("#previewSummary");
const sendLog = document.querySelector("#sendLog");

let port = null;
let writer = null;
let demoTimer = null;
let demoStart = 0;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function readValue(input) {
  return clamp(Number(input.value), -1, 1);
}

function fmt(value) {
  return value.toFixed(3);
}

function commandFor(valence, arousal) {
  return `v,${fmt(valence)},${fmt(arousal)}`;
}

function colorForValence(valence, brightness) {
  const hue = 210 - ((valence + 1) / 2) * 175;
  const saturation = 72 + Math.abs(valence) * 18;
  const lightness = 18 + brightness * 42;
  return `hsl(${hue.toFixed(1)} ${saturation.toFixed(1)}% ${lightness.toFixed(1)}%)`;
}

function buildPreview() {
  const fragment = document.createDocumentFragment();

  for (let line = 0; line < LINE_COUNT; line += 1) {
    const row = document.createElement("div");
    row.className = "led-row";
    row.setAttribute("aria-hidden", "true");

    for (let dot = 0; dot < DOT_COUNT; dot += 1) {
      const led = document.createElement("span");
      led.className = "led-dot";
      led.dataset.line = String(line);
      led.dataset.dot = String(dot);
      row.appendChild(led);
    }

    fragment.appendChild(row);
  }

  ledPreview.appendChild(fragment);
}

function renderPreview(valence, arousal) {
  const energy = (arousal + 1) / 2;
  const activeDots = Math.max(1, Math.round(1 + energy * (DOT_COUNT - 1)));
  const brightness = 0.24 + energy * 0.76;

  ledPreview.querySelectorAll(".led-dot").forEach((dot) => {
    const index = Number(dot.dataset.dot);
    const line = Number(dot.dataset.line);
    const linePhase = 1 - Math.abs(line - (LINE_COUNT - 1) / 2) / ((LINE_COUNT - 1) / 2);
    const isActive = index < activeDots;
    const localBrightness = brightness * (0.72 + linePhase * 0.28);

    dot.style.backgroundColor = isActive
      ? colorForValence(valence, localBrightness)
      : "#202632";
    dot.style.boxShadow = isActive
      ? `0 0 ${Math.round(5 + localBrightness * 14)}px ${colorForValence(valence, localBrightness)}`
      : "none";
    dot.style.opacity = isActive ? "1" : "0.34";
  });

  previewSummary.textContent = `active ${activeDots} / brightness ${Math.round(brightness * 100)}%`;
}

function setStatus(message) {
  serialStatus.textContent = message;
}

async function resetSerial(message) {
  const closingWriter = writer;
  const closingPort = port;

  writer = null;
  port = null;
  connectButton.textContent = "Serial 연결";
  setStatus(message);

  if (closingWriter) {
    try {
      await closingWriter.close();
    } catch {
      // The device may already be gone.
    }

    try {
      closingWriter.releaseLock();
    } catch {
      // Ignore stale writer locks during disconnect cleanup.
    }
  }

  if (closingPort) {
    try {
      await closingPort.close();
    } catch {
      // Closing can fail after a physical disconnect.
    }
  }
}

function appendLog(message) {
  const stamp = new Date().toLocaleTimeString("ko-KR", { hour12: false });
  const previous = sendLog.textContent === "아직 전송한 명령이 없습니다." ? "" : `${sendLog.textContent}\n`;
  sendLog.textContent = `${previous}[${stamp}] ${message}`;
  sendLog.scrollTop = sendLog.scrollHeight;
}

function currentValues() {
  return {
    valence: readValue(valenceSlider),
    arousal: readValue(arousalSlider),
  };
}

function updateFromInputs() {
  const { valence, arousal } = currentValues();
  valenceValue.textContent = fmt(valence);
  arousalValue.textContent = fmt(arousal);
  commandText.textContent = commandFor(valence, arousal);
  renderPreview(valence, arousal);
}

async function connectSerial() {
  if (!("serial" in navigator)) {
    setStatus("Web Serial 미지원: 시뮬레이션 모드");
    appendLog("이 브라우저는 Web Serial API를 지원하지 않습니다.");
    return;
  }

  if (writer) {
    setStatus("Serial 연결됨");
    return;
  }

  try {
    port = await navigator.serial.requestPort();
    await port.open({ baudRate: BAUD_RATE });
    writer = port.writable.getWriter();
  } catch (error) {
    await resetSerial("Serial 연결 실패");
    throw error;
  }

  connectButton.textContent = "연결됨";
  setStatus("Serial 연결됨");
  appendLog(`Serial 포트 연결 완료 (${BAUD_RATE})`);
}

async function sendCurrentCommand() {
  const { valence, arousal } = currentValues();
  const command = `${commandFor(valence, arousal)}\n`;

  if (!writer) {
    appendLog(`시뮬레이션: ${command.trim()}`);
    setStatus("시뮬레이션 모드");
    return;
  }

  const bytes = new TextEncoder().encode(command);
  try {
    await writer.write(bytes);
  } catch (error) {
    await resetSerial("전송 실패: 시뮬레이션 모드");
    throw error;
  }

  appendLog(`Serial 전송: ${command.trim()}`);
  setStatus("Serial 전송 완료");
}

function stopDemo() {
  if (demoTimer) {
    clearInterval(demoTimer);
    demoTimer = null;
  }
  demoButton.textContent = "Demo";
}

function startDemo() {
  demoStart = Date.now();
  demoButton.textContent = "Demo 정지";
  setStatus(writer ? "Serial 연결됨 · Demo" : "시뮬레이션 Demo");

  demoTimer = setInterval(() => {
    const seconds = (Date.now() - demoStart) / 1000;
    const valence = Math.sin(seconds * 0.85);
    const arousal = Math.sin(seconds * 1.35) * 0.65 + 0.2;
    valenceSlider.value = String(clamp(valence, -1, 1));
    arousalSlider.value = String(clamp(arousal, -1, 1));
    updateFromInputs();
  }, 50);
}

connectButton.addEventListener("click", () => {
  connectSerial().catch((error) => {
    setStatus("Serial 연결 실패");
    appendLog(`연결 실패: ${error.message}`);
  });
});

sendButton.addEventListener("click", () => {
  sendCurrentCommand().catch((error) => {
    setStatus("전송 실패");
    appendLog(`전송 실패: ${error.message}`);
  });
});

demoButton.addEventListener("click", () => {
  if (demoTimer) {
    stopDemo();
    setStatus(writer ? "Serial 연결됨" : "시뮬레이션 모드");
    return;
  }
  startDemo();
});

[valenceSlider, arousalSlider].forEach((slider) => {
  slider.addEventListener("input", () => {
    stopDemo();
    updateFromInputs();
  });
});

buildPreview();
updateFromInputs();

if (!("serial" in navigator)) {
  setStatus("Web Serial 미지원: 시뮬레이션 모드");
} else if (typeof navigator.serial.addEventListener === "function") {
  navigator.serial.addEventListener("disconnect", (event) => {
    if (!port || event.port !== port) {
      return;
    }

    resetSerial("Serial 연결 해제됨").then(() => {
      appendLog("Serial 포트 연결이 해제되었습니다.");
    });
  });
}
