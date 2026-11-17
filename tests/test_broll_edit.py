"""Cached B-roll survives trimming, reordering, repetition and reframing."""
from pathlib import Path

import pytest

import broll
from broll_edit import reapply_broll, remap_cues


def cue(start=104, end=107, asset="broll_assets_saved/image.jpg", **kwargs):
    return {"source_start": start, "source_end": end, "start": start - 100,
            "end": end - 100, "duration": end - start, "asset": asset,
            "prompt": "A cinematic view of a mountain", "anchor": "mountain",
            "timing": "word", **kwargs}


class TestRemapCues:
    def test_rebases_original_source_times(self):
        result = remap_cues([cue()], [{"start": 100, "end": 115}])
        assert len(result) == 1
        assert (result[0]["start"], result[0]["end"], result[0]["duration"]) == (4, 7, 3)
        assert result[0]["source_start"] == 104

    def test_trims_both_cue_edges(self):
        result = remap_cues([cue()], [{"start": 105, "end": 106}])
        assert [(r["start"], r["end"], r["source_start"], r["source_end"])
                for r in result] == [(0, 1, 105, 106)]

    def test_reordered_source_segments_follow_edit_order(self):
        result = remap_cues([cue(), cue(114, 116)], [
            {"start": 112, "end": 120}, {"start": 100, "end": 110}])
        assert [(r["start"], r["end"], r["source_start"])
                for r in result] == [(2, 4, 114), (12, 15, 104)]

    def test_repeated_source_range_repeats_saved_visual(self):
        result = remap_cues([cue()], [{"start": 100, "end": 110}] * 2)
        assert [(r["start"], r["end"]) for r in result] == [(4, 7), (14, 17)]

    def test_manual_images_stay_on_their_selected_source_occurrences(self):
        cues = [cue(0, 1, asset="red.jpg", mode="manual", id="first", source_occurrence=0),
                cue(0, 1, asset="blue.jpg", mode="manual", id="second", source_occurrence=1)]
        result = remap_cues(cues, [{"start": 0, "end": 5}] * 2)
        assert [(item["id"], item["start"], item["end"]) for item in result] == [
            ("first", 0, 1), ("second", 5, 6)]

    def test_duplicate_metadata_does_not_multiply_visuals(self):
        result = remap_cues([cue(), cue()], [{"start": 100, "end": 110}] * 2)
        assert len(result) == 2

    def test_drops_fragments_under_half_a_second(self):
        assert remap_cues([cue()], [{"start": 106.6, "end": 110}]) == []
        result = remap_cues([cue()], [{"start": 106.5, "end": 110}])
        assert result[0]["duration"] == 0.5

    def test_gap_between_segments_has_no_phantom_cutaway(self):
        assert remap_cues([cue()], [{"start": 100, "end": 103},
                                    {"start": 108, "end": 115}]) == []

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, None, True, "bad"])
    def test_invalid_source_cue_bounds_are_ignored(self, value):
        invalid = {**cue(), "source_start": value}
        assert remap_cues([invalid], [{"start": 100, "end": 110}]) == []

    @pytest.mark.parametrize("segment", [None, {}, {"start": True, "end": 110},
        {"start": 110, "end": 100}, {"start": -1, "end": 110},
        {"start": 100, "end": float("inf")}, {"start": float("nan"), "end": 110}])
    def test_invalid_segments_do_not_shift_later_placements(self, segment):
        with pytest.raises(ValueError):
            remap_cues([cue()], [segment, {"start": 100, "end": 110}])


@pytest.fixture
def saved(tmp_path, monkeypatch):
    from PIL import Image
    image = tmp_path / "broll_assets_saved" / "image.jpg"
    image.parent.mkdir()
    Image.new("RGB", (16, 16), "red").save(image)
    video = tmp_path / "recut_clip.mp4"
    video.write_bytes(b"clean-source-video")
    calls = []

    def render(input_path, output_path, cues, info=None):
        calls.append((input_path, output_path, cues))
        Path(output_path).write_bytes(b"video-with-cached-broll")

    monkeypatch.setattr(broll, "render_cutaways", render)

    return video, image, calls


def test_reapply_uses_verified_cached_asset_and_keeps_clean_source(saved):
    video, image, calls = saved
    record = {"enabled": True, "status": "applied", "cues": [cue()]}
    path, updated = reapply_broll(video, record, [{"start": 100, "end": 110}])
    assert path != str(video)
    assert Path(path).name.startswith("broll_")
    assert updated["status"] == "applied"
    assert updated["file"] == Path(path).name
    assert calls[0][2][0]["path"] == str(image)
    assert "path" not in updated["cues"][0]
    assert video.read_bytes() == b"clean-source-video"
    assert record["cues"][0]["start"] == 4  # input metadata remains untouched


def test_source_definitions_survive_trim_then_extension(saved):
    video, _, calls = saved
    record = {"cues": [cue()]}
    _, trimmed = reapply_broll(video, record, [{"start": 105, "end": 106}])
    assert trimmed["cues"][0]["source_start"] == 105
    assert trimmed["source_cues"][0]["source_start"] == 104
    _, extended = reapply_broll(video, trimmed, [{"start": 100, "end": 110}])
    assert extended["cues"][0]["start"] == 4
    assert extended["cues"][0]["duration"] == 3
    assert len(calls) == 2


def test_manual_short_placement_survives_recuts_and_refreshes_clean_base(saved):
    video, _, calls = saved
    record = {"mode": "manual", "media_provider": "manual", "base_file": "old.mp4",
              "cues": [cue(104, 104.2, id="user-placement", mode="manual")]}
    _, updated = reapply_broll(video, record, [{"start": 104, "end": 108}])
    assert updated["mode"] == updated["media_provider"] == "manual"
    assert updated["base_file"] == video.name
    assert updated["cues"][0]["id"] == "user-placement"
    assert updated["cues"][0]["start"] == 0
    assert updated["cues"][0]["duration"] == .2
    assert calls[0][2][0]["mode"] == "manual"


def test_trim_that_removes_broll_does_not_lose_original_cues(saved):
    video, _, calls = saved
    path, updated = reapply_broll(video, {"cues": [cue()]},
                                 [{"start": 108, "end": 110}])
    assert path == str(video) and updated["status"] == "skipped"
    assert updated["cues"] == [] and len(updated["source_cues"]) == 1
    assert calls == []


def test_missing_one_asset_produces_partial_result(saved):
    video, _, calls = saved
    record = {"cues": [cue(), cue(108, 110, asset="broll_assets_saved/missing.jpg")]}
    _, updated = reapply_broll(video, record, [{"start": 100, "end": 112}])
    assert updated["status"] == "partial" and updated["failed_cues"] == 1
    assert len(updated["cues"]) == 1 and len(calls[0][2]) == 1


@pytest.mark.parametrize("asset", ["../outside.jpg", "/tmp/outside.jpg",
    "broll_assets_saved/../../outside.jpg", "broll_assets_saved", "missing.jpg", "bad\x00.jpg"])
def test_unsafe_or_missing_assets_never_reach_renderer(saved, asset):
    video, _, calls = saved
    path, updated = reapply_broll(video, {"cues": [cue(asset=asset)]},
                                 [{"start": 100, "end": 110}])
    assert path == str(video) and updated["status"] == "failed"
    assert updated["failed_cues"] == 1 and calls == []


def test_symlink_escape_is_rejected(saved, tmp_path):
    video, image, calls = saved
    outside = tmp_path.parent / f"{tmp_path.name}_outside.jpg"
    outside.write_bytes(image.read_bytes())
    link = tmp_path / "broll_assets_saved" / "escaped.jpg"
    link.symlink_to(outside)
    try:
        path, updated = reapply_broll(video, {"cues": [cue(asset=str(link.relative_to(tmp_path)))]},
                                     [{"start": 100, "end": 110}])
        assert path == str(video) and updated["status"] == "failed" and calls == []
    finally:
        outside.unlink(missing_ok=True)


def test_corrupted_image_is_rejected(saved):
    video, image, calls = saved
    image.write_bytes(b"this-is-not-an-image")
    path, updated = reapply_broll(video, {"cues": [cue()]}, [{"start": 100, "end": 110}])
    assert path == str(video) and updated["status"] == "failed" and calls == []


def test_renderer_failure_cleans_derivative_and_preserves_original(saved, monkeypatch):
    video, _, _ = saved
    outputs = []

    def fail(input_path, output_path, cues):
        outputs.append(output_path)
        Path(output_path).write_bytes(b"partial-output")
        raise RuntimeError("secret-provider-details-must-not-be-stored")

    monkeypatch.setattr(broll, "render_cutaways", fail)
    path, updated = reapply_broll(video, {"cues": [cue()]}, [{"start": 100, "end": 110}])
    assert path == str(video) and updated["status"] == "failed" and updated["cues"] == []
    assert "secret-provider-details" not in str(updated)
    assert not Path(outputs[0]).exists()
    assert video.read_bytes() == b"clean-source-video"


def test_previous_generation_failures_remain_honest_without_accumulation(saved):
    video, _, _ = saved
    record = {"cues": [cue()], "failed_cues": 1, "status": "partial"}
    _, updated = reapply_broll(video, record, [{"start": 100, "end": 110}])
    assert updated["status"] == "partial" and updated["failed_cues"] == 1
    _, again = reapply_broll(video, updated, [{"start": 100, "end": 110}])
    assert again["failed_cues"] == 1


def test_recut_burns_captions_and_watermark_after_cached_visuals(saved):
    import recut
    video, _, _ = saved
    events = []

    def runner(command):
        Path(command[-1]).write_bytes(b"clean-source-video")

    def visuals(clean_path):
        path, _ = reapply_broll(clean_path, {"cues": [cue()]},
                               [{"start": 100, "end": 110}])
        events.append(("visuals", Path(path).name))
        return path

    def watermark(path):
        assert Path(path).read_bytes() == b"video-with-cached-broll"
        events.append(("watermark", Path(path).name))

    def caption(path, transcript, start, end):
        assert Path(path).read_bytes() == b"video-with-cached-broll"
        events.append(("captions", Path(path).name))
        out = Path(path).with_name(f"subtitled_1_{Path(path).name}")
        out.write_bytes(b"captioned-video-with-broll")
        return str(out)

    served, clean = recut.perform_recut(
        input_path=str(video), segments=[{"start": 0, "end": 10}],
        output_dir=str(video.parent), clean_name="clip_1.mp4", runner=runner,
        visual_editor=visuals, watermark=True, watermarker=watermark,
        captions_transcript={"segments": [{"text": "mountain"}]}, captioner=caption)
    assert [kind for kind, _ in events] == ["visuals", "watermark", "captions"]
    assert events[0][1] == events[1][1] == events[2][1]
    assert served.startswith("subtitled_1_broll_")
    assert clean.startswith("recut_") and "broll_" not in clean
    assert (video.parent / clean).read_bytes() == b"clean-source-video"


@pytest.mark.parametrize("record", [None, {}, {"cues": []}, {"status": "failed"}])
def test_no_cues_returns_original_without_rendering(saved, record):
    video, _, calls = saved
    path, updated = reapply_broll(video, record, [{"start": 100, "end": 110}])
    assert path == str(video) and updated["status"] == "skipped" and calls == []
