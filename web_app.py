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


def save_live_segment(frames, side=None):
    if not frames:
        return None
    os.makedirs(backend.ARCHIVE_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    prefix = f"live_{side}_" if side else "live_"
    filepath = os.path.join(backend.ARCHIVE_DIR, f"{prefix}{timestamp}.wav")
    audio_data = np.concatenate(frames, axis=0)
    with wave.open(filepath, "wb") as wav_file:
        wav_file.setnchannels(backend.CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(backend.RATE)
        wav_file.writeframes(audio_data.tobytes())
    return filepath


def _merge_side_result(side, result):
    current = get_live().get("result") or {}
    merged = {
        "left": current.get("left"),
        "right": current.get("right"),
    }
    merged[side] = result
    return merged


def side_owns_current_voice(side, own_features, other_features, margin=0.18):
    """Keep one nearby speaker from being claimed by both microphones.

    This is a fast dominance gate, not magical acoustic isolation: if one mic is
    clearly louder/more confident than the other, only that side owns the phrase.
    If both sides are genuinely active at similar strength, both may continue.
    """
    own = float(own_features.get("arousal_confidence", 0.0))
    other = float(other_features.get("arousal_confidence", 0.0))
    if own <= 0.0:
        return False
    return own >= other or (other - own) < margin


def analyze_live_segment(filepath, side):
    try:
        set_live(status="analyzing", message=f"{side} 문장 끝을 감지했습니다. valence를 갱신하는 중입니다.")
        result = backend.process_audio_result(filepath, send_osc=False, source_label=side.upper())
        result["side"] = side
        if result.get("ok"):
            valence_confidence = backend.estimate_valence_confidence(
                result.get("transcript", ""),
                result.get("valence", 0.0),
            )
            result["valence_confidence"] = valence_confidence
            # This is the slow correction layer. Live lighting has already been
            # driven by per-chunk local features while the person was speaking;
            # semantic analysis only refines the color after the phrase ends.
            final_valence = float(result.get("td_valence", result.get("valence", 0.0)))
            if side == "left":
                backend.send_live_osc(
                    left_valence_target=final_valence,
                    left_valence_confidence=valence_confidence,
                    text_final=result.get("transcript", ""),
                )
            else:
                backend.send_live_osc(
                    right_valence_target=final_valence,
                    right_valence_confidence=valence_confidence,
                    text_final=result.get("transcript", ""),
                )
            try:
                backend.manage_archive_limit(backend.ARCHIVE_DIR, max_files=20)
            except Exception:
                pass
            result["touchdesigner"] = read_touchdesigner_state()
            merged_result = _merge_side_result(side, result)
            if get_live().get("running"):
                set_live(
                    status="listening",
                    message=f"{side} valence 갱신 완료. 계속 듣는 중입니다.",
                    result=merged_result,
                    error=None,
                )
            else:
                set_live(status="stopped", message="실시간 정지됨", result=merged_result, error=None)
        else:
            merged_result = _merge_side_result(side, result)
            set_live(
                status="listening" if get_live().get("running") else "stopped",
                message=f"{side} 마지막 구간 분석에 실패했습니다. 계속 들을 수 있습니다.",
                result=merged_result,
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
    side_state = {
        "left": {"frames": [], "recording": False, "silence_start_time": None, "pending": None},
        "right": {"frames": [], "recording": False, "silence_start_time": None, "pending": None},
    }

    set_live(
        running=True,
        status="listening",
        message="실시간 입력을 듣는 중입니다.",
        latest=None,
        result=None,
        error=None,
    )

    analysis_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    left_stream = None
    right_stream = None

    try:
        left_stream = backend.sd.InputStream(
            device=backend.LEFT_MIC_DEVICE,
            samplerate=backend.RATE,
            channels=backend.CHANNELS,
            dtype="int16",
            blocksize=backend.CHUNK,
        )
        right_stream = backend.sd.InputStream(
            device=backend.RIGHT_MIC_DEVICE,
            samplerate=backend.RATE,
            channels=backend.CHANNELS,
            dtype="int16",
            blocksize=backend.CHUNK,
        )
        left_stream.start()
        right_stream.start()

        # 두 마이크 read를 같은 순간에 시작하도록 병렬 executor를 둡니다.
        # 한쪽 read가 끝날 때까지 다른 쪽이 기다리면, 좌우 반응이 미세하게라도
        # 어긋날 수 있으므로 조명 제어 경로에서는 둘을 함께 가져옵니다.
        mic_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

        while not live_stop_event.is_set():
            left_future = mic_executor.submit(left_stream.read, backend.CHUNK)
            right_future = mic_executor.submit(right_stream.read, backend.CHUNK)
            left_data, left_overflowed = left_future.result()
            right_data, right_overflowed = right_future.result()
            now = time.time()
            left_features = backend.compute_live_audio_features(left_data, rate=backend.RATE)
            right_features = backend.compute_live_audio_features(right_data, rate=backend.RATE)

            # legacy /emotion/* 채널은 기존 TouchDesigner 네트워크를 깨지 않기 위한
            # 호환 레이어입니다. 더 강한 쪽을 대표값으로 미러링합니다.
            if left_features["arousal_confidence"] >= right_features["arousal_confidence"]:
                mirror_features = left_features
            else:
                mirror_features = right_features

            # 전시 중 조명은 기다리면 안 됩니다.
            # STT/Gemini를 전혀 거치지 않고, 현재 오디오 청크에서 바로 계산한
            # 좌/우 제어 신호를 각각 보내 색과 에너지가 즉시 반응하게 합니다.
            backend.send_live_osc(
                left_arousal_live=left_features["arousal_live"],
                right_arousal_live=right_features["arousal_live"],
                left_arousal_confidence=left_features["arousal_confidence"],
                right_arousal_confidence=right_features["arousal_confidence"],
                left_valence_target=left_features["valence_live"],
                right_valence_target=right_features["valence_live"],
                left_valence_confidence=left_features["valence_live_confidence"],
                right_valence_confidence=right_features["valence_live_confidence"],
                arousal_live=mirror_features["arousal_live"],
                arousal_confidence=mirror_features["arousal_confidence"],
                valence_target=mirror_features["valence_live"],
                valence_confidence=mirror_features["valence_live_confidence"],
            )

            latest = {
                "left_arousal_live": left_features["arousal_live"],
                "right_arousal_live": right_features["arousal_live"],
                "left_arousal_confidence": left_features["arousal_confidence"],
                "right_arousal_confidence": right_features["arousal_confidence"],
                "left_valence_target": left_features["valence_live"],
                "right_valence_target": right_features["valence_live"],
                "left_valence_confidence": left_features["valence_live_confidence"],
                "right_valence_confidence": right_features["valence_live_confidence"],
                "arousal_live": mirror_features["arousal_live"],
                "arousal_confidence": mirror_features["arousal_confidence"],
                "valence_target": mirror_features["valence_live"],
                "valence_confidence": mirror_features["valence_live_confidence"],
                "timestamp": now,
                "left_overflowed": bool(left_overflowed),
                "right_overflowed": bool(right_overflowed),
            }
            set_live(
                latest=latest,
                status="recording_segment" if any(s["recording"] for s in side_state.values()) else "listening",
                message="음성 구간 수집 중입니다." if any(s["recording"] for s in side_state.values()) else "실시간 입력을 듣는 중입니다.",
            )

            for side, data, features in (
                ("left", left_data, left_features),
                ("right", right_data, right_features),
            ):
                state = side_state[side]
                if state["pending"] and state["pending"].done():
                    try:
                        state["pending"].result()
                    finally:
                        state["pending"] = None

                other_features = right_features if side == "left" else left_features
                volume = backend.analyze_audio_volume(data)
                owns_voice = side_owns_current_voice(side, features, other_features)
                should_collect = backend.should_collect_live_segment(volume, features) and owns_voice
                if should_collect:
                    if not state["recording"]:
                        state["frames"] = []
                        state["recording"] = True
                    state["frames"].append(data.copy())
                    state["silence_start_time"] = None
                elif state["recording"]:
                    state["frames"].append(data.copy())
                    if state["silence_start_time"] is None:
                        state["silence_start_time"] = now
                    elif now - state["silence_start_time"] > backend.SILENCE_LIMIT:
                        filepath = save_live_segment(state["frames"], side=side)
                        state["frames"] = []
                        state["recording"] = False
                        state["silence_start_time"] = None
                        if filepath and state["pending"] is None:
                            state["pending"] = analysis_executor.submit(analyze_live_segment, filepath, side)
                        elif filepath:
                            set_live(message=f"{side} 이전 문장 분석 중이라 이번 구간은 저장만 했습니다.")

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
        if left_stream is not None:
            try:
                left_stream.stop()
                left_stream.close()
            except Exception:
                pass
        if right_stream is not None:
            try:
                right_stream.stop()
                right_stream.close()
            except Exception:
                pass
        if "mic_executor" in locals():
            mic_executor.shutdown(wait=False, cancel_futures=True)
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
