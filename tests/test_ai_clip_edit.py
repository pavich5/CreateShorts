import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import ai_clip_edit as ai


def plan(kind='steady_camera', start=3, end=5, **kwargs):
    return {'operations': [{'type': kind, 'start': start, 'end': end, **kwargs}]}


def test_maps_clip_times_through_reordered_cuts():
    segments = [{'start': 30, 'end': 34}, {'start': 10, 'end': 14}]
    assert ai.source_windows(segments, 3, 5) == [(3, 4.0, 33.0), (4.0, 5, 10.0)]
    with pytest.raises(ValueError, match='timing'):
        ai.source_windows(segments, 6, 9)


def test_selected_range_limits_a_broader_ai_edit():
    edit = ai.EditPlan.model_validate(plan('brighten', 0, 6))
    scoped = ai.constrain_plan(edit, 3, 5, 6)
    assert [(op.start, op.end) for op in scoped.operations] == [(3, 5)]


@pytest.mark.parametrize('start,end', [(None, 5), (3, None), (5, 3), (0, 7), (3, 3.01)])
def test_invalid_selections_cannot_render(start, end):
    with pytest.raises(ValueError, match='range'):
        ai.constrain_plan(ai.EditPlan.model_validate(plan('brighten', 0, 6)), start, end, 6)


def test_ai_cannot_edit_a_different_range_than_the_selection():
    with pytest.raises(ValueError, match='missed'):
        ai.constrain_plan(ai.EditPlan.model_validate(plan('mute', 0, 1)), 3, 5, 6)


def test_ui_range_and_timestamp_helpers():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node not installed')
    module = Path(__file__).resolve().parents[1] / 'dashboard/src/lib/aiEditControls.js'
    script = """
      import assert from 'node:assert/strict';
      const {editPlaybackWindow, formatTimestamp, validEditRange, validTrimRange, scopedInstructions} = await import(process.argv[1]);
      assert.deepEqual(editPlaybackWindow(2,4), {start:2.1,end:3.9});
      const short = editPlaybackWindow(2,2.15);
      assert.ok(short.start > 2 && short.end < 2.15 && short.start < short.end);
      assert.equal(editPlaybackWindow(4,2), null);
      assert.equal(formatTimestamp(3.5), '0:03.5');
      assert.equal(formatTimestamp(60.1), '1:00.1');
      assert.equal(formatTimestamp(null), '--:--');
      assert.equal(validEditRange(3, 5, 6), true);
      for (const range of [[5,3,6], [3,7,6], ['',5,6], [3,3.01,6]]) {
        assert.equal(validEditRange(...range), false);
      }
      assert.equal(validTrimRange(1, 5, 6), true);
      assert.equal(validTrimRange(0, 5, 6), true);
      assert.equal(validTrimRange(1, 6, 6), true);
      for (const range of [[0,6,6], ['',5,6], [2,2.01,6], [0,7,6]]) {
        assert.equal(validTrimRange(...range), false);
      }
      const scoped = scopedInstructions('Keep the camera steady', {start:3,end:5});
      assert.ok(scoped.includes('ONLY from 3.000 to 5.000 seconds'));
      assert.ok(scoped.includes('outside this selected range unchanged'));
      assert.ok(scopedInstructions('a'.repeat(1700), {start:3,end:5}).length <= 2000);
      assert.equal(scopedInstructions('  Brighten it  ', null), 'Brighten it');
    """
    subprocess.run([node, '--input-type=module', '-e', script, module.as_uri()],
                   capture_output=True, check=True, timeout=30)


@pytest.mark.parametrize('raw', [
    plan(start=7, end=8), plan(start=4, end=3), plan(start=float('nan')),
    plan(end=float('inf')), plan(center_x=8), plan(kind='run_shell'),
])
def test_rejects_invalid_or_non_executable_plans(raw):
    with pytest.raises(ValueError):
        ai.validate_plan(raw, 6, True)


def test_unsupported_request_cannot_apply_a_partial_substitute():
    with pytest.raises(ValueError, match='remove objects'):
        ai.validate_plan({**plan('brighten'), 'unsupported': ['Cannot remove objects.']}, 6, True)
    with pytest.raises(ValueError, match='original source'):
        ai.validate_plan(plan(), 6, False)
    with pytest.raises(ValueError, match='text'):
        ai.validate_plan(plan('zoom_in'), 6, True, has_burns=True)


def test_overlapping_camera_requests_are_rejected():
    with pytest.raises(ValueError, match='overlap'):
        ai.validate_plan({'operations': plan()['operations'] + plan('stabilize', 4, 6)['operations']}, 6, True)


def test_text_instructions_use_the_configured_local_connection(monkeypatch):
    seen = []
    monkeypatch.setattr(ai.llm_backend, 'active', lambda: True)
    def generate(prompt, schema):
        seen.append(prompt)
        assert schema is ai.EditPlan
        return plan(), None
    monkeypatch.setattr(ai.llm_backend, 'generate_json', generate)
    monkeypatch.setenv('LLM_MODEL', 'test-generic-local-model')
    result = ai.plan_edit('Camera moving too much between 3-5 seconds.',
                          {'duration': 6}, source_available=True)
    assert result.operations[0].type == 'steady_camera'
    assert '3-5 seconds' in seen[0]
    assert 'THIS SHORT' in seen[0]


@pytest.fixture
def media(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg not installed')
    source, clip = tmp_path / 'source.mp4', tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=red:s=320x288:r=12:d=6,drawbox=x=160:y=0:w=160:h=288:color=blue:t=fill',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)],
        check=True, capture_output=True, timeout=30)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-vf',
        "crop=160:288:x='(iw-ow)*t/6':y=0", '-c:v', 'libx264', '-c:a', 'copy', str(clip)],
        check=True, capture_output=True, timeout=30)
    return source, clip, tmp_path


def pixels(path, time):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(time),
        '-i', str(path), '-frames:v', '1', '-vf', 'scale=16:28', '-pix_fmt', 'rgb24',
        '-f', 'rawvideo', '-'], timeout=30)


def average_difference(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def audio(path):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
        '-map', '0:a:0', '-c', 'copy', '-f', 'adts', '-'], timeout=30)


def test_fixed_camera_changes_only_requested_window_and_keeps_audio(media):
    source, clip, root = media
    output = root / 'fixed.mp4'
    info = ai.probe(clip)
    edit = ai.validate_plan(plan(center_x=0), info['duration'], True)
    ai.render_edit(clip, output, edit, info, source_path=source,
                   segments=[{'start': 0, 'end': 6}])
    assert average_difference(pixels(clip, 1), pixels(output, 1)) < 3
    assert average_difference(pixels(clip, 5.5), pixels(output, 5.5)) < 3
    assert average_difference(pixels(clip, 4), pixels(output, 4)) > 20
    assert average_difference(pixels(output, 3.5), pixels(output, 4.5)) < 3
    assert audio(clip) == audio(output)
    assert ai.probe(output)['duration'] == pytest.approx(info['duration'], abs=0.1)


def test_repeating_a_fixed_crop_reports_no_visible_change(media):
    source, clip, root = media
    first, second = root / 'fixed.mp4', root / 'fixed-again.mp4'
    info = ai.probe(clip)
    edit = ai.validate_plan(plan(center_x=0), info['duration'], True)
    assert ai.render_edit(clip, first, edit, info, source_path=source,
                          segments=[{'start': 0, 'end': 6}]) is True
    assert ai.render_edit(first, second, edit, info, source_path=source,
                          segments=[{'start': 0, 'end': 6}]) is False


def test_speaker_framing_ignores_inset_faces_and_follows_panel_cuts(tmp_path, monkeypatch):
    cv2 = pytest.importorskip('cv2')
    np = pytest.importorskip('numpy')
    class Capture:
        index = 0
        def set(self, *args): pass
        def read(self):
            if self.index >= 30: return False, None
            self.index += 1
            return True, np.zeros((360,640,3), dtype=np.uint8)
        def release(self): pass
    class Detector:
        index = 0
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def process(self, frame):
            center = 800 if self.index < 15 else 1200
            self.index += 1
            def detection(x,w,h):
                return SimpleNamespace(location_data=SimpleNamespace(relative_bounding_box=
                    SimpleNamespace(xmin=x-w/2,width=w,height=h)))
            return SimpleNamespace(detections=[detection(center/1920,.12,.3),
                detection(.1 if self.index%2 else .9,.025,.06)])
    monkeypatch.setattr(cv2, 'VideoCapture', lambda _path: Capture())
    monkeypatch.setitem(sys.modules, 'mediapipe', SimpleNamespace(solutions=SimpleNamespace(
        face_detection=SimpleNamespace(FaceDetection=lambda **kwargs: Detector()))))
    commands = tmp_path / 'camera.cmd'
    first = ai.speaker_crop_commands('source.mp4',0,1,{'width':1920,'fps':30},608,commands,'main')
    assert first == 496
    lines = commands.read_text().splitlines()
    assert lines == ['0.000000 crop@main x 496;', '0.500000 crop@main x 896;']


@pytest.mark.parametrize('kind', ['stabilize', 'brighten', 'mute'])
def test_renders_time_bounded_edits(media, kind):
    _, clip, root = media
    output = root / f'{kind}.mp4'
    info = ai.probe(clip)
    ai.render_edit(clip, output, ai.validate_plan(plan(kind), info['duration'], False), info)
    assert ai.probe(output)['duration'] == pytest.approx(info['duration'], abs=0.1)
    if kind == 'brighten':
        assert average_difference(pixels(clip, 1), pixels(output, 1)) < 3
        assert average_difference(pixels(clip, 4), pixels(output, 4)) > 5


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    app = pytest.importorskip('app')
    directory = tmp_path / 'job1'
    directory.mkdir()
    (directory / 'clip.mp4').write_bytes(b'original')
    clip = {'video_url': '/videos/job1/clip.mp4', 'start': 10, 'end': 16}
    meta = directory / 'video_metadata.json'
    meta.write_text(json.dumps({'shorts': [clip]}))
    monkeypatch.setattr(app, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(app, 'jobs', {'job1': {'status': 'completed', 'result': {'clips': [clip]}}})
    monkeypatch.setattr(app, 'BILLING_ENABLED', False)
    monkeypatch.setattr(app.llm_backend, 'active', lambda: True)
    monkeypatch.setattr(app, '_ensure_job_files', AsyncMock())
    monkeypatch.setattr(app, '_locate_source', lambda _jid: None)
    monkeypatch.setattr(app, '_archive_clip_edit_bg', lambda *args: None)
    monkeypatch.setattr(ai, 'probe', lambda _path: {'width': 160, 'height': 288, 'fps': 12, 'duration': 6})
    monkeypatch.setattr(ai, 'plan_edit', lambda *args, **kwargs: ai.EditPlan.model_validate(plan('brighten')))
    def render(_input, output, *args, **kwargs):
        Path(output).write_bytes(b'edited')
    monkeypatch.setattr(ai, 'render_edit', render)
    return app, directory, clip, meta


def test_endpoint_persists_new_version_without_overwriting_original(endpoint):
    app, directory, clip, meta = endpoint
    result = asyncio.run(app.edit_clip_with_ai(app.AIEditRequest(
        job_id='job1', instructions='Brighten from 3-5 seconds'), None))
    assert result['success']
    assert result['changes'] == ['Brightened · 3–5s']
    assert (directory / 'clip.mp4').read_bytes() == b'original'
    assert clip['video_url'] == result['new_video_url']
    assert json.loads(meta.read_text())['shorts'][0]['video_url'] == result['new_video_url']
    assert (result['preview_start'], result['preview_end']) == (3, 5)
    assert json.loads(meta.read_text())['shorts'][0]['ai_edit']['previous_video_url'] == '/videos/job1/clip.mp4'


def test_unchanged_crop_falls_back_to_motion_stabilization(endpoint, monkeypatch):
    app, directory, clip, meta = endpoint
    calls = []
    monkeypatch.setattr(ai, 'plan_edit', lambda *args, **kwargs: ai.EditPlan.model_validate(plan('steady_camera')))
    def render(_input, output, *args, **kwargs):
        calls.append([op.type for op in args[0].operations])
        Path(output).write_bytes(b'unchanged')
        return len(calls) > 1
    monkeypatch.setattr(ai, 'render_edit', render)
    result = asyncio.run(app.edit_clip_with_ai(app.AIEditRequest(
        job_id='job1', instructions='Keep the camera steady'), None))
    assert calls == [['steady_camera'], ['stabilize']]
    assert result['changes'] == ['Reduced camera shake · 3–5s']
    assert clip['video_url'] == result['new_video_url']
    assert json.loads(meta.read_text())['shorts'][0]['ai_edit']['operations'][0]['type'] == 'stabilize'


def test_stabilization_reduces_measured_jitter_not_just_pixel_difference(tmp_path):
    cv2 = pytest.importorskip('cv2')
    np = pytest.importorskip('numpy')
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg not installed')
    path = tmp_path / 'shaky.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 24, (240, 320))
    image = np.random.default_rng(3).integers(0, 256, (320, 240, 3), dtype=np.uint8)
    image = cv2.GaussianBlur(image, (5, 5), 0)
    shifts = [0, 7, -6, 4, -7, 6, -3, 0]
    for i in range(96):
        transform = np.float32([[1, 0, shifts[i % len(shifts)]], [0, 1, shifts[(i + 3) % len(shifts)] / 2]])
        writer.write(cv2.warpAffine(image, transform, (240, 320), borderMode=cv2.BORDER_REFLECT))
    writer.release()
    info = ai.probe(path)
    result = tmp_path / 'stable.mp4'
    ai.render_edit(path, result, ai.validate_plan(plan('stabilize', 0, 4, strength=.75), 4, False), info)
    def jitter(video):
        cap = cv2.VideoCapture(str(video))
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok: break
            frames.append(np.float32(cv2.cvtColor(frame[35:-35, 35:-35], cv2.COLOR_BGR2GRAY)))
        cap.release()
        positions = [cv2.phaseCorrelate(frames[12], frame)[0] for frame in frames[12:-12]]
        return float(np.median(np.linalg.norm(np.diff(positions, axis=0), axis=1)))
    before, after = jitter(path), jitter(result)
    assert before > 5
    assert after < before * .5, (before, after)


def test_failed_edit_preserves_current_clip_and_metadata(endpoint, monkeypatch):
    app, _, clip, meta = endpoint
    before = meta.read_bytes()
    def reject(*args, **kwargs):
        raise ValueError('Cannot remove objects.')
    monkeypatch.setattr(ai, 'plan_edit', reject)
    with pytest.raises(app.HTTPException) as error:
        asyncio.run(app.edit_clip_with_ai(app.AIEditRequest(
            job_id='job1', instructions='Remove the chair'), None))
    assert error.value.status_code == 400
    assert clip['video_url'] == '/videos/job1/clip.mp4'
    assert meta.read_bytes() == before


def test_endpoint_enforces_ownership_before_editing(endpoint, monkeypatch):
    app, _, _, _ = endpoint
    guard = AsyncMock(side_effect=app.HTTPException(status_code=404, detail='Not found'))
    monkeypatch.setattr(app, '_assert_job_owner', guard)
    with pytest.raises(app.HTTPException) as error:
        asyncio.run(app.edit_clip_with_ai(app.AIEditRequest(job_id='job1', instructions='Brighten'), None))
    assert error.value.status_code == 404
    guard.assert_awaited_once()


def test_negative_clip_index_is_rejected():
    app = pytest.importorskip('app')
    with pytest.raises(ValueError):
        app.AIEditRequest(job_id='job1', clip_index=-1, instructions='Brighten')


def test_captions_are_restored_after_editing_the_clean_picture(endpoint, monkeypatch):
    app, directory, clip, _ = endpoint
    name = 'subtitled_1_clip.mp4'
    (directory / name).write_bytes(b'captioned')
    clip['video_url'] = '/videos/job1/' + name
    seen = []
    def recap(jid, index, path):
        seen.append(Path(path).read_bytes())
        output = directory / ('subtitled_2_' + Path(path).name)
        output.write_bytes(b'edited-and-captioned')
        return str(output)
    monkeypatch.setattr(app, '_reapply_captions', recap)
    result = asyncio.run(app.edit_clip_with_ai(app.AIEditRequest(
        job_id='job1', instructions='Brighten from 3-5 seconds'), None))
    assert seen == [b'edited']
    assert '/subtitled_2_ai_' in result['new_video_url']
    assert (directory / name).read_bytes() == b'captioned'


def test_library_edits_chain_without_overwriting_the_saved_file(endpoint, monkeypatch):
    app, directory, _, _ = endpoint
    original = directory / 'saved.mp4'
    original.write_bytes(b'saved-original')
    monkeypatch.setattr(app, 'LOCAL_FOLDER_LIBRARY', SimpleNamespace(
        locate=lambda _fid: (original, {})))
    inputs = []
    def render(input_path, output, *args, **kwargs):
        inputs.append(Path(input_path).read_bytes())
        Path(output).write_bytes(inputs[-1] + b'-edited')
    monkeypatch.setattr(ai, 'render_edit', render)
    first = asyncio.run(app.edit_clip_with_ai(app.AIEditRequest(
        file_id='file1', instructions='Brighten'), None))
    second = asyncio.run(app.edit_clip_with_ai(app.AIEditRequest(
        file_id='file1', input_filename=first['new_video_url'].split('/')[-1], instructions='Brighten'), None))
    assert inputs == [b'saved-original', b'saved-original-edited']
    assert first['new_video_url'] != second['new_video_url']
    assert original.read_bytes() == b'saved-original'
