import concurrent.futures
import json
import os
import threading
import time
import traceback
import wave
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request as urlrequest

import numpy as np

import main as backend
from live_signal import compose_led_mood_signal
from tools import virtual_mic_scenarios


HOST = "127.0.0.1"
PORT = int(os.getenv("WEB_PORT", "8765"))
ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
TD_BRIDGE_URL = os.getenv("TD_BRIDGE_URL", "http://127.0.0.1:9988/td")
TD_BRIDGE_TIMEOUT = float(os.getenv("TD_BRIDGE_TIMEOUT", "0.8"))

job_lock = threading.Lock()
job_state = {
    "running": False,
    "status": "idle",
    "message": "대기 중",
    "result": None,
    "error": None,
}

live_lock = threading.Lock()
live_stop_event = threading.Event()
live_thread = None
live_state = {
    "running": False,
    "status": "idle",
    "message": "실시간 대기 중",
    "latest": None,
    "result": None,
    "error": None,
}


virtual_mic_lock = threading.Lock()
virtual_mic_state = {
    "running": False,
    "status": "idle",
    "message": "Virtual mic ready",
    "latest": None,
    "result": None,
    "error": None,
    "scenarios": virtual_mic_scenarios.scenario_catalog(),
    "arousalMirrorStrategy": virtual_mic_scenarios.AROUSAL_MIRROR_STRATEGY,
}


def set_job(**updates):
    with job_lock:
        job_state.update(updates)
        return dict(job_state)


def get_job():
    with job_lock:
        return dict(job_state)


def set_live(**updates):
    with live_lock:
        live_state.update(updates)
        return dict(live_state)


def get_live():
    with live_lock:
        return dict(live_state)


def set_virtual_mic(**updates):
    with virtual_mic_lock:
        virtual_mic_state.update(updates)
        return dict(virtual_mic_state)


def get_virtual_mic():
    with virtual_mic_lock:
        return dict(virtual_mic_state)


def run_virtual_mic_scenario(name, duration_scale=1.0, readback=False):
    def on_frame(frame):
        latest = virtual_mic_scenarios.frame_to_state(frame)
        set_virtual_mic(
            latest=latest,
            message=f"Running {name}: t={latest['time']:.2f}s",
        )

    try:
        set_virtual_mic(
            running=True,
            status="running",
            message=f"Running {name}",
            latest=None,
            result=None,
            error=None,
        )
        result = virtual_mic_scenarios.run_named_scenario(
            name=name,
            duration_scale=duration_scale,
            readback=readback,
            on_frame=on_frame,
        )
        set_virtual_mic(
            running=False,
            status="done",
            message=f"Finished {name}",
            result=result,
            error=None,
        )
        return result
    except Exception as exc:
        set_virtual_mic(
            running=False,
            status="error",
            message=f"Virtual mic failed: {exc}",
            error=str(exc),
        )
        raise


def td_channels(path):
    payload = json.dumps({"action": "channels", "path": path}).encode("utf-8")
    req = urlrequest.Request(
        TD_BRIDGE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=TD_BRIDGE_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def read_touchdesigner_state():
    paths = [
        "/project1/select2",
        "/project1/select3",
        "/project1/joy",
        "/project1/sad",
        "/project1/angry",
        "/project1/relaxed",
        "/project1/RGBs",
        "/project1/oscin2",
    ]
    state = {}
    for path in paths:
        try:
            state[path] = td_channels(path).get("channels", {})
        except Exception as exc:
            state[path] = {"error": str(exc)}
    return state


def analyze_worker():
    try:
        set_job(
            running=True,
            status="recording",
            message="마이크 입력을 기다리는 중입니다. 소리를 내면 녹음이 시작됩니다.",
            result=None,
            error=None,
        )
        filepath = backend.record_audio()
        if not filepath:
            set_job(
                running=False,
                status="error",
                message="녹음된 오디오가 없습니다.",
                error="no_audio_recorded",
            )
            return

        set_job(status="analyzing", message="OpenAI Whisper API와 Gemini API로 분석 중입니다.")
        result = backend.process_audio_result(filepath)

        if result.get("ok"):
            try:
                backend.manage_archive_limit(backend.ARCHIVE_DIR, max_files=20)
            except Exception:
                pass
            result["valence_confidence"] = backend.estimate_valence_confidence(
                result.get("transcript", ""),
                result.get("valence", 0.0),
            )
            result["touchdesigner"] = read_touchdesigner_state()
            set_job(
                running=False,
                status="done",
                message="TouchDesigner 전송까지 완료했습니다.",
                result=result,
                error=None,
            )
        else:
            set_job(
                running=False,
                status="error",
                message="분석에 실패했습니다.",
                result=result,
                error=result.get("error", "analysis_failed"),
            )
    except Exception as exc:
        set_job(
            running=False,
            status="error",
            message="실행 중 오류가 발생했습니다.",
            error=str(exc),
            result={"traceback": traceback.format_exc()},
        )


def run_test_osc():
    text = "웹 테스트 감정 메시지"
    result = backend.analyze_text_result(text, audio_arousal=0.7)
    result["valence_confidence"] = backend.estimate_valence_confidence(
        result.get("transcript", ""),
        result.get("valence", 0.0),
    )
    live_osc = {
        "arousal_live": float(result.get("td_arousal", result.get("audio_arousal", 0.0))),
        "arousal_confidence": 1.0,
        "valence_target": float(result.get("td_valence", result.get("valence", 0.0))),
        "valence_confidence": result["valence_confidence"],
    }
    backend.send_live_osc(
        **live_osc,
        text_final=result.get("transcript", ""),
    )
    result["live_osc"] = live_osc
    result["live_osc_sent"] = True
    result["touchdesigner"] = read_touchdesigner_state()
    return result


def send_composed_live_signal(
    arousal_live,
    arousal_confidence,
    latest_valence,
    latest_valence_confidence,
    ambient_valence,
    ambient_arousal,
    has_mic_activity,
):
    signal = compose_led_mood_signal(
        arousal_live=arousal_live,
        arousal_confidence=arousal_confidence,
        latest_valence=latest_valence,
        latest_valence_confidence=latest_valence_confidence,
        ambient_valence=ambient_valence,
        ambient_arousal=ambient_arousal,
        has_mic_activity=has_mic_activity,
    )
    backend.send_live_osc(
        arousal_live=signal["arousal"],
        arousal_confidence=arousal_confidence,
        valence_target=signal["valence"],
        valence_confidence=latest_valence_confidence,
    )
    return signal


def save_live_segment(frames):
    if not frames:
        return None
    os.makedirs(backend.ARCHIVE_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filepath = os.path.join(backend.ARCHIVE_DIR, f"live_{timestamp}.wav")
    audio_data = np.concatenate(frames, axis=0)
    with wave.open(filepath, "wb") as wav_file:
        wav_file.setnchannels(backend.CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(backend.RATE)
        wav_file.writeframes(audio_data.tobytes())
    return filepath


def analyze_live_segment(filepath):
    try:
        set_live(status="analyzing", message="문장 끝을 감지했습니다. valence를 갱신하는 중입니다.")
        result = backend.process_audio_result(filepath, send_osc=True)
        if result.get("ok"):
            valence_confidence = backend.estimate_valence_confidence(
                result.get("transcript", ""),
                result.get("valence", 0.0),
            )
            result["valence_confidence"] = valence_confidence
            # This is the slow correction layer. Live lighting has already been
            # driven by per-chunk local features while the person was speaking;
            # semantic analysis only refines the color after the phrase ends.
            backend.send_live_osc(
                valence_target=float(result.get("td_valence", result.get("valence", 0.0))),
                valence_confidence=valence_confidence,
                text_final=result.get("transcript", ""),
            )
            try:
                backend.manage_archive_limit(backend.ARCHIVE_DIR, max_files=20)
            except Exception:
                pass
            result["touchdesigner"] = read_touchdesigner_state()
            if get_live().get("running"):
                set_live(
                    status="listening",
                    message="valence 갱신 완료. 계속 듣는 중입니다.",
                    result=result,
                    error=None,
                )
            else:
                set_live(status="stopped", message="실시간 정지됨", result=result, error=None)
        else:
            set_live(
                status="listening" if get_live().get("running") else "stopped",
                message="마지막 구간 분석에 실패했습니다. 계속 들을 수 있습니다.",
                result=result,
                error=result.get("error", "analysis_failed"),
            )
    except Exception as exc:
        set_live(
            status="error",
            message="실시간 구간 분석 중 오류가 발생했습니다.",
            result={"traceback": traceback.format_exc()},
            error=str(exc),
        )


def live_worker():
    frames = []
    recording = False
    silence_start_time = None
    pending_analysis = None

    set_live(
        running=True,
        status="listening",
        message="실시간 입력을 듣는 중입니다.",
        latest=None,
        result=None,
        error=None,
    )

    analysis_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    stream = None

    try:
        stream = backend.sd.InputStream(
            device=backend.DEVICE,
            samplerate=backend.RATE,
            channels=backend.CHANNELS,
            dtype="int16",
            blocksize=backend.CHUNK,
        )
        stream.start()

        while not live_stop_event.is_set():
            data, overflowed = stream.read(backend.CHUNK)
            now = time.time()
            features = backend.compute_live_audio_features(data, rate=backend.RATE)

            # 전시 중 조명은 기다리면 안 됩니다.
            # STT/Gemini를 전혀 거치지 않고, 현재 오디오 청크에서 바로 계산한
            # 제어 신호를 보내 색과 에너지가 즉시 반응하게 합니다.
            backend.send_live_osc(
                arousal_live=features["arousal_live"],
                arousal_confidence=features["arousal_confidence"],
                valence_target=features["valence_live"],
                valence_confidence=features["valence_live_confidence"],
            )

            latest = {
                **features,
                "timestamp": now,
                "overflowed": bool(overflowed),
            }
            set_live(
                latest=latest,
                status="recording_segment" if recording else "listening",
                message="음성 구간 수집 중입니다." if recording else "실시간 입력을 듣는 중입니다.",
            )

            if pending_analysis and pending_analysis.done():
                try:
                    pending_analysis.result()
                finally:
                    pending_analysis = None

            volume = backend.analyze_audio_volume(data)
            if backend.should_collect_live_segment(volume, features):
                if not recording:
                    frames = []
                    recording = True
                frames.append(data.copy())
                silence_start_time = None
            elif recording:
                frames.append(data.copy())
                if silence_start_time is None:
                    silence_start_time = now
                elif now - silence_start_time > backend.SILENCE_LIMIT:
                    filepath = save_live_segment(frames)
                    frames = []
                    recording = False
                    silence_start_time = None
                    if filepath and pending_analysis is None:
                        pending_analysis = analysis_executor.submit(analyze_live_segment, filepath)
                    elif filepath:
                        set_live(message="이전 문장 분석 중이라 이번 구간은 저장만 했습니다.")

        set_live(running=False, status="stopped", message="실시간 정지됨")
    except Exception as exc:
        set_live(
            running=False,
            status="error",
            message="실시간 입력을 시작하지 못했습니다.",
            error=str(exc),
            result={"traceback": traceback.format_exc()},
        )
    finally:
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        analysis_executor.shutdown(wait=False, cancel_futures=True)


def start_live():
    global live_thread
    state = get_live()
    if state.get("running"):
        return False, state
    live_stop_event.clear()
    live_thread = threading.Thread(target=live_worker, daemon=True)
    live_thread.start()
    return True, get_live()


def stop_live():
    live_stop_event.set()
    state = set_live(running=False, status="stopping", message="실시간 정지 요청을 보냈습니다.")
    return state


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print("[web]", fmt % args)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        if self.path == "/api/status":
            self.send_json(get_job())
            return
        if self.path == "/api/live/status":
            self.send_json(get_live())
            return
        if self.path == "/api/virtual-mic/status":
            self.send_json(get_virtual_mic())
            return
        if self.path == "/api/virtual-mic/scenarios":
            self.send_json({
                "ok": True,
                "scenarios": virtual_mic_scenarios.scenario_catalog(),
                "arousalMirrorStrategy": virtual_mic_scenarios.AROUSAL_MIRROR_STRATEGY,
            })
            return
        if self.path == "/api/health":
            self.send_json({
                "ok": True,
                "backend": "media_art_backend",
                "osc": {"ip": backend.OSC_IP, "port": backend.OSC_PORT},
                "touchdesignerBridge": TD_BRIDGE_URL,
                "live": get_live(),
                "virtualMic": get_virtual_mic(),
            })
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/start":
            state = get_job()
            if state.get("running"):
                self.send_json({"ok": False, "error": "already_running", "state": state}, status=409)
                return
            thread = threading.Thread(target=analyze_worker, daemon=True)
            thread.start()
            self.send_json({"ok": True, "state": get_job()})
            return

        if self.path == "/api/live/start":
            started, state = start_live()
            if not started:
                self.send_json({"ok": False, "error": "already_running", "state": state}, status=409)
                return
            self.send_json({"ok": True, "state": state})
            return

        if self.path == "/api/live/stop":
            self.send_json({"ok": True, "state": stop_live()})
            return

        if self.path == "/api/virtual-mic/run":
            try:
                body = self.read_json()
                name = body.get("scenario") or "silence_baseline"
                duration_scale = float(body.get("durationScale", 1.0))
                readback = bool(body.get("readback", False))
                state = get_virtual_mic()
                if state.get("running"):
                    self.send_json({"ok": False, "error": "already_running", "state": state}, status=409)
                    return
                thread = threading.Thread(
                    target=run_virtual_mic_scenario,
                    args=(name, duration_scale, readback),
                    daemon=True,
                )
                thread.start()
                self.send_json({"ok": True, "state": get_virtual_mic()})
            except KeyError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if self.path == "/api/test-osc":
            try:
                self.send_json({"ok": True, "result": run_test_osc()})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return

        self.send_json({"ok": False, "error": "not_found"}, status=404)


def main():
    if not WEB_ROOT.exists():
        raise RuntimeError(f"web directory not found: {WEB_ROOT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"웹 컨트롤러 실행 중: http://{HOST}:{PORT}")
    print("종료하려면 Ctrl+C")
    server.serve_forever()


if __name__ == "__main__":
    main()
