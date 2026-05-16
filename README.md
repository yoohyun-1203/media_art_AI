# Media Art Audio Analysis Backend

음성 입력을 분석해 TouchDesigner, Arduino LED, 브라우저 테스트 UI로 보낼 감정/반응 신호를 만드는 Python backend입니다. 현재 브랜치는 `codex/live-emotion-ser-integration`이며, 실시간 제어는 빠른 로컬 오디오 feature 기반 반응을 먼저 보내고, 의미 기반 감정 보정은 느린 레이어로 얹는 방향입니다.

## 현재 구현 요약

- `live_signal.py`
  - `NoiseFloorTracker`, `EnvelopeSmoother`, `SegmentEndpoint`, `SpeakerBleedGate`, `compose_led_mood_signal`
  - 즉시 반응용 arousal/envelope/segment 판단을 담당합니다.
- `local_ser.py`
  - `RollingSerWindow.push(pcm)`, `LocalSerFallback`
  - 실제 SER 모델이 아니라 adapter/fallback 레이어입니다. 모델 선택과 통합은 남은 작업입니다.
- `ambient_emotion.py`
  - confidence-weighted `average_mood(items)`
  - 느린 ambient/background average mood 계산용입니다. 아직 Gemini batch integration은 없습니다.
- `web_app.py`
  - `send_composed_live_signal(...)`, live OSC test path, live endpoints를 제공합니다.
- `main.py`
  - `get_gemini_client()` / `get_openai_client()`가 lazy client 생성으로 바뀌었습니다.
  - import 시점에 API client를 만들거나 missing API key로 종료하지 않습니다.
  - 테스트에서는 `MEDIA_ART_LOAD_DOTENV=0`으로 dotenv load를 끌 수 있습니다.
- `web/serial_visualizer.html`, `web/serial_visualizer.js`
  - Arduino protocol `v,<valence>,<arousal>`를 브라우저에서 시각화하고 Web Serial로 테스트합니다.
- `e2e_performance.py`
  - labeled WAV 파일을 chunk 단위로 재생해 backend live signal, 가상 Arduino payload, latency/throughput/accuracy summary를 기록합니다.
- `web/arduino_simulator.html`, `web/arduino_simulator.js`
  - 실제 Arduino 없이 `v,<valence>,<arousal>` payload가 LED에 어떻게 반영되는지 확인합니다.

## 설치

backend 폴더로 이동합니다.

```powershell
cd "C:\Users\kksu1\Documents\New project\innerworld\Innerworld\media_art_backend"
```

현재 작업 가상환경은 `venv311`입니다.

```powershell
.\venv311\Scripts\python.exe -m pip install -r requirements.txt
```

`.env` 또는 secret 파일 값은 문서화하지 않습니다. 테스트에서 dotenv load를 막아야 하면 다음 환경 변수를 사용합니다.

```powershell
$env:MEDIA_ART_LOAD_DOTENV="0"
```

## 실행

웹 컨트롤러 서버:

```powershell
.\venv311\Scripts\python.exe web_app.py
```

브라우저에서 엽니다.

```text
http://127.0.0.1:8765/
```

Serial visualizer:

```text
http://127.0.0.1:8765/serial_visualizer.html
```

Arduino simulator:

```text
http://127.0.0.1:8765/arduino_simulator.html
```

Arduino Web Serial tester는 Chromium 계열 브라우저에서 사용합니다.

## E2E 성능 테스트

가장 빠른 smoke test는 synthetic WAV를 생성해서 API 없이 live-only 경로만 측정합니다.

```powershell
.\venv311\Scripts\python.exe e2e_performance.py --synthetic --output-dir logs\e2e --max-samples 3 --web-latest
```

이 명령은 다음을 확인합니다.

- audio chunk 입력부터 `arousal_live` 계산까지 평균 처리 시간
- 첫 의미 있는 arousal response가 나타난 audio timeline 기준 ms
- 가상 Arduino payload `v,<valence>,<arousal>` 생성률
- label 기반 arousal high/low 방향성 정확도

실제 감정 label 정확도를 보려면 labeled speech emotion dataset을 내려받아 실행합니다.

```powershell
.\venv311\Scripts\python.exe e2e_performance.py --dataset-dir "D:\datasets\CREMA-D\AudioWAV" --output-dir logs\e2e --max-samples 20
```

또는 CSV manifest를 사용할 수 있습니다.

```csv
path,label,dataset
AudioWAV/1001_DFA_HAP_XX.wav,happy,crema-d
AudioWAV/1001_DFA_SAD_XX.wav,sad,crema-d
```

```powershell
.\venv311\Scripts\python.exe e2e_performance.py --manifest ".\bench_manifest.csv" --output-dir logs\e2e
```

`--full-ai`를 붙이면 각 WAV를 기존 Whisper/Gemini 분석 경로까지 통과시켜 `emotion_accuracy`를 계산합니다. 이 모드는 API key, 네트워크, 비용, 외부 API latency의 영향을 받습니다.

```powershell
.\venv311\Scripts\python.exe e2e_performance.py --dataset-dir "D:\datasets\CREMA-D\AudioWAV" --max-samples 10 --full-ai
```

무료로 사용할 수 있는 labeled 음성 후보:

- [CREMA-D](https://github.com/CheyneyComputerScience/CREMA-D): 7,442개 emotional multimodal clip, `AudioWAV` 파일명에 emotion code가 들어 있습니다. repo 설명 기준 ODbL/DBCL license입니다.
- [RAVDESS](https://smartlaboratory.org/resources/speech-song-database-ravdess/): speech/song emotion dataset입니다. 비상업 연구용 성격이 강한 CC BY-NC-SA 4.0 license이므로 상업/공개 배포에는 주의합니다.

정확도 해석 caveat:

- 기본 `live-only` 모드는 진짜 감정 분류 모델이 아니라 즉시 반응성 측정입니다. 이때 `emotion_accuracy`는 `unavailable`로 남고, `arousal_direction_accuracy`만 봅니다.
- `LocalSerFallback`은 실제 SER 모델이 아닙니다. 감정 정확도 숫자는 `--full-ai`나 향후 실제 SER adapter가 붙은 뒤에 의미가 커집니다.
- 참여자가 체감하는 실시간성은 `first_response_ms_avg`, `processing_ms_avg`, `payload_rate_hz_avg`, `wall_realtime_ratio_avg`를 함께 봅니다.

## OSC live channels

현재 live path에서 사용하는 TouchDesigner용 OSC channel은 다음입니다.

```text
/emotion/arousal_live
/emotion/arousal_confidence
/emotion/valence_target
/emotion/valence_confidence
```

legacy channel도 함께 남아 있습니다.

```text
/emotion/valence
/emotion/arousal
```

`send_live_osc(...)`는 새 live channel을 보내면서 기존 TouchDesigner 표현식 호환을 위해 live control pair를 legacy channel에도 mirror합니다.

권장 연결 방향:

- `arousal_live`: sound intensity, pitch, waveform 기반 즉시 반응. pattern speed, displacement, blur, brightness 등에 연결합니다.
- `arousal_confidence`: `arousal_live` 영향력의 gain/blend weight로 사용합니다.
- `valence_target`: 느린 색상/분위기 목표값으로 사용합니다.
- `valence_confidence`: valence 반영 강도를 조절합니다.

현재 TouchDesigner runtime에서는 `arousal_confidence`와 `valence_confidence`가 `/project1/oscin2`에 보이고 gain/blend 용도로 권장되지만, 아직 실제 TD gain/blend node에 배선되지는 않았습니다. Task 9 runtime mapping은 `/project1/select2`와 `/project1/select3`의 channel retarget만 적용했습니다.

Gemini는 real-time control 경로가 아닙니다. 느린 valence 보정 또는 future background average mood에 쓰는 방향입니다.

## Arduino protocol

Arduino LED 쪽 protocol은 유지합니다.

```text
v,<valence>,<arousal>
```

여기서 `v`는 command prefix이며 mood parameter가 아닙니다. `arousal`은 빠르게 갱신할 수 있고, `valence`는 neutral/last/target 값을 유지하거나 느리게 갱신하는 방식이 적합합니다.

## TouchDesigner bridge 상태

bridge endpoint:

```text
http://127.0.0.1:9988/td
```

Task 9에서 runtime readback은 성공했습니다.

- bridge `ping`, `inspect`, `channels` 동작 확인
- `/project1/oscin2` readback에서 `emotion/arousal_live`, `emotion/arousal_confidence`, `emotion/valence_target`, `emotion/valence_confidence`와 legacy `emotion/valence`, `emotion/arousal` 확인
- 현재 열려 있던 `C:\Users\kksu1\Downloads\Innerworld\Innerworld\Innerworld.20.toe`에서 runtime parameter 변경 적용
- 이후 사용자가 최신 버전을 `C:\Users\kksu1\Downloads\Innerworld\Innerworld\Innerworld.23.toe`로 저장했고 영구 저장을 확인함

적용된 runtime-only parameter:

```text
/project1/select2.par.channames = emotion/valence_target
/project1/select2.par.renameto = emotion/valence
/project1/select2.par.timeslice = true
/project1/select3.par.channames = emotion/arousal_live
/project1/select3.par.renameto = emotion/arousal
/project1/select3.par.timeslice = true
/project1/math1.par.timeslice = true
```

이 rename은 새 live channel을 입력으로 쓰면서도 기존 `joy`, `sad`, `angry`, `relaxed`, VST bypass 표현식이 기대하던 `emotion/valence`, `emotion/arousal` channel name을 유지하기 위한 호환 레이어입니다. `timeslice`는 live OSC 값을 downstream CHOP/표현식 readback이 따라가게 하기 위한 runtime 설정입니다.

주의: 현재 영구 저장 확인된 파일은 `C:\Users\kksu1\Downloads\Innerworld\Innerworld\Innerworld.23.toe`입니다. canonical planned file인 `C:\Users\kksu1\Documents\New project\innerworld\Innerworld\Innerworld.toe`와는 별도 위치이므로, 다음 작업 시작 시 실제로 어떤 `.toe`를 열었는지 먼저 확인합니다.

## 검증 명령

backend cwd에서 실행합니다.

```powershell
.\venv311\Scripts\python.exe -m unittest discover -s tests -v
.\venv311\Scripts\python.exe -m py_compile main.py web_app.py live_signal.py local_ser.py ambient_emotion.py
node --check web\app.js
node --check web\serial_visualizer.js
node --check web\arduino_simulator.js
```

최신 확인 결과:

```text
.\venv311\Scripts\python.exe -m unittest discover -s tests -v
Ran 41 tests ... OK

.\venv311\Scripts\python.exe -m py_compile main.py web_app.py live_signal.py local_ser.py ambient_emotion.py
OK / no output

node --check web\app.js
OK / no output

node --check web\serial_visualizer.js
OK / no output

node --check web\arduino_simulator.js
OK / no output
```

TouchDesigner가 열려 있고 bridge가 살아 있으면 read-only로 확인합니다.

```powershell
Invoke-RestMethod http://127.0.0.1:9988/td -Method Post -ContentType application/json -Body '{"action":"ping"}'
Invoke-RestMethod http://127.0.0.1:9988/td -Method Post -ContentType application/json -Body '{"action":"channels","path":"/project1/oscin2"}'
Invoke-RestMethod http://127.0.0.1:9988/td -Method Post -ContentType application/json -Body '{"action":"params","path":"/project1/select2"}'
Invoke-RestMethod http://127.0.0.1:9988/td -Method Post -ContentType application/json -Body '{"action":"params","path":"/project1/select3"}'
```

최신 TD readback 결과:

```text
ping ok/pong
/project1/oscin2: emotion/arousal_live, emotion/arousal_confidence, emotion/valence_target, emotion/valence_confidence visible
/project1/select2.par.channames = emotion/valence_target
/project1/select2.par.renameto = emotion/valence
/project1/select2.par.timeslice = true
/project1/select3.par.channames = emotion/arousal_live
/project1/select3.par.renameto = emotion/arousal
/project1/select3.par.timeslice = true
/project1/math1.par.timeslice = true
```

## 남은 작업과 caveat

- `LocalSerFallback`은 아직 실제 SER 모델이 아닙니다. 모델 선택, runtime dependency, confidence calibration은 future work입니다.
- `ambient_emotion.average_mood(items)`는 로컬 평균 계산만 제공합니다. Gemini batch/background integration은 아직 없습니다.
- TouchDesigner 변경은 `C:\Users\kksu1\Downloads\Innerworld\Innerworld\Innerworld.23.toe`에 영구 저장 확인되었습니다. 다만 canonical planned file과 위치가 다르므로 다음 작업에서 기준 `.toe`를 다시 확인해야 합니다.
- `arousal_confidence`, `valence_confidence`는 `/project1/oscin2`에 들어오지만 아직 TD gain/blend node에 배선되지 않았습니다.
- 즉시 LED/TD 반응은 감정 판정보다 `sound intensity`, `pitch`, `waveform`, `arousal_live` 중심으로 시작하는 것이 안전합니다.
- Gemini 기반 감정 refinement는 느린 valence 또는 future ambient layer로 다뤄야 합니다.
