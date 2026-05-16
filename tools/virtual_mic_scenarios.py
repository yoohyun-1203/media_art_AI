import argparse
import json
import math
import socket
import struct
import time as time_module
from dataclasses import dataclass
from pathlib import Path
from urllib import request as urlrequest


OSC_HOST = "127.0.0.1"
OSC_PORT = 5000
TD_BRIDGE_URL = "http://127.0.0.1:9988/td"
DEFAULT_OUTPUT = Path("logs/virtual_mic_scenarios/latest.json")
READBACK_PATHS = [
    "/project1/oscin2",
    "/project1/select2",
    "/project1/select3",
]
AROUSAL_MIRROR_STRATEGY = "max"


@dataclass(frozen=True)
class VirtualMicFrame:
    time: float
    left_arousal: float
    right_arousal: float
    valence: float
    left_confidence: float
    right_confidence: float
    valence_confidence: float


@dataclass(frozen=True)
class VirtualMicScenario:
    name: str
    duration: float
    frame_rate: int
    expected_behavior: str
    curve: object


def clamp(value, minimum=-1.0, maximum=1.0):
    return max(minimum, min(maximum, float(value)))


def confidence_from_arousal(arousal):
    return clamp((clamp(arousal) + 1.0) / 2.0, 0.0, 1.0)


def gaussian(progress, center, width):
    return math.exp(-0.5 * ((progress - center) / width) ** 2)


def burst(progress, center=0.35, width=0.10, floor=-0.85, peak=1.0):
    return clamp(floor + (peak - floor) * gaussian(progress, center, width))


def pulse(progress, start, end, floor=-0.85, level=0.55):
    if start <= progress <= end:
        local = (progress - start) / max(end - start, 0.001)
        return clamp(floor + (level - floor) * math.sin(math.pi * local))
    return floor


def make_frame(t, duration, left, right, valence=0.0):
    return VirtualMicFrame(
        time=t,
        left_arousal=clamp(left),
        right_arousal=clamp(right),
        valence=clamp(valence),
        left_confidence=confidence_from_arousal(left),
        right_confidence=confidence_from_arousal(right),
        valence_confidence=0.8,
    )


def silence_curve(t, duration):
    return make_frame(t, duration, -0.86, -0.86, 0.0)


def left_soft_curve(t, duration):
    progress = t / duration
    left = -0.82 + 0.58 * math.sin(math.pi * progress)
    return make_frame(t, duration, left, -0.88, 0.08)


def right_soft_curve(t, duration):
    progress = t / duration
    right = -0.82 + 0.58 * math.sin(math.pi * progress)
    return make_frame(t, duration, -0.88, right, 0.08)


def left_burst_curve(t, duration):
    progress = t / duration
    return make_frame(t, duration, burst(progress), -0.86, -0.18)


def right_burst_curve(t, duration):
    progress = t / duration
    return make_frame(t, duration, -0.86, burst(progress), -0.18)


def balanced_curve(t, duration):
    progress = t / duration
    level = -0.05 + 0.18 * math.sin(math.pi * progress)
    return make_frame(t, duration, level, level, 0.15)


def left_to_right_curve(t, duration):
    progress = t / duration
    left = 0.75 - 1.45 * progress
    right = -0.70 + 1.45 * progress
    return make_frame(t, duration, left, right, 0.2 * math.sin(math.pi * progress))


def right_to_left_curve(t, duration):
    progress = t / duration
    left = -0.70 + 1.45 * progress
    right = 0.75 - 1.45 * progress
    return make_frame(t, duration, left, right, 0.2 * math.sin(math.pi * progress))


def call_and_response_curve(t, duration):
    progress = t / duration
    left = max(
        pulse(progress, 0.08, 0.22, level=0.65),
        pulse(progress, 0.54, 0.66, level=0.55),
    )
    right = max(
        pulse(progress, 0.30, 0.44, level=0.62),
        pulse(progress, 0.74, 0.88, level=0.68),
    )
    return make_frame(t, duration, left, right, 0.05)


def noisy_left_speech_curve(t, duration):
    progress = t / duration
    noise = -0.58 + 0.08 * math.sin(progress * math.tau * 3)
    speech = max(pulse(progress, 0.24, 0.40, floor=noise, level=0.72), pulse(progress, 0.58, 0.72, floor=noise, level=0.52))
    return make_frame(t, duration, speech, noise - 0.08, -0.05)


SCENARIOS = [
    VirtualMicScenario("silence_baseline", 4.0, 20, "Only 1-3 dim LEDs react at both ends.", silence_curve),
    VirtualMicScenario("left_soft_voice", 5.0, 20, "Four to five LEDs wake from the left edge; right stays almost still.", left_soft_curve),
    VirtualMicScenario("right_soft_voice", 5.0, 20, "Four to five LEDs wake from the right edge.", right_soft_curve),
    VirtualMicScenario("left_loud_burst", 4.0, 24, "Left edge rapidly expands to 10-12 LEDs, then decays.", left_burst_curve),
    VirtualMicScenario("right_loud_burst", 4.0, 24, "Right edge rapidly expands to 10-12 LEDs, then decays.", right_burst_curve),
    VirtualMicScenario("balanced_center_voice", 5.0, 20, "Both edges move inward with similar strength and meet near center.", balanced_curve),
    VirtualMicScenario("left_to_right_sweep", 6.0, 20, "Left response fades while right response grows into a sweep.", left_to_right_curve),
    VirtualMicScenario("right_to_left_sweep", 6.0, 20, "Right response fades while left response grows into a sweep.", right_to_left_curve),
    VirtualMicScenario("call_and_response", 6.0, 20, "Left and right LED pulses alternate like call and response.", call_and_response_curve),
    VirtualMicScenario("noisy_room_with_left_speech", 6.0, 20, "Low baseline remains on both sides while left speech bursts stand out.", noisy_left_speech_curve),
]


def scenario_names():
    return [scenario.name for scenario in SCENARIOS]


def get_scenario(name):
    for scenario in SCENARIOS:
        if scenario.name == name:
            return scenario
    raise KeyError(f"unknown scenario: {name}")


def scenario_catalog():
    return [
        {
            "name": scenario.name,
            "duration": scenario.duration,
            "frameRate": scenario.frame_rate,
            "expectedBehavior": scenario.expected_behavior,
        }
        for scenario in SCENARIOS
    ]


def iter_frames(scenario, duration_scale=1.0):
    scale = max(float(duration_scale), 0.001)
    frame_count = max(1, int(round(scenario.duration * scale * scenario.frame_rate)))
    effective_duration = scenario.duration * scale
    for index in range(frame_count):
        if frame_count == 1:
            t = 0.0
        else:
            t = (index / (frame_count - 1)) * effective_duration
        source_t = min(scenario.duration, t / scale)
        yield scenario.curve(source_t, scenario.duration)


def frame_to_messages(frame):
    # Test-mode compatibility mirror: max keeps one loud side visible to old TD mappings.
    arousal_live = max(frame.left_arousal, frame.right_arousal)
    arousal_confidence = max(frame.left_confidence, frame.right_confidence)
    return [
        ("/emotion/left_arousal_live", frame.left_arousal),
        ("/emotion/right_arousal_live", frame.right_arousal),
        ("/emotion/left_arousal_confidence", frame.left_confidence),
        ("/emotion/right_arousal_confidence", frame.right_confidence),
        ("/emotion/arousal_live", arousal_live),
        ("/emotion/arousal_confidence", arousal_confidence),
        ("/emotion/valence_target", frame.valence),
        ("/emotion/valence_confidence", frame.valence_confidence),
        ("/emotion/arousal", arousal_live),
        ("/emotion/valence", frame.valence),
    ]


def frame_to_state(frame):
    return {
        "time": frame.time,
        "left_arousal_live": frame.left_arousal,
        "right_arousal_live": frame.right_arousal,
        "left_arousal_confidence": frame.left_confidence,
        "right_arousal_confidence": frame.right_confidence,
        "arousal_live": max(frame.left_arousal, frame.right_arousal),
        "arousal_confidence": max(frame.left_confidence, frame.right_confidence),
        "valence_target": frame.valence,
        "valence_confidence": frame.valence_confidence,
    }


def _osc_pad(data):
    padding = (4 - (len(data) % 4)) % 4
    return data + (b"\0" * padding)


def _osc_string(value):
    return _osc_pad(value.encode("utf-8") + b"\0")


def encode_osc_float_message(address, value):
    return _osc_string(address) + _osc_string(",f") + struct.pack(">f", float(value))


class OscUdpSender:
    def __init__(self, host=OSC_HOST, port=OSC_PORT):
        self.host = host
        self.port = int(port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, address, value):
        self.socket.sendto(encode_osc_float_message(address, value), (self.host, self.port))

    def close(self):
        self.socket.close()


def read_touchdesigner_channels(path, bridge_url=TD_BRIDGE_URL, timeout=0.8):
    payload = json.dumps({"action": "channels", "path": path}).encode("utf-8")
    req = urlrequest.Request(
        bridge_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_readback(readback_client):
    channels = {}
    errors = {}
    for path in READBACK_PATHS:
        try:
            channels[path] = readback_client(path)
        except Exception as exc:
            errors[path] = str(exc)
            if not channels:
                return {"ok": False, "error": str(exc), "channels": channels, "errors": errors}
    return {"ok": not errors, "channels": channels, "errors": errors}


def run_scenario(scenario, sender=None, duration_scale=1.0, readback=False, readback_client=None, sleep=None, on_frame=None):
    owns_sender = sender is None
    sender = sender or OscUdpSender()
    sleep = sleep or time_module.sleep
    readback_client = readback_client or read_touchdesigner_channels
    started = time_module.time()
    frames_sent = 0
    messages_sent = 0
    errors = []

    try:
        for frame in iter_frames(scenario, duration_scale=duration_scale):
            if on_frame:
                on_frame(frame)
            for address, value in frame_to_messages(frame):
                try:
                    sender.send(address, value)
                    messages_sent += 1
                except Exception as exc:
                    errors.append({"address": address, "error": str(exc)})
            frames_sent += 1
            sleep((1.0 / scenario.frame_rate) * max(float(duration_scale), 0.001))
    finally:
        if owns_sender:
            sender.close()

    result = {
        "name": scenario.name,
        "expectedBehavior": scenario.expected_behavior,
        "duration": scenario.duration,
        "frameRate": scenario.frame_rate,
        "arousalMirrorStrategy": AROUSAL_MIRROR_STRATEGY,
        "osc": {
            "ok": not errors,
            "target": {"host": OSC_HOST, "port": OSC_PORT},
            "framesSent": frames_sent,
            "messagesSent": messages_sent,
            "errors": errors,
        },
        "readback": {"ok": None, "skipped": True},
        "elapsedSeconds": round(time_module.time() - started, 3),
    }

    if readback:
        result["readback"] = collect_readback(readback_client)

    return result


def run_named_scenario(name, **kwargs):
    return run_scenario(get_scenario(name), **kwargs)


def write_report(report, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args():
    parser = argparse.ArgumentParser(description="Run Innerworld virtual left/right microphone OSC scenarios.")
    parser.add_argument("--scenario", choices=scenario_names())
    parser.add_argument("--all", action="store_true", help="Run all virtual microphone scenarios.")
    parser.add_argument("--duration-scale", type=float, default=1.0)
    parser.add_argument("--readback", action="store_true", help="Try TouchDesigner HTTP bridge channel readback after each scenario.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def main():
    args = parse_args()
    selected = SCENARIOS if args.all or not args.scenario else [get_scenario(args.scenario)]
    results = [
        run_scenario(scenario, duration_scale=args.duration_scale, readback=args.readback)
        for scenario in selected
    ]
    report = {
        "ok": all(item["osc"]["ok"] for item in results),
        "scenarioCount": len(results),
        "arousalMirrorStrategy": AROUSAL_MIRROR_STRATEGY,
        "results": results,
    }
    output_path = write_report(report, args.output)
    print(json.dumps({"ok": report["ok"], "output": str(output_path), "scenarioCount": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
