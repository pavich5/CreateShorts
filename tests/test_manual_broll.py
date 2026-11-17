import io
from pathlib import Path

import pytest

import manual_broll as manual


def image_bytes(color="red", mode="RGB"):
    from PIL import Image
    output = io.BytesIO()
    Image.new(mode, (120, 80), color).save(output, "PNG")
    return output.getvalue()


def test_upload_normalizes_pixels_and_keeps_same_asset_for_same_image(tmp_path):
    from PIL import Image
    data = image_bytes((255, 0, 0, 90), "RGBA")
    asset = manual.save_image(tmp_path, 0, data)
    assert manual.ASSET_RE.fullmatch(asset)
    assert asset == manual.save_image(tmp_path, 0, data)
    with Image.open(tmp_path / asset) as saved:
        assert saved.format == "PNG" and saved.getpixel((10, 10)) == (255, 0, 0, 90)
        assert not saved.info


def test_photo_orientation_is_applied_and_private_metadata_is_removed(tmp_path):
    from PIL import Image
    image = Image.new("RGB", (120, 80), "red")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "Private photo information"
    data = io.BytesIO()
    image.save(data, "JPEG", exif=exif)
    asset = manual.save_image(tmp_path, 0, data.getvalue())
    with Image.open(tmp_path / asset) as normalized:
        assert normalized.size == (80, 120)
        assert not normalized.info and not normalized.getexif()


@pytest.mark.parametrize("data", [b"", b"not an image", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"])
def test_invalid_upload_does_not_create_assets(tmp_path, data):
    with pytest.raises(manual.ManualBrollError):
        manual.save_image(tmp_path, 0, data)
    assert not list(tmp_path.glob("broll_assets_*"))


def test_upload_limit_precedes_image_decode(tmp_path):
    with pytest.raises(manual.ManualBrollError, match="20 MB"):
        manual.save_image(tmp_path, 0, b"x" * (manual.MAX_IMAGE_BYTES + 1))


def test_missing_optional_image_plugin_is_a_validation_error(tmp_path, monkeypatch):
    from PIL import Image

    def missing_plugin(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'pi_heif'")

    # Ultralytics wraps Pillow and tries this optional plugin on invalid bytes.
    monkeypatch.setattr(Image, "open", missing_plugin)
    with pytest.raises(manual.ManualBrollError, match="valid JPG"):
        manual.save_image(tmp_path, 0, b"invalid image bytes")
    assert not list(tmp_path.glob("broll_assets_*"))


@pytest.mark.parametrize("bounds", [(-1, 2), (0, 0), (True, 2), (0, float("nan")),
                                    (0, float("inf")), (0, 10.1), (2, 1), ("0", 2)])
def test_placement_bounds_are_finite_real_clip_seconds(tmp_path, bounds):
    asset = manual.save_image(tmp_path, 0, image_bytes())
    with pytest.raises(manual.ManualBrollError):
        manual.validate_placements([{"asset": asset, "start": bounds[0], "end": bounds[1]}], 10, tmp_path)


def test_manual_timing_can_cover_the_first_and_final_frames_without_ai_limits(tmp_path):
    asset = manual.save_image(tmp_path, 0, image_bytes())
    result = manual.validate_placements([{"asset": asset, "start": 0, "end": .1},
                                         {"asset": asset, "start": 9, "end": 10}], 10, tmp_path)
    assert [(item["start"], item["end"]) for item in result] == [(0, .1), (9, 10)]
    assert all(item["mode"] == "manual" and item["motion"] is False for item in result)


def test_fifty_millisecond_placement_accepts_floating_point_rounding(tmp_path):
    asset = manual.save_image(tmp_path, 0, image_bytes())
    result = manual.validate_placements([{"asset": asset, "start": .1, "end": .15}], 10, tmp_path)
    assert result[0]["duration"] == .05


def test_placements_allow_overlap_but_do_not_reuse_identifiers(tmp_path):
    asset = manual.save_image(tmp_path, 0, image_bytes())
    overlapping = manual.validate_placements([{"asset": asset, "start": 0, "end": 3},
                                     {"asset": asset, "start": 2, "end": 4}], 10, tmp_path)
    assert len(overlapping) == 2
    with pytest.raises(manual.ManualBrollError, match="identifier"):
        manual.validate_placements([{"id": "same", "asset": asset, "start": 0, "end": 1},
                                     {"id": "same", "asset": asset, "start": 2, "end": 3}], 10, tmp_path)


@pytest.mark.parametrize("asset", ["../outside.png", "/tmp/outside.png", "missing.png",
                                  "broll_assets_123456abcdef/../../outside.png"])
def test_assets_must_be_images_in_this_project(tmp_path, asset):
    with pytest.raises(manual.ManualBrollError):
        manual.asset_path(tmp_path, asset)


def test_asset_symlink_cannot_escape_project(tmp_path):
    asset = manual.save_image(tmp_path, 0, image_bytes())
    path = tmp_path / asset
    path.unlink()
    outside = tmp_path.parent / (tmp_path.name + "_outside.png")
    outside.write_bytes(image_bytes())
    try:
        path.symlink_to(outside)
        with pytest.raises(manual.ManualBrollError):
            manual.asset_path(tmp_path, asset)
    finally:
        outside.unlink()


def test_image_across_edit_seam_retains_source_intervals_and_one_visible_placement():
    placed = [{"id": "image1", "placement_id": "image1", "asset": "example.png",
               "start": 2, "end": 6, "mode": "manual"}]
    saved = manual.source_cues(placed, [{"start": 100, "end": 104}, {"start": 120, "end": 124}])
    assert [(item["source_start"], item["source_end"]) for item in saved] == [(102, 104), (120, 122)]
    assert manual.displayed_placements({"mode": "manual", "cues": saved}) == [
        {"id": "image1", "asset": "example.png", "start": 2, "end": 6,
         **manual.TRANSFORM_DEFAULTS}]


def test_old_ai_images_are_not_presented_as_user_uploads():
    assert manual.displayed_placements({"mode": "stock", "cues": [{"asset": "old.jpg"}]}) == []


def test_repeated_source_occurrences_keep_independent_manual_placements():
    placed = [{"id": "red", "asset": "red.png", "start": 0, "end": 1, "mode": "manual"},
              {"id": "blue", "asset": "blue.png", "start": 5, "end": 6, "mode": "manual"}]
    saved = manual.source_cues(placed, [{"start": 0, "end": 5}, {"start": 0, "end": 5}])
    assert [(item["asset"], item["source_start"], item["source_end"], item["source_occurrence"])
            for item in saved] == [("red.png", 0, 1, 0), ("blue.png", 0, 1, 1)]


def test_partial_repetition_splits_at_occurrence_boundaries():
    saved = manual.source_cues([{"asset": "image.png", "start": 3, "end": 6, "mode": "manual"}],
                              [{"start": 1, "end": 4}, {"start": 0, "end": 3}])
    assert [(item["source_start"], item["source_end"], item["source_occurrence"])
            for item in saved] == [(0, 1, 0), (1, 3, 1)]
