"""Image controls must survive edits and match the pixels delivered to users."""
import io
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

import broll
import broll_edit
import manual_broll as manual


TRANSFORM = {"x": 25.0, "y": 25.0, "width": 50.0, "height": 50.0,
             "fit": "contain", "opacity": .7, "background": "none"}
HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


@pytest.mark.parametrize("field,value", [
    ("x", -1), ("y", 100.1), ("width", 0), ("height", .09),
    ("width", 101), ("x", True), ("height", "40"),
    ("opacity", float("nan")), ("x", float("inf")), ("opacity", -.1),
    ("opacity", 1.01), ("fit", "invalid"), ("background", "invalid"),
])
def test_invalid_controls_cannot_reach_the_renderer(field, value):
    with pytest.raises(manual.ManualBrollError):
        manual.image_transform({**TRANSFORM, field: value})


def test_image_bounds_are_checked_as_a_box_and_allow_the_last_pixel():
    for transform in ({"x": 60, "width": 41}, {"y": 90, "height": 11}):
        with pytest.raises(manual.ManualBrollError):
            manual.image_transform({**TRANSFORM, **transform})
    assert manual.image_transform({"x": 99.9, "y": 99.9, "width": .1, "height": .1})["x"] == 99.9
    assert manual.image_transform({}) == manual.TRANSFORM_DEFAULTS


def test_geometry_roundtrips_across_an_edit_seam_and_a_later_trim(tmp_path):
    encoded = io.BytesIO()
    Image.new("RGB", (80, 20), "red").save(encoded, "PNG")
    asset = manual.save_image(tmp_path, 0, encoded.getvalue())
    placed = manual.validate_placements([
        {"id": "chosen", "asset": asset, "start": 2, "end": 6, **TRANSFORM},
    ], 8, tmp_path)
    assert manual.image_transform(placed[0]) == TRANSFORM
    source = manual.source_cues(placed, [{"start": 100, "end": 104},
                                        {"start": 120, "end": 124}])
    displayed = manual.displayed_placements({"mode": "manual", "cues": source})
    assert displayed == [{"id": "chosen", "asset": asset, "start": 2, "end": 6, **TRANSFORM}]
    trimmed = broll_edit.remap_cues(source, [{"start": 103, "end": 104},
                                            {"start": 120, "end": 123}])
    assert [(cue["start"], cue["end"]) for cue in trimmed] == [(0, 1), (1, 3)]
    assert all(manual.image_transform(cue) == TRANSFORM for cue in trimmed)


@pytest.fixture
def source_video(tmp_path, monkeypatch):
    if not HAS_FFMPEG:
        pytest.skip("FFmpeg unavailable")
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    path = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=160x120:r=10:d=2", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def _frame(path, at=1):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
                          "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return Image.frombytes("RGB", (160, 120), raw)


def _pixel(image, x, y):
    # Sample away from boundaries and tolerate normal H.264 quantization.
    pixels = [image.getpixel((left, top))
              for left in range(x - 2, x + 2) for top in range(y - 2, y + 2)]
    return tuple(sum(pixel[channel] for pixel in pixels) / len(pixels) for channel in range(3))


def _blue(pixel):
    assert pixel[0] < 15 and pixel[1] < 15 and pixel[2] > 235


def _audio_hash(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                           "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"],
                          capture_output=True, check=True).stdout


@pytest.mark.parametrize("fit", ["contain", "cover", "stretch"])
def test_image_fit_changes_only_the_selected_box_and_preserves_audio(tmp_path, source_video, fit):
    image = Image.new("RGB", (80, 20), "green")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 19, 19), fill="red")
    draw.rectangle((60, 0, 79, 19), fill="yellow")
    asset, output = tmp_path / "wide.png", tmp_path / (fit + ".mp4")
    image.save(asset)
    cue = {"path": str(asset), "mode": "manual", "start": .5, "end": 1.5, "duration": 1,
           "x": 25, "y": 25, "width": 50, "height": 50,
           "fit": fit, "background": "none"}
    broll.render_cutaways(str(source_video), str(output), [cue])
    frame = _frame(output)
    for point in ((10, 10), (130, 60), (80, 100)):
        _blue(_pixel(frame, *point))
    if fit == "contain":
        _blue(_pixel(frame, 80, 35))
        assert _pixel(frame, 45, 60)[0] > 235
    elif fit == "cover":
        assert _pixel(frame, 45, 35)[1] > 100
        assert _pixel(frame, 45, 60)[0] < 15
    else:
        assert _pixel(frame, 45, 35)[0] > 235
        assert _pixel(frame, 115, 60)[0] > 235
    _blue(_pixel(_frame(output, .2), 80, 60))
    _blue(_pixel(_frame(output, 1.7), 80, 60))
    assert broll._probe_video(str(output))["duration"] == pytest.approx(2, abs=.1)
    assert _audio_hash(source_video) == _audio_hash(output)


@pytest.mark.parametrize("opacity", [0, .5])
def test_png_transparency_and_opacity_combine_over_the_source(tmp_path, source_video, opacity):
    asset, output = tmp_path / "transparent.png", tmp_path / "alpha.mp4"
    Image.new("RGBA", (40, 40), (255, 0, 0, 128)).save(asset)
    cue = {"path": str(asset), "mode": "manual", "start": .5, "end": 1.5, "duration": 1,
           "x": 25, "y": 25, "width": 50, "height": 50,
           "fit": "stretch", "background": "none", "opacity": opacity}
    broll.render_cutaways(str(source_video), str(output), [cue])
    pixel = _pixel(_frame(output), 80, 60)
    if opacity == 0:
        _blue(pixel)
    else:
        assert 45 < pixel[0] < 80 and pixel[1] < 15 and 165 < pixel[2] < 210
    _blue(_pixel(_frame(output), 10, 10))


def test_default_fullscreen_and_box_blur_fill_only_the_selected_area(tmp_path, source_video):
    asset = tmp_path / "red.png"
    Image.new("RGB", (80, 20), "red").save(asset)
    base = {"path": str(asset), "mode": "manual", "start": .5, "end": 1.5, "duration": 1}
    legacy, boxed = tmp_path / "legacy.mp4", tmp_path / "boxed.mp4"
    broll.render_cutaways(str(source_video), str(legacy), [base])
    assert _pixel(_frame(legacy), 10, 10)[0] > 235  # old recipes retain full-frame blur
    broll.render_cutaways(str(source_video), str(boxed), [
        {**base, "x": 25, "y": 25, "width": 50, "height": 50}])
    frame = _frame(boxed)
    assert _pixel(frame, 80, 35)[0] > 235  # blurred fill stays inside its chosen box
    _blue(_pixel(frame, 80, 15))
    _blue(_pixel(frame, 130, 60))
