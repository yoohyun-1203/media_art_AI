import sounddevice as sd
import wave
import numpy as np
import time
import os
from dotenv import load_dotenv
from datetime import datetime
import json
import warnings
import concurrent.futures   # 병렬 처리를 위한 모듈 추가
import librosa

from pythonosc.udp_client import SimpleUDPClient
from google import genai
from google.genai import types
from openai import OpenAI

# -------- API 키 보안 설정 --------
# .env 파일에 숨겨둔 변수들을 불러옵니다.
if os.getenv("MEDIA_ART_LOAD_DOTENV", "1") != "0":
    load_dotenv()

gemini_client = None
openai_client = None


def get_gemini_client():
    global gemini_client
    if gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required")
        gemini_client = genai.Client(api_key=api_key)
    return gemini_client


def get_openai_client():
    global openai_client
    if openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
        openai_client = OpenAI(api_key=api_key)
    return openai_client

# Suppress warnings
warnings.filterwarnings("ignore")

import sys
# Windows 터미널 한글 깨짐 방지
if sys.platform == "win32":
    import io
    if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    if (sys.stderr.encoding or "").lower().replace("-", "") != "utf8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# 오디오 설정
DEVICE = 0  # 마이크 디바이스 번호 (0번)
CHANNELS = 1
RATE = 16000
CHUNK = 1024
THRESHOLD = 400  # RMS 임계값 (사람 목소리가 보통 100~300 내외이므로 하향 조정)
SILENCE_LIMIT = 0.5  # 초 (이 시간 동안 조용하면 녹음 종료)

# 폴더 설정
ARCHIVE_DIR = "wav_archive"
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# OSC 설정
OSC_IP = "127.0.0.1"
OSC_PORT = 5000
osc_client = SimpleUDPClient(OSC_IP, OSC_PORT)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

def analyze_audio_volume(data):
    """오디오 데이터의 볼륨(RMS)을 계산합니다."""
    rms = np.sqrt(np.mean(np.square(data.astype(np.float32))))
    return rms

def record_audio():
    """VAD를 사용하여 일정 볼륨 이상의 유의미한 소리를 녹음합니다."""
    print("\n마이크 대기 중... (소리를 내면 녹음이 시작됩니다)")
    
    frames = []
    recording = False
    silence_start_time = None

    stream = sd.InputStream(device=DEVICE, samplerate=RATE, channels=CHANNELS, dtype='int16', blocksize=CHUNK)
    stream.start()

    last_debug_time = time.time()

    try:
        while True:
            data, overflowed = stream.read(CHUNK)
            volume = analyze_audio_volume(data)
            
            # 마이크가 고장났는지 파악하기 위해 미녹음 중일 땐 3초마다 현재 볼륨을 보여줌
            # if not recording and time.time() - last_debug_time > 3:
            #     print(f"[디버그] 현재 마이크 입력 볼륨: {volume:.2f} (녹음 시작 임계값: {THRESHOLD})")
            #     last_debug_time = time.time()

            if volume > THRESHOLD:
                if not recording:
                    print(f"소리 감지! 녹음 시작 (현재 볼륨: {volume:.2f})")
                    recording = True
                frames.append(data.copy())
                silence_start_time = None
            elif recording:
                frames.append(data.copy())
                if silence_start_time is None:
                    silence_start_time = time.time()
                elif time.time() - silence_start_time > SILENCE_LIMIT:
                    print("녹음 종료.")
                    break
    finally:
        stream.stop()
        stream.close()

    if frames:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(ARCHIVE_DIR, f"audio_{timestamp}.wav")
        
        wf = wave.open(filepath, 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2) # 16-bit
        wf.setframerate(RATE)
        audio_data = np.concatenate(frames, axis=0)
        wf.writeframes(audio_data.tobytes())
        wf.close()
        
        return filepath
    return None

def score_arousal_from_metrics(mean_rms, mean_pitch, std_pitch, mean_zcr, mean_centroid):
    """Score arousal from complementary voice cues.

    A lively voice can stay on one bright pitch for a whole phrase.  If we only
    reward pitch *variation*, that voice is unfairly treated as calm.  We keep
    variation as one cue, but also reward sustained pitch height, energy, and
    spectral brightness so a consistently vivid tone still reads as active.
    """
    norm_rms = min(float(mean_rms) / 0.1, 1.0)
    norm_pitch_level = min(float(mean_pitch) / 280.0, 1.0)
    norm_pitch_std = min(float(std_pitch) / 65.0, 1.0)
    norm_zcr = min(float(mean_zcr) / 0.15, 1.0)
    norm_centroid = min(float(mean_centroid) / 3000.0, 1.0)

    # 에너지를 가장 크게 두되, "밝지만 일정한 목소리"도 놓치지 않도록
    # 절대 피치 높이를 별도 축으로 추가합니다.
    arousal_raw = (
        (norm_rms * 0.35)
        + (norm_pitch_level * 0.20)
        + (norm_pitch_std * 0.15)
        + (norm_zcr * 0.15)
        + (norm_centroid * 0.15)
    )
    return clamp((arousal_raw * 2.0) - 1.0)


def analyze_arousal(filepath):
    """librosa를 사용하여 어투 기반 Arousal을 분석합니다."""
    try:
        y, sr = librosa.load(filepath, sr=16000)
        
        # 1. RMS 에너지 계산
        rms = librosa.feature.rms(y=y)[0]
        mean_rms = np.mean(rms)
        
        # 2. 피치(Pitch) 변화량 계산
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y, 
            fmin=librosa.note_to_hz('C2'), 
            fmax=librosa.note_to_hz('C7')
        )
        f0 = f0[voiced_flag] # 음성이 있는 부분만 추출
        
        if len(f0) > 0:
            mean_pitch = np.mean(f0)
            std_pitch = np.std(f0)
        else:
            mean_pitch = 0
            std_pitch = 0
            
        # 3. 추가 특징: Zero Crossing Rate (발화의 선명도/속도)
        zcr = librosa.feature.zero_crossing_rate(y=y)[0]
        mean_zcr = np.mean(zcr)
        
        # 4. 추가 특징: Spectral Centroid (목소리의 밝기)
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        mean_centroid = np.mean(centroid)
            
        # 5. Arousal 계산
        # 변화량만 보지 않고, 지속적으로 높은 톤도 활력으로 인정합니다.
        arousal = score_arousal_from_metrics(
            mean_rms=mean_rms,
            mean_pitch=mean_pitch,
            std_pitch=std_pitch,
            mean_zcr=mean_zcr,
            mean_centroid=mean_centroid,
        )
        
        print(
            "[어투 분석] "
            f"에너지: {mean_rms:.3f}, 평균피치: {mean_pitch:.1f}Hz, "
            f"피치변화: {std_pitch:.1f}Hz, ZCR: {mean_zcr:.3f}, "
            f"밝기: {mean_centroid:.0f}Hz -> Arousal: {arousal:.2f}"
        )
        return arousal
    except Exception as e:
        print(f"어투 분석 중 오류 발생: {e}")
        return 0.0

MOOD_METER_GRID = [
    ["격분한", "공황에 빠진", "스트레스 받는", "초조한", "충격받은", "놀란", "긍정적인", "흥겨운", "아주 신나는", "황홀한"],
    ["격노한", "몹시 화가 난", "좌절한", "신경이 날카로운", "망연자실한", "들뜬", "쾌활한", "동기 부여된", "영감을 받은", "의기양양한"],
    ["화가 치밀어 오른", "겁먹은", "화난", "초조한", "안절부절못하는", "기운이 넘치는", "활발한", "흥분한", "낙관적인", "열광하는"],
    ["불안한", "우려하는", "근심하는", "짜증나는", "거슬리는", "만족스러운", "집중하는", "행복한", "자랑스러운", "짜릿한"],
    ["불쾌한", "골치 아픈", "염려하는", "마음이 불편한", "언짢은", "유쾌한", "기쁜", "희망찬", "재미있는", "더없이 행복한"],
    ["역겨운", "침울한", "실망스러운", "의욕 없는", "냉담한", "속 편한", "태평한", "자족하는", "다정한", "충만한"],
    ["비관적인", "시무룩한", "낙담한", "슬픈", "지루한", "평온한", "안전한", "만족스러운", "감사하는", "감동적인"],
    ["소외된", "비참한", "쓸쓸한", "기죽은", "피곤한", "여유로운", "차분한", "편안한", "축복받은", "안정적인"],
    ["의기소침한", "우울한", "뚱한", "기진맥진한", "지친", "한가로운", "생각에 잠긴", "평화로운", "편한", "근심 걱정 없는"],
    ["절망한", "가망 없는", "고독한", "소모된", "진이 빠진", "나른한", "흐뭇한", "고요한", "안락한", "안온한"]
]

def find_word_in_grid(word):
    for r in range(10):
        for c in range(10):
            if MOOD_METER_GRID[r][c] == word:
                return r, c
    return -1, -1

def clamp(value, min_value=-1.0, max_value=1.0):
    return max(min(float(value), max_value), min_value)


def _as_mono_float_audio(data):
    samples = np.asarray(data)
    if samples.size == 0:
        return np.zeros(0, dtype=np.float32)
    input_dtype = samples.dtype
    if samples.ndim > 1:
        samples = samples[:, 0]
    samples = samples.astype(np.float32, copy=False)
    if np.issubdtype(input_dtype, np.integer):
        info = np.iinfo(input_dtype)
        samples = samples / float(max(abs(info.min), info.max))
    elif np.max(np.abs(samples)) > 1.5:
        samples = samples / 32768.0
    return samples


def compute_live_audio_features(data, rate=RATE):
    """Return ultra-low-latency control features for live lighting.

    This path intentionally avoids STT and cloud AI. It is the path that must
    stay fast enough to drive lighting while the audience is still speaking.
    """
    samples = _as_mono_float_audio(data)
    if samples.size == 0:
        samples = np.zeros(1, dtype=np.float32)

    rms = float(np.sqrt(np.mean(np.square(samples))))

    if samples.size > 1:
        zcr = float(np.mean(np.diff(np.signbit(samples)) != 0))
    else:
        zcr = 0.0

    magnitude = np.abs(np.fft.rfft(samples))
    if float(np.sum(magnitude)) > 0.0:
        freqs = np.fft.rfftfreq(samples.size, d=1.0 / float(rate))
        spectral_centroid = float(np.sum(freqs * magnitude) / np.sum(magnitude))
        # High-frequency ratio is a cheap real-time proxy for a voice that feels
        # bright / forward without paying the latency cost of pitch tracking.
        high_band_energy = float(np.sum(magnitude[freqs >= 1200.0]))
        total_energy = float(np.sum(magnitude))
        high_band_ratio = high_band_energy / total_energy if total_energy > 0.0 else 0.0
    else:
        spectral_centroid = 0.0
        high_band_ratio = 0.0

    norm_energy = clamp(rms / 0.08, 0.0, 1.0)
    norm_zcr = clamp(zcr / 0.15, 0.0, 1.0)
    norm_centroid = clamp(spectral_centroid / 3000.0, 0.0, 1.0)
    norm_high_band = clamp(high_band_ratio / 0.35, 0.0, 1.0)

    # Live lighting should react to more than loudness.  Keeping this path cheap,
    # we blend size (energy) with timbral "lift" (centroid + high band content)
    # so a steady but bright voice no longer looks falsely subdued.
    arousal_confidence = clamp((rms - 0.01) / 0.08, 0.0, 1.0)
    arousal_raw = (
        (norm_energy * 0.35)
        + (norm_zcr * 0.15)
        + (norm_centroid * 0.25)
        + (norm_high_band * 0.25)
    )

    # Lighting uses arousal as a "how alive does this feel right now?" control.
    # Once we are confident that speech exists, a vivid steady tone should cross
    # into positive territory even when it is not especially loud.  Silence is
    # still pinned low so the room can visibly wake up when a person speaks.
    if arousal_confidence < 0.1:
        arousal_live = -1.0
    else:
        arousal_live = clamp((arousal_raw - 0.28) / 0.42)

    # "Live valence" is not semantic sentiment. It is an immediate timbre-based
    # lighting estimate so the artwork can choose a color before STT/LLM results
    # exist. Brighter / clearer speech leans warm-positive; noisier, harsher
    # speech leans cool-negative. Silence stays neutral.
    brightness_bias = clamp((spectral_centroid - 900.0) / 2100.0, 0.0, 1.0)
    noisiness_bias = clamp(zcr / 0.20, 0.0, 1.0)
    valence_raw = (brightness_bias * 0.65) - (noisiness_bias * 0.35)
    valence_live = clamp((valence_raw * 2.0) - 0.35)
    valence_live_confidence = arousal_confidence
    if arousal_confidence < 0.1:
        valence_live = 0.0

    return {
        "arousal_live": arousal_live,
        "arousal_confidence": arousal_confidence,
        "valence_live": valence_live,
        "valence_live_confidence": valence_live_confidence,
        "rms": rms,
        "zcr": zcr,
        "spectral_centroid": spectral_centroid,
        "high_band_ratio": high_band_ratio,
    }


def estimate_valence_confidence(transcript, valence):
    clean_text = (transcript or "").strip()
    if not clean_text:
        return 0.0
    length_score = clamp(len(clean_text) / 16.0, 0.0, 1.0)
    polarity_score = clamp(abs(valence), 0.0, 1.0)
    return clamp(0.15 + (length_score * 0.55) + (polarity_score * 0.30), 0.0, 1.0)


def should_collect_live_segment(volume, features, threshold=THRESHOLD, min_confidence=0.45):
    return float(volume) > float(threshold) and float(features.get("arousal_confidence", 0.0)) >= min_confidence


def send_live_osc(
    arousal_live=None,
    arousal_confidence=None,
    valence_target=None,
    valence_confidence=None,
    text_partial=None,
    text_final=None,
):
    values = {
        "/emotion/arousal_live": arousal_live,
        "/emotion/arousal_confidence": arousal_confidence,
        "/emotion/valence_target": valence_target,
        "/emotion/valence_confidence": valence_confidence,
        "/emotion/arousal": arousal_live,
        "/emotion/valence": valence_target,
        "/emotion/text_partial": text_partial,
        "/emotion/text_final": text_final,
    }
    for address, value in values.items():
        if value is not None:
            osc_client.send_message(address, value)


def extract_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("JSON object not found in Gemini response")
    return json.loads(text[start:end + 1])

def get_ai_emotion(transcript, arousal=0.0):
    """Gemini API로 텍스트의 Valence를 판단하고 Mood Meter 단어로 매핑합니다."""
    start_time = time.time()

    prompt = f"""
한국어 발화의 감정 Valence를 분석하세요.
Valence는 -1.0(매우 부정)부터 1.0(매우 긍정) 사이의 숫자입니다.
반드시 JSON만 반환하세요.

발화: {transcript}
어투 기반 Arousal 참고값: {arousal:.3f}

형식:
{{"valence": 0.0}}
"""

    try:
        response = get_gemini_client().models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                maxOutputTokens=48,
                responseMimeType="application/json",
                thinkingConfig=types.ThinkingConfig(thinkingBudget=0),
            ),
        )
        result = extract_json(response.text or "{}")
        valence = clamp(result.get("valence", 0.0))
    except RuntimeError as e:
        if str(e) == "GEMINI_API_KEY is required":
            raise
        print(f" -> Gemini 감정 분석 오류: {e}")
        valence = 0.0
    except Exception as e:
        print(f" -> Gemini 감정 분석 오류: {e}")
        valence = 0.0
    
    # 🌟 핵심: Valence(긍정/부정)와 Arousal(흥분도) 2개의 수치만으로 100개 단어 맵에서 즉시 1개를 매핑!
    # API가 단어를 고르게 하는 대신, 수치를 기반으로 O(1) 속도로 좌표를 찾습니다.
    # row: Arousal (-1.0 ~ 1.0) -> 9 ~ 0
    # col: Valence (-1.0 ~ 1.0) -> 0 ~ 9
    row = int(max(min((1.0 - arousal) / 2.0 * 9.99, 9.0), 0.0))
    col = int(max(min((valence + 1.0) / 2.0 * 9.99, 9.0), 0.0))
    emotion_word = MOOD_METER_GRID[row][col]

    elapsed_time = time.time() - start_time
    print(f" -> ⏱️ Gemini 감정 분석 소요 시간: {elapsed_time:.3f}초 (Valence: {valence:.2f})")
    
    return emotion_word, valence


def map_touchdesigner_values(emotion_word, audio_arousal):
    row, col = find_word_in_grid(emotion_word)

    if row != -1 and col != -1:
        if col < 5 and row < 5:
            return "빨강 (Red)", -0.7, 0.7
        if col >= 5 and row < 5:
            return "노랑 (Yellow)", 0.7, 0.7
        if col < 5 and row >= 5:
            return "파랑 (Blue)", -0.7, -0.7
        return "초록 (Green)", 0.7, -0.7

    if audio_arousal > 0:
        return "노랑 (Yellow) - 로컬대체", 0.5, audio_arousal
    return "파랑 (Blue) - 로컬대체", -0.5, audio_arousal


def send_emotion_osc(emotion_word, color_name, td_valence, td_arousal, text):
    print(f">> OSC 데이터 전송 (포트 {OSC_PORT})")
    osc_client.send_message("/emotion/word", emotion_word)
    osc_client.send_message("/emotion/color_name", color_name)
    osc_client.send_message("/emotion/valence", float(td_valence))
    osc_client.send_message("/emotion/arousal", float(td_arousal))
    osc_client.send_message("/emotion/text", text)
    print("전송 완료!")


def analyze_text_result(text, audio_arousal=0.0, send_osc=True):
    emotion_word, valence = get_ai_emotion(text, arousal=audio_arousal)
    color_name, td_valence, td_arousal = map_touchdesigner_values(emotion_word, audio_arousal)

    result = {
        "ok": True,
        "transcript": text,
        "emotion_word": emotion_word,
        "valence": valence,
        "audio_arousal": audio_arousal,
        "color_name": color_name,
        "td_valence": td_valence,
        "td_arousal": td_arousal,
        "osc_ip": OSC_IP,
        "osc_port": OSC_PORT,
    }

    if send_osc:
        send_emotion_osc(emotion_word, color_name, td_valence, td_arousal, text)
        result["osc_sent"] = True
    else:
        result["osc_sent"] = False

    return result


def process_audio_result(filepath, send_osc=True):
    """녹음 파일을 STT/감정 분석한 뒤 웹 UI가 표시할 수 있는 결과 dict를 반환합니다."""
    absolute_path = os.path.abspath(filepath) # 절대 경로 (에러 방지)
    print(f"\n[1/3] 음성 인식(STT) 진행 중... (파일: {absolute_path})")
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as arousal_executor:
            arousal_future = arousal_executor.submit(analyze_arousal, absolute_path)

            # 1. Whisper STT
            prompt_hint = "안녕? 난 지금 기분이 아주 좋아. 넌 어때? 우울하거나 슬프진 않아? 정말 짜증나고 화가 나. 너무 신기하고 재미있다!"
            with open(absolute_path, "rb") as audio_file:
                transcript = get_openai_client().audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ko",
                    prompt=prompt_hint
                ).text.strip()
            
            print(f" -> 인식된 텍스트: {transcript}")


            if not transcript:
                print("인식된 텍스트가 없어 분석을 건너뜁니다.")
                return {"ok": False, "error": "인식된 텍스트가 없습니다.", "filepath": absolute_path}

            print("[2/3 & 3/3] AI 감정 분석과 오디오 어투 분석을 진행합니다...")
        
            # 오디오 특징 분석은 Whisper API와 병렬로 시작해 둔 결과를 사용합니다.
            audio_arousal = arousal_future.result()

        result = analyze_text_result(transcript, audio_arousal=audio_arousal, send_osc=False)
        color_name = result["color_name"]
        emotion_word = result["emotion_word"]
        td_valence = result["td_valence"]
        td_arousal = result["td_arousal"]

        # 결과 요약 출력
        print(f"\n==================================================")
        print(f"🗣️ 인식된 말: {transcript}")
        print(f"🤖 최종 감정단어: {emotion_word} -> 🎨 매핑 색상: {color_name}")
        print(f"📊 터치디자이너 전송 수치 - Valence: {td_valence}, Arousal: {td_arousal}")
        print(f"==================================================")

        # 5. OSC 전송
        if send_osc:
            send_emotion_osc(emotion_word, color_name, td_valence, td_arousal, transcript)
            result["osc_sent"] = True
        
        result["filepath"] = absolute_path
        return result

    except Exception as e:
        print(f"데이터 처리 중 오류 발생: {e}")
        return {"ok": False, "error": str(e), "filepath": absolute_path}


def process_audio(filepath):
    """터미널용 기존 호출을 위해 성공 여부만 반환합니다."""
    return bool(process_audio_result(filepath).get("ok"))

def manage_archive_limit(archive_dir, max_files=20):
    """폴더 내의 wav 파일 개수를 제한하고, 초과 시 오래된 파일부터 삭제합니다."""
    try:
        files = [os.path.join(archive_dir, f) for f in os.listdir(archive_dir) if f.endswith('.wav')]
        if len(files) > max_files:
            # 오래된 파일이 앞에 오도록 수정 시간 기준 정렬
            files.sort(key=os.path.getmtime)
            
            # 초과하는 개수만큼 삭제
            files_to_delete = len(files) - max_files
            for i in range(files_to_delete):
                os.remove(files[i])
                print(f"[아카이브 정리] 최대 개수({max_files}개) 초과로 오래된 파일을 삭제했습니다: {files[i]}")
    except Exception as e:
        print(f"아카이브 관리 중 오류 발생: {e}")

def main():
    print("=== 미디어아트 100% 로컬 백엔드 시스템 시작 ===")
    
    # 텍스트 테스트 모드 추가
    test_mode_input = input("지금 소리를 내기 힘든 환경이신가요? 텍스트 모드로 테스트할까요? (y/n): ").strip().lower()
    is_text_mode = (test_mode_input == 'y')
    
    if is_text_mode:
        print("\n[텍스트 모드 활성화] 마이크 대신 키보드로 감정을 입력합니다.")
    else:
        print("\n[마이크 모드 활성화] 소리를 내면 녹음이 시작됩니다.")

    try:
        while True:
            if is_text_mode:
                text = input("\n[텍스트 모드] 분석할 문장을 입력하세요 (종료하려면 'q' 입력): ")
                if text.lower() == 'q':
                    break
                
                print(f"\n[1/3] 음성 인식(STT) 건너뜀 (입력된 텍스트: {text})")
                print("[2/3] AI 감정 분석 중...")
                
                emotion_word, valence = get_ai_emotion(text)
                audio_arousal = 0.0 # 텍스트 모드이므로 어투는 기본값
                
                # 4. 무드 미터 그리드에서 색상 찾기
                row, col = find_word_in_grid(emotion_word)
                
                if row != -1 and col != -1:
                    if col < 5 and row < 5:
                        color_name, td_valence, td_arousal = "빨강 (Red)", -0.7, 0.7
                    elif col >= 5 and row < 5:
                        color_name, td_valence, td_arousal = "노랑 (Yellow)", 0.7, 0.7
                    elif col < 5 and row >= 5:
                        color_name, td_valence, td_arousal = "파랑 (Blue)", -0.7, -0.7
                    else:
                        color_name, td_valence, td_arousal = "초록 (Green)", 0.7, -0.7
                else:
                    color_name, td_valence, td_arousal = "기본값 (파랑)", -0.5, 0.0

                print(f"\n==================================================")
                print(f"🗣️ 입력된 텍스트: {text}")
                print(f"🤖 최종 감정단어: {emotion_word} -> 🎨 매핑 색상: {color_name}")
                print(f"📊 터치디자이너 전송 수치 - Valence: {td_valence}, Arousal: {td_arousal}")
                print(f"==================================================")

                print(f">> OSC 데이터 전송 (포트 5000)")
                osc_client.send_message("/emotion/word", emotion_word)
                osc_client.send_message("/emotion/color_name", color_name)
                osc_client.send_message("/emotion/valence", float(td_valence))
                osc_client.send_message("/emotion/arousal", float(td_arousal))
                osc_client.send_message("/emotion/text", text)
                print("전송 완료!")
                
            else:
                # 1. 소리 감지 및 녹음
                filepath = record_audio()
                
                # 2. 녹음 파일이 있으면 분석 및 OSC 전송
                if filepath:
                    success = process_audio(filepath)
                    if success:
                        # 분석이 완료된 경우에만 최대 20개 유지 규칙 적용
                        manage_archive_limit(ARCHIVE_DIR, max_files=20)
                    else:
                        # 텍스트가 없거나 에러가 난 경우 해당 파일은 삭제
                        try:
                            if os.path.exists(filepath):
                                os.remove(filepath)
                                print(f"[알림] 의미 없는 소리이므로 저장하지 않고 삭제했습니다: {filepath}\n")
                        except OSError:
                            pass
    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다.")

if __name__ == "__main__":
    main()
