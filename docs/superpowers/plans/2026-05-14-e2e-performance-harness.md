# E2E Performance Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local E2E performance harness that replays labeled WAV files through the backend live signal path, reports latency/throughput/accuracy, and visualizes Arduino LED output in a browser simulator.

**Architecture:** Keep the benchmark local and explicit: `e2e_performance.py` reads a labeled dataset folder or manifest, replays audio chunks through existing backend feature functions, emits virtual serial payloads, and writes JSON/CSV summaries. `web/arduino_simulator.html` and `web/arduino_simulator.js` render `v,<valence>,<arousal>` payloads without real hardware.

**Tech Stack:** Python standard library, `wave`, `numpy`, existing backend modules, browser JavaScript/HTML/CSS.

---

### Task 1: E2E Metrics Core

**Files:**
- Create: `tests/test_e2e_performance.py`
- Create: `e2e_performance.py`

- [ ] **Step 1: Write failing tests**

```python
def test_crema_filename_label_parser():
    item = e2e_performance.dataset_item_from_path(Path("1001_DFA_HAP_XX.wav"))
    assert item.label == "happy"

def test_serial_payload_uses_existing_protocol():
    assert e2e_performance.serial_payload(0.25, -0.5) == "v,0.250,-0.500"

def test_summarize_results_reports_latency_and_accuracy():
    results = [
        {"label": "happy", "predicted_label": "happy", "first_response_ms": 80.0, "duration_ms": 1000.0},
        {"label": "sad", "predicted_label": "happy", "first_response_ms": 120.0, "duration_ms": 1000.0},
    ]
    summary = e2e_performance.summarize_results(results)
    assert summary["sample_count"] == 2
    assert summary["emotion_accuracy"] == 0.5
    assert summary["first_response_ms_avg"] == 100.0
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\venv311\Scripts\python.exe -m unittest tests.test_e2e_performance -v`

- [ ] **Step 3: Implement minimal E2E metrics**

Add filename parsing for CREMA-D/RAVDESS, serial payload formatting, chunk replay, first-response latency, update rate, and summary aggregation.

- [ ] **Step 4: Verify**

Run: `.\venv311\Scripts\python.exe -m unittest tests.test_e2e_performance -v`

### Task 2: Arduino Web Simulator

**Files:**
- Create: `web/arduino_simulator.html`
- Create: `web/arduino_simulator.js`
- Modify: `web/style.css`
- Modify: `README.md`

- [ ] **Step 1: Write syntax checks**

Run before implementation: `node --check web\arduino_simulator.js`

- [ ] **Step 2: Implement simulator**

Render LED rows, accept pasted payloads and BroadcastChannel messages, show latency and recent event log.

- [ ] **Step 3: Verify**

Run: `node --check web\arduino_simulator.js`

### Task 3: End-to-End Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run full local checks**

Run:

```powershell
.\venv311\Scripts\python.exe -m unittest discover -s tests -v
.\venv311\Scripts\python.exe -m py_compile main.py web_app.py live_signal.py local_ser.py ambient_emotion.py e2e_performance.py
node --check web\app.js
node --check web\serial_visualizer.js
node --check web\arduino_simulator.js
```

- [ ] **Step 2: Run synthetic E2E smoke**

Run: `.\venv311\Scripts\python.exe e2e_performance.py --synthetic --output-dir logs\e2e --max-samples 3`

- [ ] **Step 3: Commit**

Commit the harness and docs on `codex/live-emotion-ser-integration`, then push to the writable fork remote.
