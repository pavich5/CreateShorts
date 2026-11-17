# Real OpenShorts E2E checks

These tests use the actual local app, FFmpeg exports, and signed-in AI service.
They create test projects/categories and saved versions in Documents/Clips.
They do not publish to social accounts or permanently delete existing videos.

Start the existing local installation at http://localhost:5175 and install dashboard dependencies with `npm ci`.
Google Chrome is used by default; set `PLAYWRIGHT_CHROME_PATH` for another Chrome/Chromium executable.

```sh
npm run test:unit
OPENSHORTS_E2E_RUN=navigation npm run test:e2e -- e2e/navigation.spec.mjs
OPENSHORTS_REAL_E2E=1 OPENSHORTS_E2E_RUN=generation npm run test:e2e -- e2e/generation.spec.mjs
docker exec openshorts-local-backend-1 python dashboard/e2e/prepare_media.py artifacts/e2e-2026-11-13
OPENSHORTS_REAL_E2E=1 OPENSHORTS_E2E_RUN=editor npm run test:e2e -- e2e/editor.spec.mjs
```

The default mandatory source is `~/Downloads/Alex Hormozi The 4 Steps To Your First Dollars Tomorrow.mp4`.
Override it with `OPENSHORTS_REAL_TEST_VIDEO` only when intentionally running a different suite.
All generated outputs and source-derived media test assets use 9:16.
Each runner has a separate output directory so concurrent runners cannot delete each other's traces.
Evidence is saved under `artifacts/e2e-2026-11-13/` at the repository root.

Generation creates `job.json`; editor tests reuse that job, or `OPENSHORTS_E2E_JOB` when explicitly set.
Editor stress tests change that project. They start with a short real excerpt to make ten full-resolution renders practical.
The media tests use `source-overlay.mp4`, `source-image.jpg`, and `source-audio.wav` extracted from the required source into the evidence directory.
The generation label/category/count/min/max environment variables allow additional runs without replacing the primary manifest.

After the browser exports, verify the actual media inside the backend container:

```sh
docker exec openshorts-local-backend-1 python dashboard/e2e/verify_exports.py artifacts/e2e-2026-11-13
```

The verifier probes duration, 9:16 geometry, H.264/audio streams, decodes every frame, scans black/freeze events, and hashes saved exports against the server previews.
Freeze detections require comparison with source footage; still source scenes are not automatically rendering failures.
Waveform/timestamp checks establish alignment, not a full human audit of speech-recognition wording.

Backend regressions can run without modifying the installed app environment:

```sh
docker exec openshorts-local-backend-1 python -m pip install --target /tmp/openshorts-test-deps pytest
docker exec -e PYTHONPATH=/tmp/openshorts-test-deps:/app openshorts-local-backend-1 python -m pytest tests
```

`test_real_video_trim_stress.py` and `test_ai_planner_integration.py` are opt-in real-source/integration checks; their docstrings document required environment variables.
Mocked endpoint regressions are separate from the real-source, FFmpeg, and browser checks.

`final-journey.spec.mjs` exercises the combined workflow against the selected generation label/category.
`repair-join.spec.mjs` uses the recorded `final-frozen` project and performs real trim/AI mutations; its repeated-trim case currently reproduces the open duration-validation error documented in the stabilization report.
`OPENSHORTS_VERIFY_REPAIRED_JOIN=1` makes that test verify/reload an already applied repair without requesting another edit. This mode is verification evidence, not a new repair execution.
