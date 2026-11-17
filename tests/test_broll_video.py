"""Moving cutaways keep their own motion, the speaker's audio, and edit offsets."""
import shutil
import subprocess

import pytest

import broll
import broll_edit




def test_trimmed_video_cutaway_preserves_playback_offset():
    cue = {"source_start": 104, "source_end": 107, "asset": "broll_assets_saved/video.mp4",
           "media_type": "video", "media_duration": 12, "media_start": 2}
    result = broll_edit.remap_cues([cue], [{"start": 105, "end": 110}])
    assert result[0]["media_start"] == 3
    assert result[0]["duration"] == 2
    assert result[0]["media_type"] == "video"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")
def test_video_cutaway_moves_without_replacing_speech_and_survives_trim(tmp_path, monkeypatch):
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    source, asset, output = [tmp_path / name for name in ("source.mp4", "asset.mp4", "output.mp4")]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=160x240:r=10:d=9", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=9", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(source)], check=True)
    # Both a moving test pattern and different audio expose accidental still
    # treatment and incorrect audio mapping when a real video is inserted.
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x240:r=10:d=3", "-f", "lavfi", "-i",
                    "sine=frequency=880:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(asset)], check=True)
    cue = {"path": str(asset), "start": 4, "end": 6, "duration": 2,
           "media_type": "video", "media_duration": 3}
    broll.render_cutaways(str(source), str(output), [cue])
    assert broll._probe_video(str(output))["duration"] == pytest.approx(9, abs=.15)

    def frame(at):
        return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(output),
                               "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                              capture_output=True, check=True).stdout
    before, during, later, after = [frame(at) for at in (1, 4.3, 5.3, 7)]
    def difference(left, right):
        return sum(abs(a - b) for a, b in zip(left, right)) / len(left)
    assert difference(before, after) < 3  # allow normal H.264 quantization
    assert difference(during, before) > 20
    assert difference(during, later) > 5

    def audio_hash(path):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                               "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"],
                              capture_output=True, check=True).stdout
    assert audio_hash(source) == audio_hash(output)
    record = {"cues": [{key: value for key, value in cue.items() if key != "path"}
                       | {"asset": asset.name, "source_start": 104, "source_end": 106}]}
    restored_path, restored = broll_edit.reapply_broll(source, record, [{"start": 105, "end": 114}])
    assert restored_path != str(source) and restored["status"] == "applied"
    assert restored["cues"][0]["media_start"] == 1
    assert audio_hash(source) == audio_hash(restored_path)
