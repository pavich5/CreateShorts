"""Uploaded media keeps its timing, motion, editable box, and original speech."""
from array import array
import math
from pathlib import Path
import shutil
import subprocess

import pytest

import broll
import broll_edit
import manual_broll as manual


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")


def ffmpeg(*args):
    return subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)],
                          capture_output=True, check=True).stdout


@pytest.fixture
def media(tmp_path, monkeypatch):
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    source = tmp_path / "source.mp4"
    silent = tmp_path / "silent.mp4"
    video = tmp_path / "upload.mov"
    sound = tmp_path / "effect.wav"
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=160x240:r=20:d=4",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", source)
    ffmpeg("-i", source, "-c:v", "copy", "-an", silent)
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=160x120:r=20:d=3",
           "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video)
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=880:duration=3", sound)
    return tmp_path, source, silent, video, sound


def frame(path, at):
    return ffmpeg("-ss", at, "-i", path, "-frames:v", 1,
                  "-pix_fmt", "rgb24", "-f", "rawvideo", "-")


def tone(path, at, frequency):
    samples = array("h", ffmpeg("-ss", at, "-i", path, "-t", .3,
                               "-vn", "-ac", 1, "-ar", 8000, "-f", "s16le", "-"))
    real = sum(value * math.cos(2 * math.pi * frequency * i / 8000)
               for i, value in enumerate(samples))
    imag = sum(value * math.sin(2 * math.pi * frequency * i / 8000)
               for i, value in enumerate(samples))
    return 2 * math.hypot(real, imag) / len(samples) / 32768


def test_uploads_are_normalized_and_actual_streams_are_verified(media):
    root, _, _, video, sound = media
    uploaded_video = manual.save_media(root, 0, video.read_bytes(), "clip.mov", "video/quicktime")
    uploaded_audio = manual.save_media(root, 0, sound.read_bytes(), "pop.wav", "audio/wav")
    assert uploaded_video["kind"] == "video" and uploaded_video["asset"].endswith(".mp4")
    assert uploaded_video["has_audio"] and uploaded_video["duration"] == pytest.approx(3, abs=.1)
    assert uploaded_audio["kind"] == "audio" and uploaded_audio["asset"].endswith(".m4a")
    assert manual.asset_path(root, uploaded_audio["asset"]).is_file()
    with pytest.raises(manual.ManualBrollError):
        manual.save_media(root, 0, b"not media", "fake.mp4", "video/mp4")
    with pytest.raises(manual.ManualBrollError):
        manual.save_media(root, 0, video.read_bytes(), "fake.wav", "audio/wav")
    assert not list(root.rglob("upload_*.bin"))


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("animation", ["fade", "slide_up", "zoom_in"])
def test_manual_media_box_and_entrance_are_rendered(media, animation, kind):
    root, source, _, video, _ = media
    if kind == "image":
        from PIL import Image
        image = root / "red.png"
        Image.new("RGB", (160, 120), "red").save(image)
        uploaded = manual.save_media(root, 0, image.read_bytes(), "red.png", "image/png")
    else:
        uploaded = manual.save_media(root, 0, video.read_bytes(), "clip.mov", "video/quicktime")
    cues = manual.validate_placements([{
        "id": kind, "asset": uploaded["asset"], "kind": kind, "start": 1, "end": 2.5,
        "source_start": .5, "x": 25, "y": 25, "width": 50, "height": 50,
        "fit": "stretch", "background": "none", "volume": 0,
        "animation": animation, "animation_duration": .5}], 4, root)
    output = root / f"{animation}.mp4"
    broll.render_cutaways(str(source), str(output), cues)
    early, settled, after = (frame(output, at) for at in (1.05, 1.8, 3))
    assert sum(abs(a - b) for a, b in zip(early, settled)) / len(early) > 1.5
    center = (120 * 160 + 80) * 3
    corner = (15 * 160 + 15) * 3
    assert settled[center] > 180 and settled[center + 2] < 50
    assert settled[corner + 2] > 180 and settled[corner] < 50
    assert after[center + 2] > 180 and after[center] < 50
    assert tone(output, 1.2, 440) > .08
    assert tone(output, 1.2, 880) < .005
    assert broll._probe_video(str(output))["duration"] == pytest.approx(4, abs=.1)


@pytest.mark.parametrize("with_speech", [True, False])
def test_sound_effect_is_delayed_and_mixed_over_the_full_clip(media, with_speech):
    root, source, silent, _, sound = media
    uploaded = manual.save_media(root, 0, sound.read_bytes(), "pop.wav", "audio/wav")
    cue = {"id": "pop", "asset": uploaded["asset"], "kind": "audio", "start": 1,
           "end": 2.5, "source_start": .5, "volume": 1}
    cues = manual.validate_placements([cue], 4, root)
    output = root / "mixed.mp4"
    broll.render_cutaways(str(source if with_speech else silent), str(output), cues)
    assert tone(output, .2, 880) < .005
    assert tone(output, 1.2, 880) > .08
    assert tone(output, 3, 880) < .005
    if with_speech:
        assert tone(output, .2, 440) > .08
        assert tone(output, 1.2, 440) > .08
        assert tone(output, 3, 440) > .08
    assert broll._probe_video(str(output))["duration"] == pytest.approx(4, abs=.1)


def test_overlapping_sound_and_video_survive_source_seams_and_recuts(media):
    root, _, _, video, sound = media
    video_asset = manual.save_media(root, 0, video.read_bytes(), "clip.mov", "video/quicktime")
    sound_asset = manual.save_media(root, 0, sound.read_bytes(), "pop.wav", "audio/wav")
    placements = manual.validate_placements([
        {"id": "video", "asset": video_asset["asset"], "start": .5, "end": 2.5,
         "source_start": .2, "animation": "fade", "animation_duration": .5},
        {"id": "sound", "asset": sound_asset["asset"], "start": .5, "end": 2.5,
         "source_start": .2, "volume": .7}], 4, root)
    original = manual.source_cues(placements, [{"start": 100, "end": 102}, {"start": 110, "end": 112}])
    reopened = manual.displayed_placements({"mode": "manual", "cues": original})
    assert len(reopened) == 2
    assert {item["media_start"] for item in reopened} == {.2}
    remapped = broll_edit.remap_cues(original, [{"start": 101, "end": 102}, {"start": 110, "end": 112}])
    assert {cue["media_type"] for cue in remapped} == {"video", "audio"}
    assert all(cue["media_start"] >= .7 for cue in remapped)
    assert next(cue for cue in remapped if cue["media_type"] == "video")["animation"] == "fade"
    for bad in ({"source_start": -1}, {"source_start": 2}, {"volume": math.nan}, {"animation": "bad"}):
        with pytest.raises(manual.ManualBrollError):
            manual.validate_placements([{**placements[0], "source_start": .2, **bad}], 4, root)


def test_uploaded_video_sound_is_always_muted():
    assert manual.media_options({'volume': 1}, 'video')['volume'] == 0
    assert manual.media_options({'volume': .7}, 'audio')['volume'] == .7
