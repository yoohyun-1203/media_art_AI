(function initLedPreviewModel(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.InnerworldLedPreview = api;
})(typeof globalThis !== "undefined" ? globalThis : window, function createLedPreviewModel() {
  const LED_COUNT = 12;
  const ARDUINO_LINE_LENGTHS = Object.freeze([24, 17, 13, 9, 6, 2, 2, 6, 9, 13, 17, 24]);
  const ARDUINO_PIN_LABELS = Object.freeze(ARDUINO_LINE_LENGTHS.map((_length, index) => `D${index + 2}`));
  const ARDUINO_LINE_COUNT = ARDUINO_LINE_LENGTHS.length;
  const ARDUINO_SEND_STEPS = 24;
  const MOOD_MAX_BRIGHTNESS = 96;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function toNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function firstNumber(values, fallback = 0) {
    for (const value of values) {
      if (value === null || value === undefined || value === "") {
        continue;
      }
      const number = Number(value);
      if (Number.isFinite(number)) {
        return number;
      }
    }
    return fallback;
  }

  function fmt(value) {
    return toNumber(value).toFixed(3);
  }

  function mapInt(value, inMin, inMax, outMin, outMax) {
    return Math.trunc(((value - inMin) * (outMax - outMin)) / (inMax - inMin) + outMin);
  }

  function parsePayload(text) {
    const trimmed = String(text || "").trim();
    const parts = trimmed.split(",");
    if (parts.length !== 3 || parts[0] !== "v") {
      throw new Error("payload must be v,<valence>,<arousal>");
    }

    const valence = Number(parts[1]);
    const arousal = Number(parts[2]);
    if (!Number.isFinite(valence) || !Number.isFinite(arousal)) {
      throw new Error("valence and arousal must be numbers");
    }

    const clampedValence = clamp(valence, -1, 1);
    const clampedArousal = clamp(arousal, -1, 1);
    return {
      payload: `v,${fmt(clampedValence)},${fmt(clampedArousal)}`,
      valence: clampedValence,
      arousal: clampedArousal,
    };
  }

  function colorForValence(valence, brightness) {
    const hue = 212 - ((valence + 1) / 2) * 170;
    const saturation = 68 + Math.abs(valence) * 20;
    const lightness = 17 + brightness * 45;
    return `hsl(${hue.toFixed(1)} ${saturation.toFixed(1)}% ${lightness.toFixed(1)}%)`;
  }

  function percentFromSignedFloat(value) {
    const clamped = clamp(toNumber(value), -1, 1);
    return clamp(Math.trunc((clamped + 1) * 50 + 0.5), 0, 100);
  }

  function intensityFromValenceArousal(valence, arousal) {
    const strength = Math.max(
      Math.abs(clamp(toNumber(valence), -1, 1)),
      Math.abs(clamp(toNumber(arousal), -1, 1)),
    );
    const intensity = clamp(Math.trunc(strength * 100 + 0.5), 0, 100);
    return Math.max(30, intensity);
  }

  function parseArduinoInput(input) {
    if (typeof input === "string") {
      return parsePayload(input);
    }

    if (typeof input === "object" && input !== null) {
      const left = firstNumber([
        input.leftArousal,
        input.left_arousal,
        input.left_arousal_live,
      ], null);
      const right = firstNumber([
        input.rightArousal,
        input.right_arousal,
        input.right_arousal_live,
      ], null);
      const sideFallback = left !== null && right !== null
        ? Math.max(left, right)
        : left !== null
          ? left
          : right !== null
            ? right
            : 0;
      const valence = firstNumber([
        input.valence,
        input.valenceTarget,
        input.valence_target,
      ]);
      const arousal = firstNumber([
        input.arousal,
        input.mirrorArousal,
        input.arousalLive,
        input.arousal_live,
      ], sideFallback);
      return parsePayload(`v,${valence},${arousal}`);
    }

    return parsePayload("v,0,0");
  }

  function arduinoMoodFromPayload(input) {
    const parsed = parseArduinoInput(input);
    const pleasantness = percentFromSignedFloat(parsed.valence);
    const energy = percentFromSignedFloat(parsed.arousal);
    const intensity = intensityFromValenceArousal(parsed.valence, parsed.arousal);
    return {
      payload: parsed.payload,
      valence: parsed.valence,
      arousal: parsed.arousal,
      pleasantness,
      energy,
      intensity,
      brightness: moodOutputBrightness({ energy, intensity }),
    };
  }

  function easePercent(amount) {
    return Math.trunc((amount * amount + 50) / 100);
  }

  function forwardDistance(head, step) {
    return head >= step ? head - step : ARDUINO_SEND_STEPS + head - step;
  }

  function triangleWave(phase, span) {
    let value = phase % span;
    const half = span / 2;
    if (value > half) {
      value = span - value;
    }
    return Math.trunc((value * 100) / half);
  }

  function tailActivity(distance, coreLength, glowLength) {
    if (distance < coreLength) {
      return 100 - Math.trunc((distance * 18) / coreLength);
    }

    if (distance >= glowLength) {
      return 0;
    }

    const tailLength = glowLength - coreLength;
    const tailDistance = distance - coreLength;
    return Math.trunc(((tailLength - tailDistance) * 82) / tailLength);
  }

  function moodTrailLength(mood) {
    let trail = mapInt(mood.intensity, 0, 100, 3, 8);
    if (mood.energy < 45) {
      trail += 3;
    }
    if (mood.energy > 65 && mood.pleasantness < 50 && trail > 3) {
      trail -= 1;
    }
    return Math.min(trail, ARDUINO_SEND_STEPS);
  }

  function moodAfterglowLength(mood) {
    let glow = moodTrailLength(mood) + mapInt(mood.intensity, 0, 100, 5, 12);
    if (mood.energy < 45) {
      glow += 4;
    }
    if (mood.pleasantness >= 55) {
      glow += 2;
    }
    return Math.min(glow, ARDUINO_SEND_STEPS);
  }

  function moodOutputBrightness(mood) {
    let brightness = mapInt(mood.intensity, 0, 100, 14, MOOD_MAX_BRIGHTNESS);
    if (mood.energy < 35) {
      brightness = Math.trunc((brightness * 80) / 100);
    }
    return Math.max(brightness, 10);
  }

  function directedLineStep(line) {
    return Math.trunc((line * ARDUINO_SEND_STEPS) / ARDUINO_LINE_COUNT);
  }

  function moodLineActivity(line, frame, mood) {
    const head = frame % ARDUINO_SEND_STEPS;
    const step = directedLineStep(line);
    const core = moodTrailLength(mood);
    const glow = moodAfterglowLength(mood);
    const distance = forwardDistance(head, step);
    let amount = tailActivity(distance, core, glow);

    if (mood.energy >= 55) {
      const secondHead = (head + ARDUINO_SEND_STEPS / 2) % ARDUINO_SEND_STEPS;
      const secondDistance = forwardDistance(secondHead, step);
      amount = Math.max(amount, Math.trunc((tailActivity(secondDistance, core, glow) * 82) / 100));
    }

    if (mood.energy < 55 && mood.pleasantness >= 50) {
      const breath = triangleWave(frame + line * 3, 32);
      amount = Math.max(amount, 18 + Math.trunc((breath * 28) / 100));
    }

    if (mood.energy < 50 && mood.pleasantness < 50) {
      amount = Math.trunc((amount * mapInt(mood.energy, 0, 50, 35, 75)) / 100);
      if (((frame + line * 5) % 19) === 0) {
        amount = Math.max(amount, 35);
      }
    }

    if (mood.energy >= 65 && mood.pleasantness < 50 && ((frame + line * 3) % 7) === 0) {
      amount = Math.max(amount, 100);
    }

    const ambient = mapInt(mood.intensity, 0, 100, 3, 14);
    return Math.max(amount, ambient);
  }

  function sideEnergy(arousal) {
    return (clamp(toNumber(arousal), -1, 1) + 1) / 2;
  }

  function buildArduinoHardwareModel(input, options = {}) {
    const parsed = parseArduinoInput(input);
    const mood = arduinoMoodFromPayload(parsed);
    const frame = Math.max(0, Math.trunc(toNumber(options.frame))) % ARDUINO_SEND_STEPS;
    const leftArousal = clamp(firstNumber([input?.leftArousal, input?.left_arousal, input?.left_arousal_live], parsed.arousal), -1, 1);
    const rightArousal = clamp(firstNumber([input?.rightArousal, input?.right_arousal, input?.right_arousal_live], parsed.arousal), -1, 1);
    const leftEnergy = sideEnergy(leftArousal);
    const rightEnergy = sideEnergy(rightArousal);
    const lineMidpoint = (ARDUINO_LINE_COUNT - 1) / 2;
    const pixelCore = mood.energy >= 60 ? 4 : 6;
    const pixelGlow = Math.min(pixelCore + mapInt(mood.intensity, 0, 100, 7, 15), ARDUINO_SEND_STEPS);
    const brightnessScale = mood.brightness / MOOD_MAX_BRIGHTNESS;

    const lines = ARDUINO_LINE_LENGTHS.map((physicalLength, line) => {
      const side = line <= lineMidpoint ? "left" : "right";
      const sourceEnergy = side === "left" ? leftEnergy : rightEnergy;
      const sourceScalar = 0.82 + sourceEnergy * 0.28;
      const activity = clamp(Math.round(moodLineActivity(line, frame, mood) * sourceScalar), 0, 100);
      const pixelHead = (frame + line * 2) % ARDUINO_SEND_STEPS;
      const dots = [];

      for (let dotIndex = 0; dotIndex < physicalLength; dotIndex += 1) {
        const distance = forwardDistance(pixelHead, dotIndex);
        const pixelTail = tailActivity(distance, pixelCore, pixelGlow);
        let local = Math.max(
          Math.trunc((activity * pixelTail) / 100),
          Math.trunc((activity * 42) / 100),
        );

        if (mood.energy >= 60) {
          const sparkle = (frame * 11 + line * 17 + dotIndex * 23) % 100;
          const chance = mapInt(mood.energy, 60, 100, 4, 18);
          if (sparkle < chance) {
            local = Math.max(local, 100);
          }
        } else {
          const wave = triangleWave(frame + dotIndex * 2 + line * 3, 40);
          local = Math.trunc((local * (70 + Math.trunc((wave * 30) / 100))) / 100);
        }

        local = Math.trunc((local * mapInt(mood.intensity, 0, 100, 25, 100)) / 100);
        local = easePercent(local);

        const brightness = clamp((local / 100) * brightnessScale, 0, 1);
        const color = colorForValence(parsed.valence, brightness);
        dots.push({
          index: dotIndex,
          active: local > 6,
          activity: local,
          color,
          opacity: 0.24 + brightness * 0.76,
          glow: local > 6 ? Math.round(3 + brightness * 18) : 0,
          side,
        });
      }

      return {
        index: line,
        pin: ARDUINO_PIN_LABELS[line],
        physicalLength,
        sentSteps: ARDUINO_SEND_STEPS,
        activity,
        side,
        dots,
      };
    });

    return {
      lineCount: ARDUINO_LINE_COUNT,
      sentSteps: ARDUINO_SEND_STEPS,
      lineLengths: [...ARDUINO_LINE_LENGTHS],
      pins: [...ARDUINO_PIN_LABELS],
      frame,
      payload: parsed.payload,
      mood,
      leftArousal,
      rightArousal,
      lines,
      summary: `D2-D13 ${ARDUINO_LINE_COUNT} lines / sent ${ARDUINO_SEND_STEPS} LEDs / visible ${ARDUINO_LINE_LENGTHS.join("/")}`,
      valueSummary: `pleasantness ${mood.pleasantness}% / energy ${mood.energy}% / intensity ${mood.intensity}%`,
    };
  }

  function arousalToCount(arousal) {
    const energy = (clamp(toNumber(arousal), -1, 1) + 1) / 2;
    return Math.max(1, Math.round(1 + energy * (LED_COUNT - 1)));
  }

  function arousalToBrightness(arousal) {
    const energy = (clamp(toNumber(arousal), -1, 1) + 1) / 2;
    return 0.2 + energy * 0.8;
  }

  function normalizeArousalInput(arousalInput) {
    if (typeof arousalInput === "object" && arousalInput !== null) {
      const fallback = firstNumber([
        arousalInput.arousal,
        arousalInput.arousalLive,
        arousalInput.arousal_live,
      ]);
      return {
        left: firstNumber([
          arousalInput.leftArousal,
          arousalInput.left_arousal,
          arousalInput.left_arousal_live,
        ], fallback),
        right: firstNumber([
          arousalInput.rightArousal,
          arousalInput.right_arousal,
          arousalInput.right_arousal_live,
        ], fallback),
      };
    }

    const fallback = toNumber(arousalInput);
    return { left: fallback, right: fallback };
  }

  function buildLedModel(valenceInput, arousalInput) {
    const valence = clamp(toNumber(valenceInput), -1, 1);
    const sides = normalizeArousalInput(arousalInput);
    const leftArousal = clamp(sides.left, -1, 1);
    const rightArousal = clamp(sides.right, -1, 1);
    const leftActiveCount = arousalToCount(leftArousal);
    const rightActiveCount = arousalToCount(rightArousal);
    const leftBrightness = arousalToBrightness(leftArousal);
    const rightBrightness = arousalToBrightness(rightArousal);

    const leds = [];
    for (let index = 0; index < LED_COUNT; index += 1) {
      const leftActive = index < leftActiveCount;
      const rightActive = LED_COUNT - 1 - index < rightActiveCount;
      const active = leftActive || rightActive;
      const side = leftActive && rightActive ? "both" : leftActive ? "left" : rightActive ? "right" : "off";
      const sourceBrightness = Math.max(
        leftActive ? leftBrightness : 0,
        rightActive ? rightBrightness : 0,
      );
      const localBrightness = sourceBrightness * (0.8 + (index % 2) * 0.08);
      const color = colorForValence(valence, localBrightness);
      leds.push({
        index,
        active,
        leftActive,
        rightActive,
        side,
        color,
        opacity: active ? 1 : 0.3,
        glow: active ? Math.round(7 + localBrightness * 18) : 0,
      });
    }

    const leftBrightnessPercent = Math.round(leftBrightness * 100);
    const rightBrightnessPercent = Math.round(rightBrightness * 100);
    return {
      valence,
      arousal: (leftArousal + rightArousal) / 2,
      leftArousal,
      rightArousal,
      leftActiveCount,
      rightActiveCount,
      leftBrightness,
      rightBrightness,
      leftBrightnessPercent,
      rightBrightnessPercent,
      leds,
      summary: `L ${leftActiveCount}/${LED_COUNT} · R ${rightActiveCount}/${LED_COUNT} · brightness ${leftBrightnessPercent}%/${rightBrightnessPercent}%`,
      valueSummary: `valence ${fmt(valence)} / left ${fmt(leftArousal)} / right ${fmt(rightArousal)}`,
    };
  }

  function payloadFromLiveState(state) {
    const result = state?.result || {};
    const latest = state?.latest || {};
    const liveOsc = result.live_osc || {};
    const commonArousal = firstNumber([
      latest.arousal_live,
      liveOsc.arousal_live,
      result.td_arousal,
      result.audio_arousal,
    ]);
    const valence = firstNumber([
      latest.valence_target,
      latest.valence,
      liveOsc.valence_target,
      result.td_valence,
      result.valence,
    ]);
    const leftArousal = firstNumber([
      latest.left_arousal_live,
      latest.leftArousal,
      latest.left_arousal,
      latest.arousal_left,
      latest.mic_left_arousal,
      liveOsc.left_arousal_live,
      liveOsc.leftArousal,
      liveOsc.left_arousal,
    ], commonArousal);
    const rightArousal = firstNumber([
      latest.right_arousal_live,
      latest.rightArousal,
      latest.right_arousal,
      latest.arousal_right,
      latest.mic_right_arousal,
      liveOsc.right_arousal_live,
      liveOsc.rightArousal,
      liveOsc.right_arousal,
    ], commonArousal);
    const averageArousal = (leftArousal + rightArousal) / 2;
    const mirrorArousal = Math.max(leftArousal, rightArousal);
    const parsed = parsePayload(`v,${valence},${mirrorArousal}`);

    return {
      ...parsed,
      averageArousal,
      mirrorArousal,
      leftArousal,
      rightArousal,
      leftPayload: parsePayload(`v,${valence},${leftArousal}`).payload,
      rightPayload: parsePayload(`v,${valence},${rightArousal}`).payload,
      arduinoMood: arduinoMoodFromPayload(parsed),
      arousalConfidence: firstNumber([
        latest.left_arousal_confidence,
        latest.arousal_confidence,
        liveOsc.arousal_confidence,
      ], null),
      rightArousalConfidence: firstNumber([
        latest.right_arousal_confidence,
        latest.arousal_confidence,
        liveOsc.arousal_confidence,
      ], null),
      valenceConfidence: firstNumber([
        latest.valence_confidence,
        liveOsc.valence_confidence,
        result.valence_confidence,
      ], null),
    };
  }

  return {
    ARDUINO_LINE_COUNT,
    ARDUINO_LINE_LENGTHS,
    ARDUINO_PIN_LABELS,
    ARDUINO_SEND_STEPS,
    LED_COUNT,
    arduinoMoodFromPayload,
    buildArduinoHardwareModel,
    buildLedModel,
    colorForValence,
    fmt,
    intensityFromValenceArousal,
    parsePayload,
    payloadFromLiveState,
    percentFromSignedFloat,
  };
});
