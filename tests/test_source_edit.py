"""Actual-source inserts preserve pictures, spoken timing and original files."""
import array
import copy
import math
from pathlib import Path
import shutil
import subprocess

import pytest

import source_edit as se


def _words(transcript):
    return [(word["word"].strip(), word["start"], word["end"])
            for segment in transcript["segments"] for word in segment["words"]]


@pytest.fixture
def transcript():
    return {"language": "en", "segments": [{"words": [
        {"word": " opening", "start": 10.2, "end": 10.6},
        {"word": " ending", "start": 13.2, "end": 13.6},
        {"word": " reveal", "start": 100.2, "end": 100.6},
        {"word": " reaction", "start": 101.2, "end": 101.6},
    ]}]}


def test_cutaway_keeps_current_caption_timeline(transcript):
    result = se.compose_transcript(transcript, [{"start": 10, "end": 14}],
                                   {"start": 100, "end": 102}, at=1, mode="cutaway")
    assert _words(result) == [("opening", .2, .6), ("ending", 3.2, 3.6)]


def test_insert_moves_existing_words_after_source_speech(transcript):
    original = copy.deepcopy(transcript)
    result = se.compose_transcript(transcript, [{"start": 10, "end": 14}],
                                   {"start": 100, "end": 102}, at=2, mode="insert")
    assert _words(result) == [("opening", .2, .6), ("reveal", 2.2, 2.6),
                              ("reaction", 3.2, 3.6), ("ending", 5.2, 5.6)]
    assert transcript == original


def test_append_captions_are_after_existing_clip(transcript):
    result = se.compose_transcript(transcript, [{"start": 10, "end": 14}],
                                   {"start": 100, "end": 102}, mode="append")
    assert _words(result) == [("opening", .2, .6), ("ending", 3.2, 3.6),
                              ("reveal", 4.2, 4.6), ("reaction", 5.2, 5.6)]


def test_reordered_current_source_segments_keep_their_order(transcript):
    result = se.compose_transcript(transcript, [{"start": 13, "end": 14},
                                               {"start": 10, "end": 11}],
                                   {"start": 100, "end": 102}, at=1, mode="insert")
    assert _words(result) == [("ending", .2, .6), ("reveal", 1.2, 1.6),
                              ("reaction", 2.2, 2.6), ("opening", 3.2, 3.6)]


def test_existing_caption_edits_survive_source_insertion(transcript):
    override = {"language": "en", "segments": [{"words": [
        {"word": " corrected opening", "start": .2, "end": .6},
        {"word": " corrected ending", "start": 3.2, "end": 3.6},
    ]}]}
    result = se.compose_transcript(transcript, [{"start": 10, "end": 14}],
                                   {"start": 100, "end": 102}, at=2, mode="insert",
                                   current_transcript=override)
    assert _words(result) == [("corrected opening", .2, .6), ("reveal", 2.2, 2.6),
                              ("reaction", 3.2, 3.6), ("corrected ending", 5.2, 5.6)]


def test_source_recipe_maps_insert_through_reordered_current_segments():
    segments = [{"start": 30, "end": 32}, {"start": 10, "end": 12}]
    timeline = se.build_timeline(4, {"start": 100, "end": 102}, at=3, mode="insert")["timeline"]
    assert se.original_source_segments(timeline, segments) == [
        {"start": 30, "end": 32}, {"start": 10, "end": 11},
        {"start": 100, "end": 102}, {"start": 11, "end": 12},
    ]


def test_cutaway_does_not_replace_source_speech_recipe():
    segments = [{"start": 10, "end": 14}]
    timeline = se.build_timeline(4, {"start": 100, "end": 102}, at=1, mode="cutaway")["timeline"]
    assert se.original_source_segments(timeline, segments) == segments


def test_appended_source_recipe_is_absolute():
    timeline = se.build_timeline(4, {"start": 100, "end": 102}, mode="append")["timeline"]
    assert se.original_source_segments(timeline, [{"start": 10, "end": 14}]) == [
        {"start": 10, "end": 14}, {"start": 100, "end": 102},
    ]


def test_current_recipe_mapping_rejects_outside_input_timeline():
    with pytest.raises(ValueError):
        se.original_source_segments([{"kind": "current", "input_start": 0, "input_end": 5}],
                                     [{"start": 10, "end": 14}])


def test_cutaway_clamps_to_remaining_short():
    record = se.build_timeline(10, {"start": 30, "end": 36}, at=8, mode="cutaway")
    assert record["source_moment"] == {"start": 30, "end": 32}
    assert record["new_duration"] == 10 and record["duration"] == 2
    assert record["preserve_current_audio"] is True


@pytest.mark.parametrize("moment", [None, {}, {"start": True, "end": 5},
    {"start": -1, "end": 5}, {"start": 5, "end": 5},
    {"start": 0, "end": 21}, {"start": float("nan"), "end": 5},
    {"start": 0, "end": float("inf")}])
def test_rejects_invalid_source_bounds(moment):
    with pytest.raises(ValueError):
        se.build_timeline(10, moment, at=2)


@pytest.mark.parametrize("at", [-1, 11, True, float("nan"), float("inf")])
def test_rejects_invalid_insertion_time(at):
    with pytest.raises(ValueError):
        se.build_timeline(10, {"start": 0, "end": 2}, at=at)


def test_no_room_for_cutaway_does_not_silently_extend_audio():
    with pytest.raises(ValueError):
        se.build_timeline(10, {"start": 0, "end": 2}, at=10)


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        se.build_timeline(10, {"start": 0, "end": 2}, mode="invented_filter")


def test_missing_and_existing_files_are_not_overwritten(tmp_path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"keep")
    for output in [original, tmp_path / "existing.mp4"]:
        output.write_bytes(b"keep")
        with pytest.raises(ValueError):
            se.apply_source_edit(original, output, source_path=original,
                                 moment={"start": 0, "end": 1})
        assert output.read_bytes() == b"keep"
    with pytest.raises(ValueError):
        se.apply_source_edit(tmp_path / "gone.mp4", tmp_path / "new.mp4",
                             source_path=original, moment={"start": 0, "end": 1})


@pytest.fixture
def media(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    current = tmp_path / "current.mp4"
    source = tmp_path / "source.mp4"
    silent = tmp_path / "source_silent.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=160x240:r=10:d=4", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(current),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=white:s=320x180:r=15:d=3,drawbox=x=0:y=0:w=80:h=180:c=red:t=fill,drawbox=x=240:y=0:w=80:h=180:c=lime:t=fill",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source),
                    "-an", "-c:v", "copy", str(silent)], check=True)
    return current, source, silent


def _frame(path, at):
    image_module = pytest.importorskip("PIL.Image")
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ])
    return image_module.frombytes("RGB", (160, 240), raw)


def _frequency(path, at):
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-t", "0.4",
        "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
    ])
    samples = array.array("h", raw)
    amplitude = max((abs(value) for value in samples), default=0)
    if amplitude < 30:
        return 0
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
    return crossings / (len(samples) / 8000)


def _rms_db(path, at):
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-t", "0.4",
        "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-",
    ])
    samples = array.array("f", raw)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    return 20 * math.log10(max(rms, 1e-10))


def _assert_full_source_picture(path, at):
    frame = _frame(path, at)
    left, right = frame.getpixel((15, 120)), frame.getpixel((145, 120))
    assert left[0] > left[1] + 100
    assert right[1] > right[0] + 100


def test_real_cutaway_shows_complete_source_and_keeps_audio_packets(media, tmp_path):
    current, source, _ = media
    output = tmp_path / "cutaway.mp4"
    record = se.apply_source_edit(current, output, source_path=source,
                                  moment={"start": .5, "end": 1.5}, at=1, mode="cutaway")
    assert se.probe(output)["duration"] == pytest.approx(4, abs=.1)
    _assert_full_source_picture(output, 1.5)
    before, after = _frame(output, .5).getpixel((80, 120)), _frame(output, 2.5).getpixel((80, 120))
    assert before[2] > before[0] + 100 and after[2] > after[0] + 100
    def audio_hash(path):
        return subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
            "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-",
        ])
    assert audio_hash(current) == audio_hash(output)
    assert record["layout_ranges"][1]["layout"] == "wide"
    assert current.is_file() and source.is_file()


@pytest.mark.parametrize("mode,at,source_at,current_after", [
    ("append", None, 4.2, None), ("insert", 2, 2.2, 4.0),
])
def test_real_source_splice_keeps_both_audio_timelines(media, tmp_path, mode, at, source_at, current_after):
    current, source, _ = media
    output = tmp_path / f"{mode}.mp4"
    record = se.apply_source_edit(current, output, source_path=source,
                                  moment={"start": .5, "end": 2}, at=at, mode=mode)
    assert record["new_duration"] == 5.5
    assert se.probe(output)["duration"] == pytest.approx(5.5, abs=.15)
    assert _frequency(output, .5) == pytest.approx(440, abs=15)
    assert _frequency(output, source_at) == pytest.approx(660, abs=15)
    _assert_full_source_picture(output, source_at)
    if current_after is not None:
        assert _frequency(output, current_after) == pytest.approx(440, abs=15)
        assert _frame(output, current_after).getpixel((80, 120))[2] > 150


def test_silent_source_insert_gets_silence_without_shifting_following_speech(media, tmp_path):
    current, _, silent = media
    output = tmp_path / "silent_insert.mp4"
    se.apply_source_edit(current, output, source_path=silent,
                         moment={"start": .5, "end": 2}, at=2, mode="insert")
    assert _frequency(output, .5) == pytest.approx(440, abs=15)
    assert _frequency(output, 2.2) == 0
    assert _frequency(output, 4) == pytest.approx(440, abs=15)


@pytest.mark.parametrize("mode,at,source_at,after", [
    ("append", None, 4.5, None), ("insert", 2, 2.5, 4.5),
])
def test_quiet_source_is_normalized_without_changing_current_gain_and_can_opt_out(
        media, tmp_path, monkeypatch, mode, at, source_at, after):
    current, source, _ = media
    quiet = tmp_path / "quiet.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-c:v", "copy",
                    "-af", "volume=0.05", "-c:a", "aac", str(quiet)], check=True)
    outputs = []
    for enabled in ("1", "0"):
        monkeypatch.setenv("AUDIO_NORMALIZE", enabled)
        output = tmp_path / f"{mode}_normalize_{enabled}.mp4"
        se.apply_source_edit(current, output, source_path=quiet,
                             moment={"start": .5, "end": 2}, at=at, mode=mode)
        outputs.append(output)
        assert _rms_db(output, .5) == pytest.approx(_rms_db(current, .5), abs=.3)
        if after is not None:
            assert _rms_db(output, after) == pytest.approx(_rms_db(current, 3), abs=.3)
        assert _frequency(output, source_at) == pytest.approx(660, abs=15)
        assert se.probe(output)["duration"] == pytest.approx(5.5, abs=.15)
    normalized, disabled = outputs
    assert _rms_db(normalized, source_at) > -20
    assert _rms_db(normalized, source_at) - _rms_db(disabled, source_at) > 15
    assert _rms_db(disabled, source_at) == pytest.approx(_rms_db(quiet, 1), abs=.5)


def test_source_bounds_are_checked_against_actual_file(media, tmp_path):
    current, source, _ = media
    with pytest.raises(ValueError, match="past the original"):
        se.apply_source_edit(current, tmp_path / "bad.mp4", source_path=source,
                             moment={"start": 2, "end": 5}, at=1)
    assert not (tmp_path / "bad.mp4").exists()


def test_failed_render_cleans_temporary_file_and_keeps_inputs(tmp_path, monkeypatch):
    current, source, output = (tmp_path / name for name in ("current.mp4", "source.mp4", "failed.mp4"))
    current.write_bytes(b"current")
    source.write_bytes(b"source")
    monkeypatch.setattr(se, "probe", lambda *args: {
        "width": 160, "height": 240, "fps": 10, "duration": 4, "has_audio": True,
    })
    monkeypatch.setattr(se.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 1, "", "secret provider output"))
    with pytest.raises(RuntimeError, match="original clip is unchanged"):
        se.apply_source_edit(current, output, source_path=source,
                             moment={"start": 0, "end": 2}, at=1)
    assert current.read_bytes() == b"current" and source.read_bytes() == b"source"
    assert not output.exists() and not list(tmp_path.glob("temp_source_edit_*"))
