import argparse
import csv
import json
import math
import os
import statistics
import time
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


CREMA_LABELS = {
    "NEU": "neutral",
    "HAP": "happy",
    "SAD": "sad",
    "ANG": "angry",
    "FEA": "fearful",
    "DIS": "disgust",
}

RAVDESS_LABELS = {
    "01": "neutral",
    "02": "calm",
    "03": "happy",
    "04": "sad",
    "05": "angry",
    "06": "fearful",
    "07": "disgust",
    "08": "surprised",
}

LOW_AROUSAL_LABELS = {"neutral", "calm", "sad"}
HIGH_AROUSAL_LABELS = {"happy", "angry", "fearful", "disgust", "surprised"}


@dataclass(frozen=True)
class DatasetItem:
    path: Path
    label: str
    dataset: str


def clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, float(value)))


def normalize_label(label):
    normalized = (label or "unknown").strip().lower()
    aliases = {
        "anger": "angry",
        "ang": "angry",
        "hap": "happy",
        "happiness": "happy",
        "sadness": "sad",
        "fear": "fearful",
        "fea": "fearful",
        "surprise": "surprised",
        "sur": "surprised",
        "dis": "disgust",
        "neu": "neutral",
    }
    return aliases.get(normalized, normalized or "unknown")


def dataset_item_from_path(path):
    path = Path(path)
    stem = path.stem

    if "_" in stem:
        parts = stem.split("_")
        if len(parts) >= 3 and parts[2].upper() in CREMA_LABELS:
            return DatasetItem(path=path, label=CREMA_LABELS[parts[2].upper()], dataset="crema-d")

    if "-" in stem:
        parts = stem.split("-")
        if len(parts) >= 3 and parts[2] in RAVDESS_LABELS:
            return DatasetItem(path=path, label=RAVDESS_LABELS[parts[2]], dataset="ravdess")

    return DatasetItem(path=path, label="unknown", dataset="custom")


def dataset_item_from_manifest_row(row, base_dir):
    path = Path(row["path"])
    if not path.is_absolute():
        path = Path(base_dir) / path
    return DatasetItem(
        path=path,
        label=normalize_label(row.get("label", "unknown")),
        dataset=(row.get("dataset") or "custom").strip().lower() or "custom",
    )


def serial_payload(valence, arousal):
    return f"v,{clamp(valence):.3f},{clamp(arousal):.3f}"


def infer_label_from_va(valence, arousal):
    valence = clamp(valence)
    arousal = clamp(arousal)
    if valence >= 0.25 and arousal >= 0.25:
        return "happy"
    if valence < -0.25 and arousal >= 0.25:
        return "angry"
    if valence < -0.25 and arousal < 0.25:
        return "sad"
    if arousal < -0.15:
        return "calm"
    return "neutral"


def expected_arousal_class(label):
    label = normalize_label(label)
    if label in HIGH_AROUSAL_LABELS:
        return "high"
    if label in LOW_AROUSAL_LABELS:
        return "low"
    return "unknown"


def predicted_arousal_class(arousal_avg):
    if arousal_avg is None:
        return "unknown"
    return "high" if float(arousal_avg) >= 0.0 else "low"


def read_wav_int16(path):
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        raw = wav_file.readframes(frame_count)

    if sample_width != 2:
        raise ValueError(f"only 16-bit PCM WAV is supported: {path}")

    audio = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels)[:, :1]
    else:
        audio = audio.reshape(-1, 1)
    return audio, rate


def load_manifest(path):
    path = Path(path)
    base_dir = path.parent
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [dataset_item_from_manifest_row(row, base_dir) for row in rows]

    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dataset_item_from_manifest_row(row, base_dir) for row in csv.DictReader(handle)]


def discover_dataset_items(dataset_dir):
    items = []
    for path in sorted(Path(dataset_dir).rglob("*.wav")):
        item = dataset_item_from_path(path)
        if item.label != "unknown":
            items.append(item)
    return items


def write_sine_wav(path, frequency, amplitude, seconds=1.2, rate=16000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.arange(int(rate * seconds), dtype=np.float32)
    wave_data = np.sin(2.0 * math.pi * frequency * samples / rate) * amplitude
    pcm = np.clip(wave_data * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(pcm.tobytes())


def create_synthetic_items(output_dir):
    synthetic_dir = Path(output_dir) / "synthetic_audio"
    specs = [
        ("1001_DFA_HAP_XX.wav", "happy", 660, 0.42),
        ("1002_DFA_SAD_XX.wav", "sad", 180, 0.10),
        ("1003_DFA_ANG_XX.wav", "angry", 520, 0.55),
    ]
    items = []
    for filename, label, frequency, amplitude in specs:
        path = synthetic_dir / filename
        write_sine_wav(path, frequency=frequency, amplitude=amplitude)
        items.append(DatasetItem(path=path, label=label, dataset="synthetic"))
    return items


def percentile(values, pct):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    index = (len(values) - 1) * (pct / 100.0)
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return round(values[int(index)], 3)
    return round(values[low] + (values[high] - values[low]) * (index - low), 3)


def avg(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(statistics.fmean(values), 3)


def replay_item(item, chunk_ms=50, realtime=False, full_ai=False):
    import main as backend

    audio, rate = read_wav_int16(item.path)
    chunk_size = max(1, int(rate * (float(chunk_ms) / 1000.0)))
    first_response_ms = None
    processing_ms = []
    arousal_values = []
    payloads = []

    wall_start = time.perf_counter()
    for offset in range(0, len(audio), chunk_size):
        chunk = audio[offset:offset + chunk_size]
        process_start = time.perf_counter()
        features = backend.compute_live_audio_features(chunk, rate=rate)
        processing_ms.append((time.perf_counter() - process_start) * 1000.0)

        arousal = float(features["arousal_live"])
        confidence = float(features["arousal_confidence"])
        arousal_values.append(arousal)

        payload = serial_payload(0.0, arousal)
        payloads.append({
            "t_ms": round((offset / rate) * 1000.0, 3),
            "payload": payload,
            "arousal_confidence": round(confidence, 3),
        })

        if first_response_ms is None and confidence >= 0.15:
            first_response_ms = round((offset / rate) * 1000.0, 3)

        if realtime:
            time.sleep(len(chunk) / float(rate))

    duration_ms = round((len(audio) / float(rate)) * 1000.0, 3)
    wall_elapsed_ms = round((time.perf_counter() - wall_start) * 1000.0, 3)
    arousal_avg = avg(arousal_values)

    predicted_label = "unknown"
    full_ai_ms = None
    full_ai_error = None
    if full_ai:
        ai_start = time.perf_counter()
        try:
            result = backend.process_audio_result(str(item.path), send_osc=False)
            full_ai_ms = round((time.perf_counter() - ai_start) * 1000.0, 3)
            if result.get("ok"):
                predicted_label = infer_label_from_va(
                    result.get("td_valence", result.get("valence", 0.0)),
                    result.get("td_arousal", result.get("audio_arousal", 0.0)),
                )
            else:
                full_ai_error = result.get("error", "analysis_failed")
        except Exception as exc:
            full_ai_ms = round((time.perf_counter() - ai_start) * 1000.0, 3)
            full_ai_error = str(exc)

    expected_arousal = expected_arousal_class(item.label)
    predicted_arousal = predicted_arousal_class(arousal_avg)

    return {
        "path": str(item.path),
        "dataset": item.dataset,
        "label": item.label,
        "predicted_label": predicted_label,
        "expected_arousal_class": expected_arousal,
        "predicted_arousal_class": predicted_arousal,
        "first_response_ms": first_response_ms,
        "duration_ms": duration_ms,
        "wall_elapsed_ms": wall_elapsed_ms,
        "wall_realtime_ratio": round(wall_elapsed_ms / duration_ms, 4) if duration_ms else None,
        "processing_ms_avg": avg(processing_ms),
        "processing_ms_p95": percentile(processing_ms, 95),
        "payload_count": len(payloads),
        "payload_rate_hz": round(len(payloads) / (duration_ms / 1000.0), 3) if duration_ms else 0.0,
        "arousal_avg": arousal_avg,
        "last_payload": payloads[-1]["payload"] if payloads else None,
        "full_ai_ms": full_ai_ms,
        "full_ai_error": full_ai_error,
        "payloads": payloads[:10],
    }


def summarize_results(results):
    for row in results:
        if row.get("payload_rate_hz") is None and row.get("duration_ms"):
            row["payload_rate_hz"] = round(
                float(row.get("payload_count", 0)) / (float(row["duration_ms"]) / 1000.0),
                3,
            )

    valid_predictions = [
        row for row in results
        if row.get("label") not in (None, "unknown")
        and row.get("predicted_label") not in (None, "unknown")
    ]
    if valid_predictions:
        correct = sum(1 for row in valid_predictions if row["label"] == row["predicted_label"])
        emotion_accuracy = round(correct / len(valid_predictions), 3)
        accuracy_note = "full-ai"
    else:
        emotion_accuracy = None
        accuracy_note = "unavailable"

    valid_arousal = [
        row for row in results
        if row.get("expected_arousal_class", "unknown") != "unknown"
        and row.get("predicted_arousal_class", "unknown") != "unknown"
    ]
    arousal_accuracy = None
    if valid_arousal:
        correct_arousal = sum(
            1 for row in valid_arousal
            if row["expected_arousal_class"] == row["predicted_arousal_class"]
        )
        arousal_accuracy = round(correct_arousal / len(valid_arousal), 3)

    return {
        "sample_count": len(results),
        "emotion_accuracy": emotion_accuracy,
        "accuracy_note": accuracy_note,
        "arousal_direction_accuracy": arousal_accuracy,
        "first_response_ms_avg": avg([row.get("first_response_ms") for row in results]),
        "first_response_ms_p95": percentile([row.get("first_response_ms") for row in results], 95),
        "processing_ms_avg": avg([row.get("processing_ms_avg") for row in results]),
        "processing_ms_p95": percentile([row.get("processing_ms_p95") for row in results], 95),
        "payload_rate_hz_avg": avg([row.get("payload_rate_hz") for row in results]),
        "wall_realtime_ratio_avg": avg([row.get("wall_realtime_ratio") for row in results]),
    }


def write_outputs(output_dir, results, summary, web_latest=False):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"e2e_report_{timestamp}.json"
    csv_path = output_dir / f"e2e_report_{timestamp}.csv"

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "results": results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "path",
        "dataset",
        "label",
        "predicted_label",
        "first_response_ms",
        "processing_ms_avg",
        "payload_rate_hz",
        "arousal_avg",
        "last_payload",
        "full_ai_ms",
        "full_ai_error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({name: row.get(name) for name in fieldnames})

    if web_latest:
        latest_path = Path("web") / "e2e_latest.json"
        latest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report_path, csv_path


def load_items(args):
    if args.synthetic:
        items = create_synthetic_items(args.output_dir)
    elif args.manifest:
        items = load_manifest(args.manifest)
    elif args.dataset_dir:
        items = discover_dataset_items(args.dataset_dir)
    else:
        raise SystemExit("--synthetic, --manifest, or --dataset-dir is required")

    if args.max_samples:
        items = items[:args.max_samples]
    if not items:
        raise SystemExit("No WAV samples found with supported labels")
    return items


def parse_args():
    parser = argparse.ArgumentParser(description="Run local E2E latency and emotion benchmark.")
    parser.add_argument("--dataset-dir", help="Folder containing CREMA-D or RAVDESS WAV files.")
    parser.add_argument("--manifest", help="CSV/JSON manifest with path,label,dataset columns.")
    parser.add_argument("--synthetic", action="store_true", help="Generate small synthetic WAV files for smoke testing.")
    parser.add_argument("--output-dir", default="logs/e2e", help="Directory for JSON/CSV reports.")
    parser.add_argument("--max-samples", type=int, default=0, help="Limit the number of samples.")
    parser.add_argument("--chunk-ms", type=float, default=50.0, help="Replay chunk size in milliseconds.")
    parser.add_argument("--realtime", action="store_true", help="Sleep between chunks to match audio duration.")
    parser.add_argument("--full-ai", action="store_true", help="Run Whisper/Gemini path for emotion accuracy.")
    parser.add_argument("--web-latest", action="store_true", help="Write web/e2e_latest.json for the browser simulator.")
    return parser.parse_args()


def main():
    args = parse_args()
    items = load_items(args)
    results = [
        replay_item(
            item,
            chunk_ms=args.chunk_ms,
            realtime=args.realtime,
            full_ai=args.full_ai,
        )
        for item in items
    ]
    summary = summarize_results(results)
    report_path, csv_path = write_outputs(args.output_dir, results, summary, web_latest=args.web_latest)

    print(json.dumps({
        "summary": summary,
        "report": str(report_path),
        "csv": str(csv_path),
        "samples": [asdict(item) | {"path": str(item.path)} for item in items],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
