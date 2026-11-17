"""Local image compositing preserves precise timing and original speech."""
from pathlib import Path
import shutil
import subprocess

import pytest

import broll


class TestRendering:
    def test_command_preserves_audio_duration_and_uses_exact_cue_window(self, tmp_path, monkeypatch):
        commands = []
        output = tmp_path / "broll.mp4"
        def run(command, **kwargs):
            commands.append(command)
            output.write_bytes(b"rendered")
            return subprocess.CompletedProcess(command, 0, "", "")
        monkeypatch.setattr(broll.subprocess, "run", run)
        monkeypatch.setenv("FFMPEG_ENCODER", "x264")
        broll.render_cutaways(str(tmp_path / "source.mp4"), str(output), [
            {"path": str(tmp_path / "image.jpg"), "start": 4.25, "end": 6.75, "duration": 2.5}],
            {"width": 180, "height": 320, "duration": 30, "fps": 30})
        command = commands[0]
        assert command[command.index("-c:a") + 1] == "copy"
        assert "0:a?" in command
        assert "-shortest" not in command
        graph = command[command.index("-filter_complex") + 1]
        assert "gte(t,4.25)*lt(t,6.75)" in graph
        assert "zoompan=" in graph
        assert command[-1] == str(output)

    @pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe unavailable")
    def test_real_cutaway_timing_and_audio_survive(self, tmp_path, monkeypatch):
        image_module = pytest.importorskip("PIL.Image")
        monkeypatch.setenv("FFMPEG_ENCODER", "x264")
        source, output, image = (tmp_path / name for name in ("source.mp4", "broll.mp4", "red.jpg"))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=160x240:r=10:d=9", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=9", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(source)], check=True)
        image_module.new("RGB", (160, 240), "red").save(image)
        broll.render_cutaways(str(source), str(output), [{"path": str(image), "start": 4,
                                                       "end": 6, "duration": 2}])
        assert broll._probe_video(str(output))["duration"] == pytest.approx(9, abs=0.15)
        def frame(at):
            result = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(output),
                                     "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24",
                                     "-f", "rawvideo", "-"], capture_output=True, check=True)
            return tuple(result.stdout[:3])
        for at in (1, 7):
            red, _, blue = frame(at)
            assert blue > red + 100
        red, _, blue = frame(5)
        assert red > blue + 100
        def audio_hash(path):
            return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                                   "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"],
                                  capture_output=True, check=True).stdout
        assert audio_hash(source) == audio_hash(output)

    @pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe unavailable")
    def test_landscape_stock_keeps_both_subjects_in_portrait_video(self, tmp_path, monkeypatch):
        image_module = pytest.importorskip("PIL.Image")
        drawing = pytest.importorskip("PIL.ImageDraw")
        monkeypatch.setenv("FFMPEG_ENCODER", "x264")
        source, output, photo = (tmp_path / name for name in ("source.mp4", "stock.mp4", "subjects.jpg"))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=160x240:r=10:d=3", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(source)], check=True)
        image = image_module.new("RGB", (800, 400), "white")
        draw = drawing.Draw(image)
        draw.rectangle((50, 20, 250, 380), fill=(240, 0, 0))
        draw.rectangle((550, 20, 750, 380), fill=(0, 220, 0))
        image.save(photo)
        broll.render_cutaways(str(source), str(output), [{
            "path": str(photo), "start": 1, "end": 2, "duration": 1,
            "credit": {"title": "Two subjects at opposite sides"},
        }])
        result = subprocess.run([
            "ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(output),
            "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
        ], capture_output=True, check=True)
        frame = image_module.frombytes("RGB", (160, 240), result.stdout)
        left, right = frame.getpixel((40, 120)), frame.getpixel((120, 120))
        assert left[0] > left[1] + 100  # left subject survives
        assert right[1] > right[0] + 100  # right subject survives
        assert broll._probe_video(str(output))["duration"] == pytest.approx(3, abs=.15)
