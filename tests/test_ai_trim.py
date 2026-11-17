"""AI trims keep the requested picture, sound, captions and source recipe together."""
import asyncio
from array import array
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import ai_clip_edit as ai


def trim_plan(start=1.9, end=6, *extra):
    return {"operations": [{"type": "trim", "start": start, "end": end}, *extra]}


@pytest.mark.parametrize("start,end", [(-1, 6), (6, 7), (4, 3), (2, 2.05),
                                         (0, 6), (0, 7), (float("nan"), 6),
                                         (1.9, float("inf"))])
def test_invalid_or_unchanged_trims_are_rejected(start, end):
    with pytest.raises(ValueError):
        ai.validate_plan(trim_plan(start, end), 6, False)


def test_trim_can_keep_burned_text_and_combine_original_timeline_effects():
    mute = {"type": "mute", "start": 3, "end": 4}
    plan = ai.validate_plan(trim_plan(1.9, 4.9, mute), 6, False, has_burns=True)
    assert [(op.type, op.start, op.end) for op in plan.operations] == [
        ("trim", 1.9, 4.9), ("mute", 3, 4)]


@pytest.mark.parametrize("extra", [
    {"type": "trim", "start": 2, "end": 5},
    {"type": "source_append", "start": 6, "end": 9, "query": "the reaction"},
])
def test_trim_rejects_conflicting_timeline_operations(extra):
    with pytest.raises(ValueError):
        ai.validate_plan(trim_plan(1.9, 6, extra), 6, True)


def test_trim_requires_whole_clip_scope():
    plan = ai.validate_plan(trim_plan(), 6, False)
    with pytest.raises(ValueError, match="[Ww]hole clip"):
        ai.constrain_plan(plan, 1, 5, 6)


def test_planner_supports_requested_start_and_uses_playhead_context(monkeypatch):
    prompts = []
    monkeypatch.setattr(ai.llm_backend, "active", lambda: True)
    monkeypatch.setenv('LLM_MODEL', 'test-generic-local-model')

    def generate(prompt, schema):
        prompts.append(prompt)
        assert schema is ai.EditPlan
        return trim_plan(), None

    monkeypatch.setattr(ai.llm_backend, "generate_json", generate)
    result = ai.plan_edit("1.9 seconds the video should start from here",
                          {"duration": 6, "playhead_time": 1.9})
    assert result.operations[0].type == "trim"
    assert "- trim:" in prompts[0]
    assert "1.9" in prompts[0]
    assert "rewriting captions, trimming" not in prompts[0]


def caption_transcript():
    return {"language": "en", "segments": [{"start": 0, "end": 40, "words": [
        {"word": "old", "start": 30.2, "end": 30.6},
        {"word": "first", "start": 31.8, "end": 32.4},
        {"word": "second", "start": 10.5, "end": 11},
        {"word": "last", "start": 11.7, "end": 12.2},
    ]}]}


def test_trim_accepts_verified_video_tail_without_inventing_caption_words():
    segments = [{'start': 4957.66, 'end': 5013.21}]
    transcript = {'segments': [{'words': [
        {'word': 'kept', 'start': 5013.0, 'end': 5013.2},
        {'word': 'outside', 'start': 5013.3, 'end': 5013.6},
    ]}]}
    result = ai.prepare_trim({'start': 4957.66, 'end': 5013.21}, transcript,
        segments, 1, 56.1561, 56.1561, [], media_info={'audio_duration': 55.6})
    assert result['clip_updates']['duration_seconds'] == pytest.approx(55.1561)
    assert [word['word'].strip() for word in result['clip_updates']['caption_words']] == ['kept']
    assert sum(s['end'] - s['start'] for s in result['clip_updates']['recipe']['segments']) == pytest.approx(55.1561)
    assert segments == [{'start': 4957.66, 'end': 5013.21}]


@pytest.mark.parametrize('media_info', [None, {'audio_duration': 50}, {'audio_duration': float('nan')}])
def test_trim_still_rejects_unverified_source_mismatch(media_info):
    with pytest.raises(ValueError, match='source timing'):
        ai.prepare_trim({'start': 10, 'end': 15}, {}, [{'start': 10, 'end': 15}],
                        .5, 6, 6, [], media_info=media_info)


def test_trim_rebases_reordered_recipe_captions_and_layouts_without_mutating_inputs():
    segments = [{"start": 30, "end": 33}, {"start": 10, "end": 13}]
    clip = {"start": 10, "end": 33, "recipe": {"v": 1, "segments": segments,
            "canonical_range": {"start": 10, "end": 40}, "keep": "custom"},
            "source_visuals": [{"source_start": 10, "source_end": 13,
                                "picture_start": 60, "picture_end": 63}]}
    layouts = [{"start": 0, "end": 2.5, "layout": "track"},
               {"start": 2.5, "end": 6, "layout": "wide", "content": [.2, .8]}]
    before = deepcopy((clip, segments, layouts))
    result = ai.prepare_trim(clip, caption_transcript(), segments, 1.9, 4.9, 6, layouts)
    updates = result["clip_updates"]
    assert updates["recipe"]["segments"] == [
        {"start": 31.9, "end": 33}, {"start": 10, "end": 11.9}]
    assert updates["recipe"]["canonical_range"] == {"start": 10, "end": 40}
    assert updates["recipe"]["keep"] == "custom"
    assert updates["duration_seconds"] == pytest.approx(3)
    assert (updates["start"], updates["end"]) == (10, 33)
    assert updates["layout_ranges"] == [
        {"start": 0, "end": .6, "layout": "track"},
        {"start": .6, "end": 3, "layout": "wide", "content": [.2, .8]}]
    words = [word for segment in result["transcript"]["segments"] for word in segment["words"]]
    assert [word["word"].strip() for word in words] == ["first", "second", "last"]
    assert [(word["start"], word["end"]) for word in words] == [(0, .5), (1.6, 2.1), (2.8, 3)]
    assert updates["caption_words"] == words
    # Source visuals use absolute source anchors; remap their placements through
    # the kept recipe rather than subtracting 1.9 from those source timestamps.
    from source_visual_edit import _mapped
    assert _mapped(updates["source_visuals"], updates["recipe"]["segments"]) == [
        {"at": 1.1, "start": 60, "end": 61.9}]
    assert (clip, segments, layouts) == before


def test_trim_preserves_custom_caption_words_and_clips_boundary_words():
    clip = {"start": 10, "end": 16, "caption_words": [
        {"word": " deleted", "start": .2, "end": 1},
        {"word": " Custom", "start": 1.8, "end": 2.3},
        {"word": " ending", "start": 4.7, "end": 5.3},
    ]}
    result = ai.prepare_trim(clip, caption_transcript(), [{"start": 10, "end": 16}],
                             1.9, 4.9, 6, [])
    assert result["clip_updates"]["caption_words"] == [
        {"word": " Custom", "start": 0, "end": .4},
        {"word": " ending", "start": 2.8, "end": 3}]


def test_saved_file_trim_discards_inherited_project_timing():
    updates = ai.prepare_trim({}, None, None, 1.9, 6, 6, [])['clip_updates']
    assert updates['recipe'] is None
    assert (updates['start'], updates['end']) == pytest.approx((0, 4.1))
    assert updates['caption_words'] is None
    assert updates['source_visuals'] == []


@pytest.mark.parametrize('duration,remaining', [(1.5, None), (2.5, .6), (None, 4.1)])
def test_trim_updates_hook_metadata(duration, remaining):
    clip = {'start': 10, 'end': 16,
            'auto_hook': {'text': 'A hook', 'duration_seconds': duration}}
    updates = ai.prepare_trim(clip, None, [{'start': 10, 'end': 16}],
                              1.9, 6, 6, [])['clip_updates']
    if remaining is None:
        assert updates['auto_hook'] is None
    else:
        assert updates['auto_hook']['duration_seconds'] == pytest.approx(remaining)
    assert clip['auto_hook']['duration_seconds'] == duration


@pytest.fixture
def trim_media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg not installed")
    path = tmp_path / "timed.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:s=160x96:r=10:d=6,drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,1.5)',"
        "drawbox=x=0:y=0:w=iw:h=ih:color=green:t=fill:enable='gte(t,4)'",
        "-f", "lavfi", "-i",
        r"aevalsrc=if(lt(t\,3)\,0.1*sin(2*PI*440*t)\,0.1*sin(2*PI*880*t)):s=48000:d=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        capture_output=True, check=True, timeout=30)
    return path


def pixel(path, time):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(time),
        "-i", str(path), "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24",
        "-f", "rawvideo", "-"], timeout=30)


def samples(path, time, duration=.2):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(time),
        "-i", str(path), "-t", str(duration), "-vn", "-ac", "1", "-ar", "48000",
        "-f", "f32le", "-"], timeout=30)
    values = array("f")
    values.frombytes(raw)
    return values


def frequency(values):
    return sum(a <= 0 < b for a, b in zip(values, values[1:])) * 48000 / len(values)


def test_trim_removes_earlier_frames_and_shifts_audio_in_sync(trim_media, tmp_path):
    output = tmp_path / "trimmed.mp4"
    info = ai.probe(trim_media)
    # The editor sends its playhead alongside probe metadata. Planner-only
    # context must not leak as keyword arguments into the FFmpeg filter builder.
    info['playhead_time'] = 1.9
    ai.render_edit(trim_media, output, ai.validate_plan(trim_plan(1.9, 4.9), 6, False), info)
    assert ai.probe(output)["duration"] == pytest.approx(3, abs=.11)
    assert pixel(output, .05)[2] > 200  # Source 1.9s is blue, not the red intro.
    assert pixel(output, 2.3)[1] > 100  # Source 4.2s is green.
    assert frequency(samples(output, .2)) == pytest.approx(440, abs=10)
    assert frequency(samples(output, 1.4)) == pytest.approx(880, abs=10)


def test_trim_supports_silent_video(trim_media, tmp_path):
    silent, output = tmp_path / "silent.mp4", tmp_path / "silent-trimmed.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(trim_media),
                    "-map", "0:v:0", "-c:v", "copy", "-an", str(silent)],
                   capture_output=True, check=True, timeout=30)
    ai.render_edit(silent, output, ai.validate_plan(trim_plan(1.9, 4.9), 6, False), ai.probe(silent))
    assert ai.probe(output)["duration"] == pytest.approx(3, abs=.11)
    streams = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams",
                                                  "-of", "json", str(output)], timeout=30))
    assert all(stream["codec_type"] != "audio" for stream in streams["streams"])


def test_combined_mute_uses_the_pretrim_timeline(trim_media, tmp_path):
    output = tmp_path / "trimmed-muted.mp4"
    raw = trim_plan(1.9, 4.9, {"type": "mute", "start": 3, "end": 4})
    ai.render_edit(trim_media, output, ai.validate_plan(raw, 6, False), ai.probe(trim_media))
    assert max(abs(value) for value in samples(output, .3)) > .02
    assert max(abs(value) for value in samples(output, 1.4)) < .002
    assert frequency(samples(output, 2.4)) == pytest.approx(880, abs=10)


@pytest.fixture
def trim_endpoint(tmp_path, monkeypatch):
    app = pytest.importorskip("app")
    directory = tmp_path / "trim-job"
    directory.mkdir()
    original = directory / "clip.mp4"
    original.write_bytes(b"original")
    clip = {"video_url": "/videos/trim-job/clip.mp4", "start": 10, "end": 16,
            "caption_style": {"font_name": "Arial", "font_size": 60}}
    transcript = {"language": "en", "segments": [{"start": 10, "end": 16, "words": [
        {"word": "intro", "start": 10.2, "end": 11},
        {"word": "kept", "start": 12, "end": 13}]}]}
    meta = directory / "video_metadata.json"
    meta.write_text(json.dumps({"shorts": [clip], "transcript": transcript}))
    job = {"status": "completed", "result": {"clips": [clip]}, "ready_files": {0: "clip.mp4"}}
    monkeypatch.setattr(app, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app, "jobs", {"trim-job": job})
    monkeypatch.setattr(app, "_rerender_locks", {})
    monkeypatch.setattr(app, "BILLING_ENABLED", False)
    monkeypatch.setattr(app.llm_backend, "active", lambda: True)
    monkeypatch.setattr(app, "_ensure_job_files", AsyncMock())
    monkeypatch.setattr(app, "_assert_job_owner", AsyncMock())
    monkeypatch.setattr(app, "require_managed_entitlement", AsyncMock())
    monkeypatch.setattr(app, "_locate_source", lambda _: None)
    monkeypatch.setattr(app, "_archive_clip_edit_bg", lambda *args: None)
    monkeypatch.setattr(app, "_deliver", AsyncMock(side_effect=lambda request, jid, name: name))
    real_probe, real_render = ai.probe, ai.render_edit
    durations = {}

    def mock_probe(path):
        # These endpoint unit tests stub encoding; model each derivative's
        # duration so final-file reconciliation is tested with a coherent probe.
        name = Path(path).name
        duration = next((value for filename, value in durations.items() if name.endswith(filename)), 6)
        return {"width": 160, "height": 96, "fps": 10, "duration": duration}

    def mock_render(_input, output, plan, info, **kwargs):
        operation = next((op for op in plan.operations if op.type in ('trim', 'remove')), None)
        durations[Path(output).name] = (sum(piece['end'] - piece['start'] for piece in ai.retained_ranges(operation, info['duration']))
                                        if operation else info['duration'])
        Path(output).write_bytes(b"trimmed")

    monkeypatch.setattr(ai, "probe", mock_probe)
    monkeypatch.setattr(ai, "plan_edit", lambda *args, **kwargs: ai.validate_plan(trim_plan(), 6, False))
    monkeypatch.setattr(ai, "render_edit", mock_render)
    return SimpleNamespace(app=app, directory=directory, clip=clip, meta=meta, job=job,
                           real_probe=real_probe, real_render=real_render,
                           mock_probe=mock_probe, mock_render=mock_render)


def apply_trim(endpoint):
    return asyncio.run(endpoint.app.edit_clip_with_ai(endpoint.app.AIEditRequest(
        job_id="trim-job", instructions="1.9 seconds the video should start from here"), None))


def test_endpoint_commits_trim_duration_recipe_and_output_relative_preview(trim_endpoint):
    result = apply_trim(trim_endpoint)
    assert result["success"]
    assert result["duration_seconds"] == pytest.approx(4.1)
    assert (result["preview_start"], result["preview_end"]) == pytest.approx((0, 4.1))
    assert result["recipe"]["segments"] == [{"start": 11.9, "end": 16}]
    assert [(w["start"], w["end"]) for w in result["caption_words"]] == [(.1, 1.1)]
    saved = json.loads(trim_endpoint.meta.read_text())["shorts"][0]
    assert saved["duration_seconds"] == trim_endpoint.clip["duration_seconds"] == pytest.approx(4.1)
    assert saved["recipe"] == result["recipe"]
    assert saved["ai_edit"]["previous_video_url"] == "/videos/trim-job/clip.mp4"
    assert (trim_endpoint.directory / "clip.mp4").read_bytes() == b"original"


def test_manual_trim_needs_no_prompt_provider_or_ai_quota(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    monkeypatch.setattr(endpoint.app.llm_backend, "active", lambda: False)
    monkeypatch.setattr(endpoint.app, "resolve_gemini", lambda *args: pytest.fail("manual trim requested an AI key"))
    monkeypatch.setattr(endpoint.app, "_ai_edit_minutes", lambda *args: pytest.fail("manual trim consumed AI quota"))
    monkeypatch.setattr(ai, "plan_edit", lambda *args, **kwargs: pytest.fail("manual trim called AI"))
    monkeypatch.setattr(endpoint.app, "BILLING_ENABLED", True)
    result = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id="trim-job", start=1.9, end=4.9), None))
    assert result["success"]
    assert result["duration_seconds"] == pytest.approx(3)
    assert result["recipe"]["segments"] == [{"start": 11.9, "end": 14.9}]
    assert result["caption_words"] == [{"word": " kept", "start": .1, "end": 1.1}]
    assert (result["preview_start"], result["preview_end"]) == pytest.approx((0, 3))
    assert (endpoint.directory / "clip.mp4").read_bytes() == b"original"


@pytest.mark.parametrize('mode', ['keep', 'remove'])
def test_untouched_render_with_replaced_metadata_can_be_trimmed(trim_endpoint, monkeypatch, mode):
    from test_clip_timing import make_legacy_render
    # Use the real caption-evidence recovery, with the endpoint's render stub.
    fixture = make_legacy_render(trim_endpoint.directory)
    _, srt, _, transcript, _ = fixture
    endpoint = trim_endpoint
    served = 'subtitled_123_clip.mp4'
    (endpoint.directory / served).write_bytes(b'captioned original')
    endpoint.clip.update(start=10, end=14, video_url=f'/videos/trim-job/{served}')
    endpoint.meta.write_text(json.dumps({'shorts': [endpoint.clip], 'transcript': transcript}))
    monkeypatch.setattr(endpoint.app, '_strip_burned_captions', lambda *args, **kwargs: 'clip.mp4')
    monkeypatch.setattr(endpoint.app, '_restyle_captions', lambda path, *args: path)
    result = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', start=1, end=4, mode=mode), None))
    assert result['success']
    assert result['duration_seconds'] == pytest.approx(3)
    expected = ([{'start': 101, 'end': 104}] if mode == 'keep' else
                [{'start': 100, 'end': 101}, {'start': 104, 'end': 106}])
    assert result['recipe']['segments'] == expected
    assert result['recipe']['canonical_range'] == {'start': 100, 'end': 106}
    assert all(0 <= word['start'] < word['end'] <= 3 for word in result['caption_words'])
    assert (endpoint.directory / served).read_bytes() == b'captioned original'
    # The repaired source recipe supports the next trim without caption recovery.
    monkeypatch.setattr(ai, 'probe', endpoint.mock_probe)
    srt.unlink()
    again = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', start=.5, end=2.5), None))
    assert again['success'] and again['duration_seconds'] == pytest.approx(2)


def test_trim_uses_the_selected_render_when_its_clean_file_was_replaced(trim_endpoint, monkeypatch):
    from clip_timing import write_render_timing
    endpoint = trim_endpoint
    served = endpoint.directory / 'subtitled_123_clip.mp4'
    served.write_bytes(b'older complete render')
    endpoint.clip.update(start=10, end=14, video_url=f'/videos/trim-job/{served.name}')
    data = json.loads(endpoint.meta.read_text())
    data['shorts'][0] = endpoint.clip
    endpoint.meta.write_text(json.dumps(data))
    write_render_timing(served, 100, 106)
    monkeypatch.setattr(endpoint.app, '_strip_burned_captions', lambda *args, **kwargs: 'clip.mp4')
    monkeypatch.setattr(ai, 'probe', lambda path: {
        'width': 160, 'height': 96, 'fps': 10, 'duration': 6 if Path(path) == served else 4})
    sources = []
    def render(source, output, *args, **kwargs):
        sources.append(Path(source))
        Path(output).write_bytes(b'trimmed selected render')
    monkeypatch.setattr(ai, 'render_edit', render)
    result = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', start=1, end=4), None))
    assert result['success']
    assert sources == [served]
    assert result['recipe']['segments'] == [{'start': 101, 'end': 104}]
    assert served.read_bytes() == b'older complete render'


def test_manual_trim_supports_a_local_file_draft(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    source = endpoint.directory / "clip.mp4"
    monkeypatch.setattr(endpoint.app, "LOCAL_FOLDER_LIBRARY", SimpleNamespace(locate=lambda _: (source, {})))
    monkeypatch.setattr(endpoint.app.llm_backend, "active", lambda: False)
    monkeypatch.setattr(endpoint.app, "resolve_gemini", lambda *args: pytest.fail("manual trim requested an AI key"))
    monkeypatch.setattr(ai, "plan_edit", lambda *args, **kwargs: pytest.fail("manual trim called AI"))
    result = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        file_id="local-file", start=.5, end=5), None))
    assert result["success"]
    assert result["new_video_url"].startswith("/videos/ai_local-file/ai_")
    assert result["duration_seconds"] == pytest.approx(4.5)
    assert result["recipe"] is None
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("mode", ["trim", "ai"])
def test_local_file_edit_continues_the_selected_media_draft(trim_endpoint, monkeypatch, mode):
    endpoint = trim_endpoint
    file_id = 'a' * 64
    draft_id = 'manual_' + file_id
    draft = endpoint.directory.parent / draft_id
    draft.mkdir()
    selected = draft / 'broll_123_clip.mp4'
    selected.write_bytes(b'video with media')
    clip = {"video_url": f"/videos/{draft_id}/{selected.name}", "start": 0, "end": 6}
    endpoint.app.jobs[draft_id] = {"status": "completed", "result": {"clips": [clip]},
                                  "ready_files": {0: selected.name}}
    metadata = draft / (draft_id + '_metadata.json')
    metadata.write_text(json.dumps({"shorts": [clip], "library_source_id": file_id,
                                    "transcript": {"segments": []}}))
    monkeypatch.setattr(endpoint.app, "LOCAL_CAMPAIGNS", None)
    monkeypatch.setattr(endpoint.app, "LOCAL_FOLDER_LIBRARY", SimpleNamespace(
        locate=lambda _: (endpoint.directory / "clip.mp4", {})))
    rendered = []

    def render(input_path, output_path, *args, **kwargs):
        rendered.append(Path(input_path))
        endpoint.mock_render(input_path, output_path, *args, **kwargs)
        Path(output_path).write_bytes(b'trimmed media draft')

    monkeypatch.setattr(ai, "render_edit", render)
    if mode == 'trim':
        result = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
            file_id=file_id, input_filename=selected.name, start=1.9, end=6), None))
    else:
        result = asyncio.run(endpoint.app.edit_clip_with_ai(endpoint.app.AIEditRequest(
            file_id=file_id, input_filename=selected.name, instructions='Start at 1.9 seconds'), None))
    assert result['success']
    assert rendered == [selected]
    assert result['new_video_url'].startswith(f'/videos/{draft_id}/ai_')
    assert clip['duration_seconds'] == pytest.approx(4.1)
    assert json.loads(metadata.read_text())['shorts'][0]['video_url'] == result['new_video_url']
    assert selected.read_bytes() == b'video with media'
    assert (endpoint.directory / 'clip.mp4').read_bytes() == b'original'


@pytest.mark.parametrize("start,end", [(0, 6), (0, 7), (4, 3), (2, 2.05)])
def test_manual_trim_rejects_invalid_or_unchanged_ranges(trim_endpoint, start, end):
    endpoint = trim_endpoint
    before = endpoint.meta.read_bytes(), deepcopy(endpoint.clip)
    with pytest.raises(endpoint.app.HTTPException) as failure:
        asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
            job_id="trim-job", start=start, end=end), None))
    assert failure.value.status_code == 400
    assert (endpoint.meta.read_bytes(), endpoint.clip) == before


def test_editor_playhead_reaches_planner_and_real_trim_renderer(trim_endpoint, trim_media, monkeypatch):
    endpoint = trim_endpoint
    shutil.copyfile(trim_media, endpoint.directory / 'clip.mp4')
    monkeypatch.setattr(ai, 'probe', endpoint.real_probe)
    planned, rendered = [], []

    def planning(instructions, info, **kwargs):
        planned.append(dict(info))
        return ai.validate_plan(trim_plan(), info['duration'], False)

    def rendering(input_path, output_path, plan, info, **kwargs):
        rendered.append(dict(info))
        return endpoint.real_render(input_path, output_path, plan, info, **kwargs)

    monkeypatch.setattr(ai, 'plan_edit', planning)
    monkeypatch.setattr(ai, 'render_edit', rendering)
    result = asyncio.run(endpoint.app.edit_clip_with_ai(endpoint.app.AIEditRequest(
        job_id='trim-job', playhead_time=1.9,
        instructions='Start the video here at 1.9 seconds'), None))
    assert result['success']
    assert planned[0]['playhead_time'] == rendered[0]['playhead_time'] == 1.9
    output = endpoint.directory / result['new_video_url'].split('/')[-1]
    assert endpoint.real_probe(output)['duration'] == pytest.approx(4.1, abs=.11)
    assert pixel(output, .05)[2] > 200
    assert frequency(samples(output, 1.4)) == pytest.approx(880, abs=10)
    assert endpoint.clip['recipe']['segments'] == [{'start': 11.9, 'end': 16}]


def test_saved_manual_trim_and_second_trim_remove_intro_and_tail_after_reopening(trim_endpoint, trim_media, monkeypatch):
    endpoint = trim_endpoint
    shutil.copyfile(trim_media, endpoint.directory / 'clip.mp4')
    monkeypatch.setattr(ai, 'probe', endpoint.real_probe)
    monkeypatch.setattr(ai, 'render_edit', endpoint.real_render)
    monkeypatch.setattr(endpoint.app.llm_backend, 'active', lambda: False)
    monkeypatch.setattr(ai, 'plan_edit', lambda *args, **kwargs: pytest.fail('manual trim called AI'))

    first = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', input_filename='clip.mp4', start=1.9, end=4.9), None))
    first_path = endpoint.directory / Path(first['new_video_url']).name
    assert endpoint.real_probe(first_path)['duration'] == pytest.approx(3, abs=.11)
    assert pixel(first_path, .05)[2] > 200  # The red intro is physically gone.
    assert pixel(first_path, 2.5)[1] > 100  # The retained green tail is present.

    # Simulate opening the saved job from metadata rather than retaining the
    # old in-memory clip. Every next edit must start from this derivative.
    reopened = json.loads(endpoint.meta.read_text())['shorts'][0]
    assert reopened['video_url'] == first['new_video_url']
    endpoint.job['result']['clips'][0] = reopened
    second = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', input_filename=first_path.name, start=.3, end=1.7), None))
    final = endpoint.directory / Path(second['new_video_url']).name
    assert endpoint.real_probe(final)['duration'] == pytest.approx(1.4, abs=.11)
    assert pixel(final, .05)[2] > 200
    assert pixel(final, 1.2)[2] > 200  # No green tail or red intro can return.
    assert frequency(samples(final, .2)) == pytest.approx(440, abs=10)
    assert frequency(samples(final, 1)) == pytest.approx(880, abs=10)
    persisted = json.loads(endpoint.meta.read_text())['shorts'][0]
    assert persisted['video_url'] == second['new_video_url']
    assert persisted['duration_seconds'] == pytest.approx(1.4)
    assert persisted['recipe']['segments'][0]['start'] == pytest.approx(12.2)
    assert persisted['recipe']['segments'][0]['end'] == pytest.approx(13.6)


def test_endpoint_restores_captions_against_new_timeline(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    (endpoint.directory / "subtitled_1_clip.mp4").write_bytes(b"captioned original")
    endpoint.clip["video_url"] = "/videos/trim-job/subtitled_1_clip.mp4"
    seen = []

    def recap(path, transcript, start, end, style, layouts=None):
        seen.append((deepcopy(transcript), start, end, style))
        output = endpoint.directory / ("subtitled_2_" + Path(path).name)
        output.write_bytes(b"trimmed and captioned")
        return str(output)

    monkeypatch.setattr(endpoint.app, "_restyle_captions", recap)
    monkeypatch.setattr(endpoint.app, "_reapply_captions", lambda *args: pytest.fail("old caption timeline used"))
    result = apply_trim(endpoint)
    assert result["success"]
    assert seen[0][1:3] == pytest.approx((0, 4.1))
    assert [(w["start"], w["end"]) for s in seen[0][0]["segments"] for w in s["words"]] == [(.1, 1.1)]
    assert seen[0][3] == endpoint.clip["caption_style"]


def test_failed_trim_delivery_keeps_original_metadata(trim_endpoint, monkeypatch):
    before = trim_endpoint.meta.read_bytes(), deepcopy(trim_endpoint.clip), deepcopy(trim_endpoint.job["ready_files"])
    monkeypatch.setattr(trim_endpoint.app, "_deliver", AsyncMock(side_effect=RuntimeError("delivery failed")))
    with pytest.raises(trim_endpoint.app.HTTPException):
        apply_trim(trim_endpoint)
    assert (trim_endpoint.meta.read_bytes(), trim_endpoint.clip, trim_endpoint.job["ready_files"]) == before
    assert (trim_endpoint.directory / "clip.mp4").read_bytes() == b"original"


@pytest.mark.parametrize("custom_words,expected_words", [
    ([{"word": " Custom", "start": 1.8, "end": 2.4}],
     [{"word": " Custom", "start": 0, "end": .5}]),
    ([{"word": " Removed", "start": .2, "end": .8}], []),
])
def test_trimmed_custom_captions_survive_read_and_restyle(trim_endpoint, monkeypatch,
                                                        custom_words, expected_words):
    endpoint = trim_endpoint
    endpoint.clip['caption_words'] = custom_words
    original_meta = json.loads(endpoint.meta.read_text())
    original_meta['shorts'][0] = endpoint.clip
    endpoint.meta.write_text(json.dumps(original_meta))
    trimmed = apply_trim(endpoint)
    assert trimmed['caption_words'] == expected_words
    transcript = asyncio.run(endpoint.app.get_clip_transcript('trim-job', 0, None))
    assert transcript['durationSec'] == pytest.approx(4.1)
    assert transcript['captions'] == [{
        'text': word['word'].strip(), 'startMs': round(word['start'] * 1000),
        'endMs': round(word['end'] * 1000), 'utteranceId': 0,
    } for word in expected_words]
    burned = []

    def burn(input_path, output_path, sub_path, words, start, end, style, layouts):
        burned.append((deepcopy(words), start, end))
        Path(output_path).write_bytes(b'custom captions restyled')
        return True

    monkeypatch.setattr(endpoint.app, '_burn_caption_style', burn)
    monkeypatch.setattr(endpoint.app, 'reserve_managed_action', AsyncMock(return_value=None))
    result = asyncio.run(endpoint.app.add_subtitles(endpoint.app.SubtitleRequest(
        job_id='trim-job', clip_index=0, font_size=42), None))
    assert result['success']
    assert [word for record in burned for segment in record[0]['segments']
            for word in segment['words']] == expected_words
    assert len(burned) == bool(expected_words)
    if burned:
        assert burned[0][1:] == pytest.approx((0, 4.1))
    assert json.loads(endpoint.meta.read_text())['shorts'][0]['caption_words'] == expected_words
    assert endpoint.clip['caption_words'] == expected_words


def test_explicit_new_caption_words_replace_the_trimmed_saved_override(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    apply_trim(endpoint)
    burned = []

    def burn(input_path, output_path, sub_path, transcript, *args):
        burned.extend(word for segment in transcript['segments'] for word in segment['words'])
        Path(output_path).write_bytes(b'new edited captions')
        return True

    monkeypatch.setattr(endpoint.app, '_burn_caption_style', burn)
    monkeypatch.setattr(endpoint.app, 'reserve_managed_action', AsyncMock(return_value=None))
    result = asyncio.run(endpoint.app.add_subtitles(endpoint.app.SubtitleRequest(
        job_id='trim-job', clip_index=0,
        words=[endpoint.app.CaptionWordIn(text='New', startMs=300, endMs=600)]), None))
    assert result['success']
    assert burned == [{'word': ' New', 'start': .3, 'end': .6}]
    assert endpoint.clip['caption_words'] == burned


def test_trimmed_captioned_clip_without_surviving_words_needs_no_reburn(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    endpoint.clip['caption_words'] = [{'word': ' Gone', 'start': .2, 'end': .8}]
    (endpoint.directory / 'subtitled_1_clip.mp4').write_bytes(b'captioned original')
    endpoint.clip['video_url'] = '/videos/trim-job/subtitled_1_clip.mp4'
    monkeypatch.setattr(endpoint.app, '_restyle_captions', lambda *args: pytest.fail('No words to burn'))
    result = apply_trim(endpoint)
    assert result['success']
    assert result['caption_words'] == []
    assert '/ai_' in result['new_video_url']


@pytest.mark.parametrize('hook_duration,remaining', [(1.5, None), (2.5, .6), (None, None)])
def test_trim_preserves_only_the_remaining_hook_window(trim_endpoint, monkeypatch, hook_duration, remaining):
    import hooks
    endpoint = trim_endpoint
    hooked_name = 'hooked_1_clip.mp4'
    (endpoint.directory / hooked_name).write_bytes(b'hooked original')
    endpoint.clip['video_url'] = '/videos/trim-job/' + hooked_name
    recipe_path, png_path = hooks.recipe_paths(str(endpoint.directory), hooked_name)
    Path(recipe_path).write_text(json.dumps({'source': 'clip.mp4', 'x': 0, 'y': 0,
                                            'duration': hook_duration}))
    Path(png_path).write_bytes(b'hook image')
    restored = []
    monkeypatch.setattr(hooks, '_overlay_graph', lambda x, y, duration: restored.append(duration) or 'overlay')
    monkeypatch.setattr(hooks, '_overlay_cmd', lambda video, image, graph, output: [output])

    def encode(command, original, outputs):
        Path(outputs[0]).write_bytes(b'trimmed and hooked')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(hooks, '_run_encode', encode)
    result = apply_trim(endpoint)
    assert result['success']
    if hook_duration is not None and hook_duration < 1.9:
        assert restored == []
        assert '/ai_' in result['new_video_url']
    else:
        assert restored == [pytest.approx(remaining) if remaining is not None else None]
        saved_recipe, _ = hooks.recipe_paths(str(endpoint.directory), result['new_video_url'].split('/')[-1])
        assert json.loads(Path(saved_recipe).read_text())['duration'] == (pytest.approx(remaining) if remaining is not None else None)


def test_repeated_manual_trim_with_longer_video_than_audio(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    source = endpoint.directory / 'clip.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=blue:s=160x96:r=10:d=4', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=3.4', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True)
    endpoint.clip['end'] = 13.4
    metadata = json.loads(endpoint.meta.read_text())
    metadata['shorts'][0]['end'] = 13.4
    endpoint.meta.write_text(json.dumps(metadata))
    monkeypatch.setattr(ai, 'probe', endpoint.real_probe)
    monkeypatch.setattr(ai, 'render_edit', endpoint.real_render)
    info = ai.probe(source)
    assert info['duration'] - info['audio_duration'] > .35
    first = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', start=.5, end=4), None))
    assert first['success']
    assert ai.probe(endpoint.directory / Path(first['new_video_url']).name)['duration'] == pytest.approx(3.5, abs=.11)
    second = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', start=.5, end=3.4), None))
    assert second['success']
    assert ai.probe(endpoint.directory / Path(second['new_video_url']).name)['duration'] == pytest.approx(2.9, abs=.11)
