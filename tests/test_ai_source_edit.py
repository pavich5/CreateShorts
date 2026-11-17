"""Verified source scenes keep captions, source recipes and timing together."""
import copy
from pathlib import Path
import shutil
import subprocess

import pytest

import ai_source_edit as ase
import source_edit


def verified(start=100, end=102):
    return {"start": start, "end": end, "summary": "Car exterior and reaction",
            "source_path": "/private/original.mp4", "source_fingerprint": "abc",
            "evidence": {"verified": True, "subject_view": "recognizable",
                         "subject_frames": [start + .4], "reaction_frames": [end - .5],
                         "confidence": .95}}


def words(transcript):
    return [(word["word"].strip(), word["start"], word["end"])
            for segment in transcript["segments"] for word in segment["words"]]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    clean, output = tmp_path / "clean.mp4", tmp_path / "new.mp4"
    clean.write_bytes(b"original")
    state = {"duration": 4.168, "moment": verified(), "search": [], "renders": []}
    transcript = {"language": "en", "segments": [{"words": [
        {"word": " before", "start": 10.2, "end": 10.6},
        {"word": " after", "start": 13.2, "end": 13.6},
        {"word": " reveal", "start": 100.2, "end": 100.6},
        {"word": " wow", "start": 101.2, "end": 101.6}]}]}
    clip = {"start": 10, "end": 14, "recipe": {"v": 1, "segments": [{"start": 10, "end": 14}],
            "canonical_range": {"start": 10, "end": 14}, "other": "keep"},
            "caption_words": [{"word": " corrected", "start": .2, "end": .6},
                              {"word": " ending", "start": 3.2, "end": 3.6}]}
    operation = {"type": "source_insert", "start": 2, "end": 4, "query": "Show the actual car and reaction",
                 "preferred_region": "end"}
    def probe(path):
        return {"duration": state["duration"] if Path(path) == clean else state["record"]["new_duration"] + state.get("output_delta", 0),
                "width": 160, "height": 240, "fps": 10, "has_audio": True}
    def search(*args, **kwargs):
        state["search"].append((args, kwargs))
        return copy.deepcopy(state["moment"])
    def render(input_path, output_path, *, source_path, moment, at, mode, info):
        state["renders"].append({"moment": moment, "at": at, "mode": mode})
        record = source_edit.build_timeline(info["duration"], moment, at, mode)
        record["layout_ranges"] = [{"start": at, "end": at + record["duration"], "mode": "wide"}]
        state["record"] = record
        Path(output_path).write_bytes(b"edited")
        Path(str(output_path) + ".layout.json").write_text("{}")
        return record
    monkeypatch.setattr(source_edit, "probe", probe)
    monkeypatch.setattr(source_edit, "apply_source_edit", render)
    monkeypatch.setattr(ase.source_moments, "find_source_moment", search)
    def run(**changes):
        kwargs = dict(source_path=tmp_path / "source.mp4", transcript=transcript,
                      current_segments=[{"start": 10, "end": 14}], operation=operation,
                      current_clip=clip, playhead_time=2, cache_dir=tmp_path / "cache")
        kwargs.update(changes)
        return ase.render_source_operation(clean, output, **kwargs)
    return state, transcript, clip, operation, run, output


def test_insert_preserves_edited_words_canonical_range_and_search_cache_identity(setup):
    state, transcript, clip, operation, run, _ = setup
    original = copy.deepcopy((transcript, clip, operation))
    result = run()
    assert words(result["transcript"]) == [("corrected", .2, .6), ("reveal", 2.2, 2.6),
                                            ("wow", 3.2, 3.6), ("ending", 5.2, 5.6)]
    updates = result["clip_updates"]
    assert updates["duration_seconds"] == pytest.approx(6.168)
    assert updates["recipe"]["segments"] == [{"start": 10, "end": 12}, {"start": 100, "end": 102},
                                                {"start": 12, "end": 14.168}]
    assert updates["recipe"]["canonical_range"] == {"start": 10, "end": 14}
    assert updates["recipe"]["other"] == "keep"
    assert updates["caption_words"][-1]["start"] == 5.2
    assert state["search"][0][1]["current_segments"] == [{"start": 10, "end": 14}]
    assert "source_path" not in result["source_moment"]
    assert len(updates["source_edits"]) == 1
    assert (transcript, clip, operation) == original


def test_append_uses_actual_duration_and_records_actual_encoded_length(setup):
    state, _, _, operation, run, _ = setup
    operation["type"] = "source_append"
    state["output_delta"] = .021
    result = run()
    assert state["renders"][0]["at"] == pytest.approx(4.168)
    assert result["record"]["new_duration"] == pytest.approx(6.189)
    assert result["clip_updates"]["source_edits"][-1]["new_duration"] == pytest.approx(6.189)
    assert words(result["transcript"])[2] == ("reveal", 4.368, 4.768)


def test_codec_tail_does_not_add_unheard_words_on_later_caption_restyles(setup):
    _, transcript, clip, operation, run, _ = setup
    clip.pop("caption_words")
    transcript["segments"][0]["words"].append({"word": " unheard", "start": 14.02, "end": 14.15})
    operation["type"] = "source_append"
    result = run()
    saved = result["clip_updates"]["caption_words"]
    assert "unheard" not in [word["word"].strip() for word in saved]
    assert [word["word"].strip() for word in saved] == ["before", "after", "reveal", "wow"]
    assert result["clip_updates"]["source_visuals"] == [{
        "source_start": 100, "source_end": 102, "picture_start": 100, "picture_end": 102}]


def test_cutaway_cannot_discard_verified_reaction_to_fit_short_range(setup):
    state, _, _, operation, run, _ = setup
    operation.update(type="source_cutaway", start=3, end=4)
    with pytest.raises(ValueError, match="longer range.*append"):
        run()
    assert not state["renders"]


def test_cutaway_keeps_current_spoken_recipe_and_captions(setup):
    _, _, _, operation, run, _ = setup
    operation.update(type="source_cutaway", start=1, end=3)
    result = run()
    assert words(result["transcript"]) == [("corrected", .2, .6), ("ending", 3.2, 3.6)]
    assert result["clip_updates"]["recipe"]["segments"] == [{"start": 10, "end": 14.168}]


def test_insert_snaps_nearby_word_end_instead_of_splitting_edited_speech(setup):
    state, _, _, operation, run, _ = setup
    operation.update(start=.5, end=2.5)
    result = run()
    assert state["renders"][0]["at"] == .6
    assert words(result["transcript"])[0] == ("corrected", .2, .6)
    assert words(result["transcript"])[1] == ("reveal", .8, 1.2)


def test_insert_rejects_middle_of_long_word(setup):
    state, _, clip, operation, run, _ = setup
    clip["caption_words"] = [{"word": "long", "start": 0, "end": 2}]
    operation.update(start=1, end=3)
    with pytest.raises(ValueError, match="spoken word"):
        run()
    assert not state["search"]


def test_recipe_discrepancy_is_not_silently_stretched(setup):
    state, _, _, _, run, _ = setup
    state["duration"] = 5
    with pytest.raises(ValueError, match="no longer matches"):
        run()
    assert not state["search"]


@pytest.mark.parametrize("evidence", [None, [], {"verified": False},
    {"verified": True, "subject_frames": [], "reaction_frames": [], "confidence": .95},
    {"verified": True, "subject_frames": [99], "reaction_frames": [], "confidence": .95}])
def test_unverified_or_outside_evidence_never_renders(setup, evidence):
    state, _, _, _, run, _ = setup
    state["moment"]["evidence"] = evidence
    with pytest.raises(ValueError):
        run()
    assert not state["renders"]


def test_missing_transcript_still_allows_verified_visual_scene(setup):
    _, _, clip, _, run, _ = setup
    clip.pop("caption_words")
    result = run(transcript=None)
    assert words(result["transcript"]) == []
    assert "caption_words" not in result["clip_updates"]


def test_unexpected_encoded_timing_cleans_new_output_only(setup):
    state, _, _, _, run, output = setup
    state["output_delta"] = .5
    with pytest.raises(ValueError, match="unexpected timing"):
        run()
    assert not output.exists() and not Path(str(output) + ".layout.json").exists()
    assert (output.parent / "clean.mp4").read_bytes() == b"original"


def test_partial_source_word_trims_without_losing_any_verified_frame(setup):
    state, transcript, _, operation, run, _ = setup
    state["moment"] = verified(936.864, 946.485)
    state["moment"]["evidence"]["reaction_frames"] = [945.485]
    transcript["segments"][0]["words"].append({"word": " Dude", "start": 946.36, "end": 946.88})
    operation.update(type="source_append", start=0, end=8)
    result = run()
    moment = result["source_moment"]
    assert moment["end"] == 946.36
    assert moment["verified_range"] == {"start": 936.864, "end": 946.485}
    assert "Dude" not in [word[0] for word in words(result["transcript"])]
    assert result["clip_updates"]["source_edits"][-1]["source_moment"] == moment


def test_source_word_trim_keeps_reaction_evidence_when_trim_would_remove_it(setup):
    state, transcript, _, operation, run, _ = setup
    state["moment"] = verified(100, 102)
    state["moment"]["evidence"]["reaction_frames"] = [101.95]
    transcript["segments"][0]["words"].append({"word": " last", "start": 101.9, "end": 102.2})
    operation["type"] = "source_append"
    result = run()
    assert result["source_moment"]["end"] == 102
    assert "verified_range" not in result["source_moment"]


def test_real_media_duration_and_source_recipe_stay_in_sync(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    clean, source, output = [tmp_path / name for name in ("clean.mp4", "source.mp4", "out.mp4")]
    for path, color, size, length in [(clean, "blue", "160x240", 1), (source, "red", "320x180", 3)]:
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        f"color=c={color}:s={size}:r=10:d={length}", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={length}", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    original = clean.read_bytes()
    moment = verified(.2, 1.2)
    monkeypatch.setattr(ase.source_moments, "find_source_moment", lambda *args, **kwargs: moment)
    transcript = {"segments": [{"words": [{"word": " original", "start": 2.1, "end": 2.4},
                                           {"word": " source", "start": .4, "end": .8}]}]}
    result = ase.render_source_operation(clean, output, source_path=source, transcript=transcript,
        current_segments=[{"start": 2, "end": 3}], current_clip={"start": 2, "end": 3},
        operation={"type": "source_append", "start": 0, "end": 1, "query": "Show the car"})
    actual = source_edit.probe(output)["duration"]
    assert result["clip_updates"]["duration_seconds"] == actual
    assert actual == pytest.approx(2, abs=.06)
    assert result["clip_updates"]["recipe"]["segments"] == [{"start": 2, "end": 3}, {"start": .2, "end": 1.2}]
    assert words(result["transcript"]) == [("original", .1, .4), ("source", 1.2, 1.6)]
    assert clean.read_bytes() == original
