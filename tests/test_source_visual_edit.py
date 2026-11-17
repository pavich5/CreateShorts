"""Source pictures survive trims and reordered speech without another search."""
from pathlib import Path

import pytest

import source_visual_edit as visuals


def cue(start=10, end=14, picture=60):
    return {"source_start": start, "source_end": end,
            "picture_start": picture, "picture_end": picture + end - start}


def record(mode="cutaway", at=2, duration=4):
    return {"mode": mode, "at": at, "duration": duration}


def test_cutaway_anchors_picture_through_noncontiguous_current_segments():
    result = visuals.make_source_visuals({}, record(),
        [{"start": 10, "end": 14}, {"start": 30, "end": 35}],
        {"start": 60, "end": 64})
    assert result == [cue(12, 14, 60), cue(30, 32, 62)]


@pytest.mark.parametrize("mode", ["insert", "append"])
def test_insert_and_append_anchor_to_the_new_source_speech(mode):
    result = visuals.make_source_visuals({}, record(mode, 6),
        [{"start": 10, "end": 16}], {"start": 60, "end": 64})
    assert result == [cue(60, 64, 60)]


def test_new_picture_replaces_overlap_and_retains_each_old_remainder():
    result = visuals.make_source_visuals({"source_visuals": [cue(10, 18, 60)]},
        record(at=2), [{"start": 10, "end": 18}],
        {"start": 80, "end": 84})
    assert result == [cue(10, 12, 60), cue(12, 16, 80), cue(16, 18, 66)]


def test_existing_cues_are_preserved_without_mutating_clip():
    old = cue()
    clip = {"source_visuals": [old]}
    result = visuals.make_source_visuals(clip, record("append", 6),
        [{"start": 10, "end": 16}], {"start": 80, "end": 84})
    assert result == [old, cue(80, 84, 80)]
    assert clip == {"source_visuals": [cue()]}


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    original, source = tmp_path / "recut.mp4", tmp_path / "source.mp4"
    original.write_bytes(b"current-audio-and-picture")
    source.write_bytes(b"full-original")
    calls = []
    monkeypatch.setattr(visuals.source_edit, "probe", lambda path:
        {"duration": 100 if Path(path) == source else 6})

    def render(input_path, output_path, **kwargs):
        calls.append({"input": input_path, "output": str(output_path), **kwargs})
        Path(output_path).write_bytes(b"same-audio-restored-picture")
        Path(str(output_path) + ".layout.json").write_text("[]")
    monkeypatch.setattr(visuals.source_edit, "apply_source_edit", render)
    return original, source, calls


def test_reapply_maps_reordered_repeated_segments_and_preserves_audio(renderer):
    original, source, calls = renderer
    result = visuals.reapply_source_visuals(original, source, [cue()], [
        {"start": 12, "end": 14}, {"start": 30, "end": 32},
        {"start": 10, "end": 12}])
    assert [(call["at"], call["moment"]) for call in calls] == [
        (0, {"start": 62, "end": 64}), (4, {"start": 60, "end": 62})]
    assert all(call["mode"] == "cutaway" and call["source_path"] == source for call in calls)
    assert calls[1]["input"] == calls[0]["output"]
    assert result == calls[-1]["output"]
    assert Path(result).is_file()
    assert not Path(calls[0]["output"]).exists()
    assert not Path(calls[0]["output"] + ".layout.json").exists()
    assert original.read_bytes() == b"current-audio-and-picture"
    assert source.read_bytes() == b"full-original"


def test_small_but_visible_slice_is_preserved(renderer):
    original, source, calls = renderer
    visuals.reapply_source_visuals(original, source, [cue()], [
        {"start": 13.8, "end": 19.8}])
    assert calls[0]["moment"] == {"start": 63.8, "end": 64}


def test_fractional_frame_remnant_is_omitted_without_needing_source(renderer):
    original, _, calls = renderer
    result = visuals.reapply_source_visuals(original, None, [cue()], [
        {"start": 13.95, "end": 19.95}])
    assert result == str(original) and calls == []


def test_missing_source_fails_for_active_saved_visual(renderer):
    original, _, _ = renderer
    with pytest.raises(ValueError, match="original source"):
        visuals.reapply_source_visuals(original, None, [cue()], [{"start": 10, "end": 16}])


def test_source_bounds_are_checked_before_render(renderer):
    original, source, calls = renderer
    with pytest.raises(ValueError, match="past the retained"):
        visuals.reapply_source_visuals(original, source, [cue(picture=99)],
                                      [{"start": 10, "end": 16}])
    assert calls == []


def test_timeline_mismatch_fails_instead_of_losing_visuals(renderer):
    original, source, calls = renderer
    with pytest.raises(ValueError, match="timing"):
        visuals.reapply_source_visuals(original, source, [cue()], [{"start": 10, "end": 30}])
    assert calls == []


def test_renderer_failure_cleans_outputs_without_swallowing_error(renderer, monkeypatch):
    original, source, calls = renderer
    original_render = visuals.source_edit.apply_source_edit
    def fail_second(*args, **kwargs):
        original_render(*args, **kwargs)
        if len(calls) == 2:
            raise RuntimeError("source decode failed")
    monkeypatch.setattr(visuals.source_edit, "apply_source_edit", fail_second)
    with pytest.raises(RuntimeError, match="source decode failed"):
        visuals.reapply_source_visuals(original, source, [cue()], [
            {"start": 10, "end": 13}, {"start": 10, "end": 13}])
    assert not list(original.parent.glob("source_visual_*"))
    assert original.read_bytes() == b"current-audio-and-picture"


@pytest.mark.parametrize("bad", [None, [], "cue", {"source_start": float("nan")},
    {**cue(), "source_start": True}, {**cue(), "picture_start": -1},
    {**cue(), "picture_end": 65}, {**cue(), "source_end": 40, "picture_end": 90}])
def test_invalid_saved_cues_never_become_render_inputs(renderer, bad):
    original, source, calls = renderer
    with pytest.raises(ValueError):
        visuals.reapply_source_visuals(original, source, [bad], [{"start": 10, "end": 16}])
    assert calls == []


def test_unknown_metadata_fields_cannot_supply_a_media_path(renderer):
    original, source, calls = renderer
    supplied = {**cue(), "path": "/etc/passwd", "asset": "../../outside"}
    visuals.reapply_source_visuals(original, source, [supplied], [{"start": 10, "end": 16}])
    assert calls[0]["source_path"] == source
    assert calls[0]["moment"] == {"start": 60, "end": 64}


def test_append_anchors_cannot_exceed_the_rendered_moment():
    with pytest.raises(ValueError, match="outside"):
        visuals.make_source_visuals({}, record("append", 6, 5),
            [{"start": 10, "end": 16}], {"start": 60, "end": 64})


def test_new_overlapping_saved_metadata_uses_the_latest_picture():
    assert visuals._cues([cue(10, 18, 60), cue(12, 16, 80)]) == [
        cue(10, 12, 60), cue(12, 16, 80), cue(16, 18, 66)]
