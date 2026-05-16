const assert = require("node:assert/strict");

const {
  ARDUINO_LINE_COUNT,
  ARDUINO_LINE_LENGTHS,
  ARDUINO_SEND_STEPS,
  arduinoMoodFromPayload,
  buildArduinoHardwareModel,
  buildLedModel,
  parsePayload,
  payloadFromLiveState,
} = require("../web/led_preview_model.js");

function testParsePayloadClampsAndFormats() {
  const parsed = parsePayload("v,2,-2");

  assert.equal(parsed.payload, "v,1.000,-1.000");
  assert.equal(parsed.valence, 1);
  assert.equal(parsed.arousal, -1);
}

function testBuildLedModelMapsBidirectionalArousalToLinearLeds() {
  const model = buildLedModel(0.823, {
    leftArousal: 0.7,
    rightArousal: -0.7,
  });

  assert.equal(model.leftActiveCount, 10);
  assert.equal(model.rightActiveCount, 3);
  assert.equal(model.leftBrightnessPercent, 88);
  assert.equal(model.rightBrightnessPercent, 32);
  assert.equal(model.summary, "L 10/12 · R 3/12 · brightness 88%/32%");
  assert.equal(model.valueSummary, "valence 0.823 / left 0.700 / right -0.700");
  assert.equal(model.leds.length, 12);
  assert.equal(model.leds[0].index, 0);
  assert.equal(model.leds[0].active, true);
  assert.equal(model.leds[0].side, "left");
  assert.equal(model.leds[11].active, true);
  assert.equal(model.leds[11].side, "right");
}

function testPayloadFromLiveStateUsesFastArousalAndLatestValence() {
  const parsed = payloadFromLiveState({
    latest: {
      left_arousal_live: 0.6,
      right_arousal_live: -0.2,
      valence_target: -0.25,
      left_arousal_confidence: 0.8,
      right_arousal_confidence: 0.5,
    },
    result: {
      valence_confidence: 0.7,
    },
  });

  assert.equal(parsed.payload, "v,-0.250,0.600");
  assert.equal(parsed.leftPayload, "v,-0.250,0.600");
  assert.equal(parsed.rightPayload, "v,-0.250,-0.200");
  assert.equal(parsed.valence, -0.25);
  assert.equal(parsed.arousal, 0.6);
  assert.ok(Math.abs(parsed.averageArousal - 0.2) < 0.000001);
  assert.equal(parsed.mirrorArousal, 0.6);
  assert.equal(parsed.leftArousal, 0.6);
  assert.equal(parsed.rightArousal, -0.2);
  assert.equal(parsed.arousalConfidence, 0.8);
  assert.equal(parsed.rightArousalConfidence, 0.5);
  assert.equal(parsed.valenceConfidence, 0.7);
}

function testPayloadFromLiveStateFallsBackToCommonArousalForBothSides() {
  const parsed = payloadFromLiveState({
    latest: {
      arousal_live: 0.4,
      valence_target: 0.2,
    },
  });

  assert.equal(parsed.payload, "v,0.200,0.400");
  assert.equal(parsed.leftPayload, "v,0.200,0.400");
  assert.equal(parsed.rightPayload, "v,0.200,0.400");
  assert.equal(parsed.leftArousal, 0.4);
  assert.equal(parsed.rightArousal, 0.4);
}

function testArduinoMoodTransformMatchesSketchMath() {
  const mood = arduinoMoodFromPayload("v,-0.25,0.6");

  assert.equal(mood.payload, "v,-0.250,0.600");
  assert.equal(mood.pleasantness, 38);
  assert.equal(mood.energy, 80);
  assert.equal(mood.intensity, 60);

  const quiet = arduinoMoodFromPayload("v,0.1,-0.1");
  assert.equal(quiet.pleasantness, 55);
  assert.equal(quiet.energy, 45);
  assert.equal(quiet.intensity, 30);
}

function testArduinoHardwareModelUsesUnoLineGeometry() {
  const model = buildArduinoHardwareModel({
    valence: 0.25,
    arousal: 0.7,
    leftArousal: 0.7,
    rightArousal: -0.4,
  }, { frame: 7 });

  assert.equal(ARDUINO_LINE_COUNT, 12);
  assert.equal(ARDUINO_SEND_STEPS, 24);
  assert.deepEqual(ARDUINO_LINE_LENGTHS, [24, 17, 13, 9, 6, 2, 2, 6, 9, 13, 17, 24]);
  assert.equal(model.lines.length, 12);
  assert.equal(model.lines[0].pin, "D2");
  assert.equal(model.lines[0].physicalLength, 24);
  assert.equal(model.lines[0].sentSteps, 24);
  assert.equal(model.lines[0].dots.length, 24);
  assert.equal(model.lines[5].pin, "D7");
  assert.equal(model.lines[5].physicalLength, 2);
  assert.equal(model.lines[5].dots.length, 2);
  assert.equal(model.lines[11].pin, "D13");
  assert.equal(model.lines[11].physicalLength, 24);
  assert.equal(model.lines[11].dots.length, 24);
  assert.equal(model.mood.pleasantness, 63);
  assert.equal(model.mood.energy, 85);
  assert.equal(model.mood.intensity, 70);
}

testParsePayloadClampsAndFormats();
testBuildLedModelMapsBidirectionalArousalToLinearLeds();
testPayloadFromLiveStateUsesFastArousalAndLatestValence();
testPayloadFromLiveStateFallsBackToCommonArousalForBothSides();
testArduinoMoodTransformMatchesSketchMath();
testArduinoHardwareModelUsesUnoLineGeometry();

console.log("test_led_preview_model.js passed");
