"""Exercise stock-photo compositing at the dimensions used by portrait clips."""

import shutil
import subprocess

import pytest

import broll


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe unavailable",
)
def test_bt709_portrait_with_stock_jpeg_preserves_video_and_audio(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    source, output, photo = [tmp_path / name for name in ("source.mp4", "broll.mp4", "stock.jpg")]
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=540x960:r=12:d=3", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709", "-color_range", "tv", "-shortest", str(source),
    ], check=True, timeout=30)
    # Match the production failure: limited-range BT.709 source video plus
    # a full-range JPEG. Untagged lavfi sources do not expose the crash.
    image_module.new("RGB", (1024, 768), (235, 15, 15)).save(photo, quality=92)
    broll.render_cutaways(str(source), str(output), [{
        "path": str(photo), "start": 1, "end": 2, "duration": 1,
        "credit": {"title": "Illustrative stock photo"},
    }])

    assert broll._probe_video(str(output))["duration"] == pytest.approx(3, abs=.1)
    for at, expected in [(0.5, "blue"), (1.5, "red"), (2.5, "blue")]:
        result = subprocess.run([
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(output),
            "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
        ], capture_output=True, check=True, timeout=30)
        red, _, blue = result.stdout[:3]
        if expected == "red":
            assert red > blue + 100
        else:
            assert blue > red + 100

    def audio_hash(path):
        return subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy",
            "-f", "hash", "-hash", "sha256", "-",
        ], capture_output=True, check=True, timeout=30).stdout

    assert audio_hash(output) == audio_hash(source)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")
def test_manual_transparent_png_keeps_source_visible_and_allows_start_and_end(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    drawing = pytest.importorskip("PIL.ImageDraw")
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    source, output, photo = [tmp_path / name for name in ("source.mp4", "output.mp4", "transparent.png")]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=160x240:r=10:d=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(source)], check=True, timeout=30)
    image = image_module.new("RGBA", (160, 240), (0, 0, 0, 0))
    drawing.Draw(image).rectangle((50, 90, 110, 150), fill=(255, 0, 0, 255))
    image.save(photo)
    broll.render_cutaways(str(source), str(output), [{"path": str(photo), "start": 0,
        "end": 3, "duration": 3, "mode": "manual", "motion": False}])
    for at in (.1, 2.8):
        data = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(output),
            "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            capture_output=True, check=True, timeout=30).stdout
        frame = image_module.frombytes("RGB", (160, 240), data)
        center, corner = frame.getpixel((80, 120)), frame.getpixel((5, 5))
        assert center[0] > center[2] + 100
        assert corner[2] > corner[0] + 100
