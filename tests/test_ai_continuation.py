"""Narrative extension appends retained speech rather than searching for an object.

These regression tests exercise selection and actual local FFmpeg rendering.
They do not stand in for an external AI planner integration test.
"""
from copy import deepcopy
import shutil
import subprocess

import pytest

import ai_clip_edit as ai
import ai_source_edit as ase
import source_edit


def transcript():
    return {"language": "en", "segments": [{"words": [
        {"word": " first", "start": 0.2, "end": 0.6},
        {"word": " next", "start": 1.2, "end": 1.6},
        {"word": " explanation", "start": 2.2, "end": 2.6},
        {"word": " ending", "start": 3.2, "end": 3.6},
    ]}]}


def test_continuation_is_an_append_and_cannot_run_in_selected_range():
    plan = ai.validate_plan({"operations": [{"type": "source_continue", "start": 0,
        "end": 2, "query": "Continue the existing narrative"}]}, 4, True)
    assert (plan.operations[0].start, plan.operations[0].end) == (4, 6)
    assert "Continued source story" in ai.describe_plan(plan)[0]
    with pytest.raises(ValueError, match="Whole clip"):
        ai.constrain_plan(plan, 1, 3, 4)


def test_continuation_selects_next_speech_without_visual_evidence():
    moment = ase.continuation_moment(transcript(), [{"start": 0, "end": 1}], 2, 4)
    assert (moment["start"], moment["end"]) == (1, 3)
    assert moment["evidence"]["kind"] == "transcript_continuation"
    assert "next explanation" in moment["summary"]
    assert "subject_frames" not in moment["evidence"]


def test_continuation_keeps_requested_content_and_ends_between_words():
    moment = ase.continuation_moment(transcript(), [{"start": 0, "end": 1}], 1.4, 4)
    assert moment["start"] == 1
    assert moment["end"] == pytest.approx(2.6)


@pytest.mark.parametrize("segments,requested,duration,message", [
    ([{"start": 0, "end": 4}], 1, 4, "end of the original"),
    ([{"start": 0, "end": 3}], 2, 4, "Only 1"),
    ([{"start": 1, "end": 3}, {"start": 0, "end": 1}], 2, 4, "already included"),
])
def test_unavailable_continuation_explains_limitation(segments, requested, duration, message):
    with pytest.raises(ValueError, match=message):
        ase.continuation_moment(transcript(), segments, requested, duration)


def test_continuation_does_not_claim_to_add_story_without_speech():
    with pytest.raises(ValueError, match="transcribed speech"):
        ase.continuation_moment({"segments": []}, [{"start": 0, "end": 1}], 2, 4)


@pytest.mark.parametrize("edited", [False, True])
def test_continuation_does_not_repeat_a_word_crossing_the_old_ending(edited):
    source = {"segments": [{"words": [{"word": " explanation", "start": .5, "end": 1.5}]}]}
    virtual = {"segments": [
        {"words": [{"word": " corrected" if edited else " explanation", "start": .5, "end": 1}]},
        {"words": [{"word": " explanation", "start": 1, "end": 1.5}]},
    ]}
    ase.join_continuation_caption(source, [{"start": 0, "end": 1}], virtual, 1)
    words = [word for segment in virtual["segments"] for word in segment["words"]]
    assert len(words) == 1
    assert words[0]["word"] == (" corrected" if edited else " explanation")
    assert (words[0]["start"], words[0]["end"]) == (.5, 1.5)


def test_continuation_does_not_extend_an_unrelated_manual_phrase():
    source = {"segments": [{"words": [{"word": " explanation", "start": .5, "end": 1.5}]}]}
    virtual = {"segments": [
        {"words": [{"word": " unrelated phrase", "start": .1, "end": 1}]},
        {"words": [{"word": " explanation", "start": 1, "end": 1.5}]},
    ]}
    ase.join_continuation_caption(source, [{"start": 0, "end": 1}], virtual, 1)
    assert len([word for segment in virtual["segments"] for word in segment["words"]]) == 2


def test_corrected_boundary_word_maps_through_reordered_source_and_mux_tail():
    source = {"segments": [{"words": [{"word": " wrong", "start": 30.5, "end": 31.5}]}]}
    virtual = {"segments": [
        {"words": [{"word": " corrected", "start": 2.5, "end": 3}]},
        {"words": [{"word": " wrong", "start": 3.125, "end": 3.625}]},
    ]}
    ase.join_continuation_caption(source, [{"start": 100, "end": 102}, {"start": 30, "end": 31}], virtual, 3.125)
    assert [word for segment in virtual["segments"] for word in segment["words"]] == [
        {"word": " corrected", "start": 2.5, "end": 3.625}]


def test_real_repeated_continuation_preserves_previous_audio_captions_and_source(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    source = tmp_path / "source.mp4"
    current = tmp_path / "current.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=s=90x160:r=10:d=4", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-t", "1",
        "-c:v", "libx264", "-c:a", "aac", str(current)], check=True)
    original = source.read_bytes()
    clip = {"start": 0, "end": 1, "caption_words": [
        {"word": " corrected", "start": .2, "end": .6}]}
    segments = [{"start": 0, "end": 1}]
    monkeypatch.setattr(ase.source_moments, "find_source_moment", lambda *a, **k:
        pytest.fail("Narrative continuation must not use object/reveal frame search"))
    for index in range(2):
        output = tmp_path / f"continued_{index}.mp4"
        result = ase.render_source_operation(current, output, source_path=source,
            transcript=transcript(), current_segments=segments, current_clip=clip,
            operation={"type": "source_continue", "start": 0, "end": 1,
                       "query": "Continue the story"})
        info = source_edit.probe(output)
        assert info["duration"] == pytest.approx(index + 2, abs=.12)
        assert (info["width"], info["height"], info["has_audio"]) == (90, 160, True)
        updates = result["clip_updates"]
        assert updates["caption_words"][0] == clip["caption_words"][0]
        assert len(updates["source_edits"]) == index + 1
        assert updates["source_edits"][-1]["source_moment"]["evidence"]["kind"] == "transcript_continuation"
        assert updates["caption_words"][-1]["word"].strip() == ("next" if index == 0 else "explanation")
        clip = {**clip, **deepcopy(updates)}
        segments = clip["recipe"]["segments"]
        current = output
    assert source.read_bytes() == original
