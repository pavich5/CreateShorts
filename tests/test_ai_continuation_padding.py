"""A restored video clock may include padding after the retained source speech."""
import asyncio
import json
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock

import pytest

import ai_clip_edit as ai
import ai_source_edit as ase
import clip_timing


def test_actual_extension_beyond_canonical_range_is_never_capped(tmp_path):
    segments = [{"start": 10, "end": 14.2}]
    clip = {"recipe": {"canonical_range": {"start": 10, "end": 14}}}
    result = ase.continuation_source_segments(tmp_path / "current.mp4", clip, segments,
        {"duration": 4.2, "audio_duration": 4.2})
    assert result == segments


def test_explicit_recut_can_intentionally_include_a_silent_visual_suffix(tmp_path):
    segments = [{"start": 10, "end": 14.2}]
    clip = {"recipe": {"canonical_range": {"start": 10, "end": 14}, "framing": "auto"}}
    result = ase.continuation_source_segments(tmp_path / "current.mp4", clip, segments,
        {"duration": 4.2, "audio_duration": 4})
    assert result == segments


def test_trim_inside_later_append_never_falls_back_to_original_canonical(tmp_path):
    segments = [{"start": 10, "end": 14.2}]
    clip = {"recipe": {"canonical_range": {"start": 10, "end": 14}},
        "source_edits": [{"mode": "append", "source_moment": {"start": 14, "end": 16}}]}
    result = ase.continuation_source_segments(tmp_path / "current.mp4", clip, segments,
        {"duration": 4.2, "audio_duration": 4})
    assert result == segments


def test_audio_proves_small_original_padding_without_guessing_from_video_duration(tmp_path):
    segments = [{"start": 10, "end": 14.2}]
    clip = {"recipe": {"canonical_range": {"start": 10, "end": 14}}}
    result = ase.continuation_source_segments(tmp_path / "current.mp4", clip, segments,
        {"duration": 4.2, "audio_duration": 4.01})
    assert result == [{"start": 10, "end": 14}]
    assert segments == [{"start": 10, "end": 14.2}]


def test_required_source_long_canonical_range_is_not_a_twenty_second_insert(tmp_path):
    segments = [{"start": 310.88, "end": 333.005}]
    clip = {"recipe": {"canonical_range": {"start": 310.88, "end": 332.78}}}
    result = ase.continuation_source_segments(tmp_path / "current.mp4", clip, segments,
        {"duration": 22.125, "audio_duration": 21.909})
    assert result == [{"start": 310.88, "end": 332.78}]


@pytest.mark.parametrize("stale,explicit_recut", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("removed_append", [False, True])
def test_repeated_head_trim_uses_bound_original_tail_after_media_audio_padding(tmp_path, stale, explicit_recut, removed_append):
    original = tmp_path / "generated.mp4"
    original.write_bytes(b"bound rendered original")
    clip_timing.write_render_timing(original, 329.26, 350.7)
    if stale:
        original.write_bytes(b"different rendered original")
    segments = [{"start": 329.635, "end": 350.801667}]
    recipe = {"canonical_range": {"start": 329.26, "end": 350.7}}
    if explicit_recut:
        recipe["framing"] = "auto"
    clip = {"recipe": recipe}
    if removed_append:
        clip["source_edits"] = [{"mode": "append", "source_moment": {"start": 350.801667, "end": 355.88}}]
        segments[-1]["end"] = 350.802  # persisted recipe rounds to milliseconds
    result = ase.continuation_source_segments(tmp_path / "current.mp4", clip, segments,
        {"duration": 21.166667, "audio_duration": 21.167})
    expected_end = segments[-1]["end"] if stale or explicit_recut else 350.7
    assert result == [{"start": 329.635, "end": expected_end}]
    assert segments[-1]["end"] == (350.802 if removed_append else 350.801667)


def test_removed_corrected_append_history_does_not_overlap_original_padding(tmp_path):
    original = tmp_path / "generated.mp4"
    original.write_bytes(b"bound rendered original")
    clip_timing.write_render_timing(original, 329.26, 350.7)
    segments = [{"start": 329.635, "end": 350.801667}]
    clip = {"recipe": {"canonical_range": {"start": 329.26, "end": 350.7}},
        "source_edits": [{"mode": "append", "at": 21.166,
            "source_moment": {"start": 350.7, "end": 355.88}}]}
    result = ase.continuation_source_segments(tmp_path / "current.mp4", clip, segments,
        {"duration": 21.166667, "audio_duration": 21.167})
    assert result == [{"start": 329.635, "end": 350.7}]
    # Once any actual append is retained, its source ending remains authoritative.
    segments[-1]["end"] = 351
    assert ase.continuation_source_segments(tmp_path / "current.mp4", clip, segments,
        {"duration": 21.365, "audio_duration": 21.365}) == segments


def test_recovered_recipe_extension_uses_verified_speech_end_at_actual_output_end(tmp_path, monkeypatch):
    import app

    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    directory = tmp_path / "padding-job"
    directory.mkdir()
    source, current = directory / "source.mp4", directory / "current.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=s=90x160:r=20:d=6", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=6", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-t", "4", "-c", "copy",
        str(directory / "cut.mp4")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(directory / "cut.mp4"), "-vf",
        "tpad=stop_mode=clone:stop_duration=0.25", "-c:v", "libx264", "-c:a", "copy", str(current)], check=True)
    transcript = {"segments": [{"words": [
        {"word": " setup", "start": .2, "end": .8},
        {"word": " that", "start": 4, "end": 4.6},
        {"word": " continues", "start": 4.6, "end": 4.9}]}]}
    clip = {"start": 0, "end": 4, "video_url": "/videos/padding-job/current.mp4"}
    clip_timing.write_render_timing(current, 0, 4)
    recovered = clip_timing.recover_caption_timing(directory, clip, transcript)
    clip.update(recovered)
    assert clip["recipe"]["segments"][-1]["end"] > 4.1
    assert clip["recipe"]["canonical_range"]["end"] == 4
    metadata = directory / "source_metadata.json"
    metadata.write_text(json.dumps({"shorts": [clip], "transcript": transcript}))
    job = {"status": "completed", "result": {"clips": [clip]}, "ready_files": {0: current.name}}
    monkeypatch.setattr(app, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app, "jobs", {"padding-job": job})
    monkeypatch.setattr(app, "_rerender_locks", {})
    monkeypatch.setattr(app, "BILLING_ENABLED", False)
    monkeypatch.setattr(app, "_ensure_job_files", AsyncMock())
    monkeypatch.setattr(app, "_assert_job_owner", AsyncMock())
    monkeypatch.setattr(app, "require_managed_entitlement", AsyncMock())
    monkeypatch.setattr(app, "_locate_source", lambda _: str(source))
    monkeypatch.setattr(app, "_archive_clip_edit_bg", lambda *args: None)
    monkeypatch.setattr(app, "_deliver", AsyncMock(side_effect=lambda request, job_id, name: name))
    monkeypatch.setattr(ai.llm_backend, "active", lambda: True)
    monkeypatch.setattr(ai, "plan_edit", lambda instructions, info, **kwargs: ai.validate_plan({
        "operations": [{"type": "source_continue", "start": info["duration"],
            "end": info["duration"] + 1, "query": "Continue the next source sentence"}]}, info["duration"], True))
    before = ai.probe(current)["duration"]
    result = asyncio.run(app.edit_clip_with_ai(app.AIEditRequest(job_id="padding-job",
        instructions="Add another second of story"), None))
    edit = result["source_edits"][-1]
    assert edit["source_moment"]["start"] == 4
    assert edit["at"] == pytest.approx(before)
    assert result["duration_seconds"] == pytest.approx(before + 1, abs=.1)
    saved_words = result["caption_words"]
    assert next(word for word in saved_words if word["word"].strip() == "that")["end"] - before == pytest.approx(.6)
