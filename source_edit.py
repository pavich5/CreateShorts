"""Splice verified moments from the user's own source video into a short.

The model chooses bounded times; this module owns every media command. Source
shots retain their full frame over a blurred fill so an object and a reaction
are not lost to the short's portrait crop. Originals are never overwritten.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

import layout_ranges
import recut
from ffmpeg_utils import LOUDNORM_FILTER, METADATA_SCRUB, QUALITY_FAST, video_encode_args

MAX_MOMENT_SECONDS = 20.0
MODES = {"cutaway", "insert", "append"}


def _number(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number.")
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{name} must be a finite number.") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    return value


def _moment(moment, source_duration=None):
    if not isinstance(moment, dict):
        raise ValueError("Choose a verified source moment before rendering.")
    start = _number(moment.get("start"), "Source start")
    end = _number(moment.get("end"), "Source end")
    if start < 0 or end <= start:
        raise ValueError("The source moment needs a positive duration within the video.")
    if end - start > MAX_MOMENT_SECONDS + .001:
        raise ValueError(f"A source insert can be at most {MAX_MOMENT_SECONDS:g} seconds.")
    if source_duration is not None and end > source_duration + .05:
        raise ValueError("The source moment extends past the original video.")
    if source_duration is not None:
        end = min(end, source_duration)
    return start, end


def probe(path):
    """Read canvas, duration and audio presence without importing the ML stack."""
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate:format=duration",
        "-of", "json", str(path),
    ], capture_output=True, text=True, check=True, timeout=30)
    data = json.loads(result.stdout)
    video = next((stream for stream in data.get("streams", [])
                  if stream.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("The selected file has no video stream.")
    duration = _number((data.get("format") or {}).get("duration"), "Video duration")
    width, height = int(video["width"]), int(video["height"])
    try:
        fps = float(Fraction(video.get("avg_frame_rate") or "30"))
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    if duration <= 0 or width < 2 or height < 2 or not 0 < fps <= 240:
        raise ValueError("The video has invalid timing or dimensions.")
    return {"width": width, "height": height, "duration": duration, "fps": fps,
            "has_audio": any(stream.get("codec_type") == "audio"
                             for stream in data.get("streams", []))}


def build_timeline(duration, moment, at=None, mode="cutaway"):
    """Describe current-file and original-source intervals in output seconds."""
    duration = _number(duration, "Clip duration")
    if duration <= 0 or mode not in MODES:
        raise ValueError("Choose cutaway, insert or append for a positive-length clip.")
    source_start, source_end = _moment(moment)
    at = duration if at is None and mode == "append" else (0.0 if at is None else _number(at, "Insert time"))
    if mode == "append":
        at = duration
    if not 0 <= at <= duration:
        raise ValueError("The insertion time must fall within this short.")
    inserted = source_end - source_start
    if mode == "cutaway":
        inserted = min(inserted, duration - at)
        source_end = source_start + inserted
    if inserted < .1:
        raise ValueError("There is not enough time for a visible source moment.")
    timeline = []

    def current(start, end, output_start):
        if end - start > .000001:
            timeline.append({"kind": "current", "start": round(output_start, 6),
                             "end": round(output_start + end - start, 6),
                             "input_start": start, "input_end": end,
                             "audio": "current"})

    current(0.0, at, 0.0)
    timeline.append({"kind": "source", "start": round(at, 6),
                     "end": round(at + inserted, 6), "source_start": source_start,
                     "source_end": source_end,
                     "audio": "current" if mode == "cutaway" else "source"})
    if mode == "cutaway":
        current(at + inserted, duration, at + inserted)
        new_duration = duration
    else:
        current(at, duration, at + inserted)
        new_duration = duration + inserted
    return {"mode": mode, "at": at, "duration": inserted,
            "old_duration": duration, "new_duration": round(new_duration, 6),
            "source_moment": {"start": source_start, "end": source_end},
            "timeline": timeline,
            "preserve_current_audio": mode == "cutaway"}


def _current_segments(current_segments):
    if not isinstance(current_segments, (list, tuple)) or not current_segments:
        raise ValueError("The current clip needs its source timeline for captions.")
    segments = []
    for segment in current_segments:
        if not isinstance(segment, dict):
            raise ValueError("Invalid current source interval.")
        start = _number(segment.get("start"), "Current source start")
        end = _number(segment.get("end"), "Current source end")
        if start < 0 or end <= start:
            raise ValueError("Invalid current source interval.")
        segments.append({"start": start, "end": end})
    return segments


def original_source_segments(timeline, current_segments):
    """Build the updated source-absolute speech recipe after a source edit.

    A visual-only cutaway retains the old spoken timeline. Real inserts and
    appends add their source intervals. Adjacent source intervals merge so a
    word spanning an artificial cutaway boundary is not captioned twice.
    """
    current_segments = _current_segments(current_segments)
    duration = sum(segment["end"] - segment["start"] for segment in current_segments)
    if not isinstance(timeline, (list, tuple)) or not timeline:
        raise ValueError("The source edit needs a valid output timeline.")
    out = []

    def current(start, end):
        start, end = _number(start, "Current interval start"), _number(end, "Current interval end")
        if not 0 <= start < end <= duration + .000001:
            raise ValueError("Current intervals must stay inside the existing clip.")
        offset = 0.0
        for segment in current_segments:
            length = segment["end"] - segment["start"]
            overlap_start, overlap_end = max(start, offset), min(end, offset + length)
            if overlap_end > overlap_start:
                out.append({"start": round(segment["start"] + overlap_start - offset, 6),
                            "end": round(segment["start"] + overlap_end - offset, 6)})
            offset += length

    for part in timeline:
        if not isinstance(part, dict):
            raise ValueError("Invalid source edit timeline interval.")
        if part.get("kind") == "current":
            current(part.get("input_start"), part.get("input_end"))
        elif part.get("kind") == "source" and part.get("audio") == "current":
            # In cutaway mode the existing speech keeps playing at these
            # output times, even though its picture comes from elsewhere.
            current(part.get("start"), part.get("end"))
        elif part.get("kind") == "source":
            start, end = _moment({"start": part.get("source_start"),
                                  "end": part.get("source_end")})
            out.append({"start": start, "end": end})
        else:
            raise ValueError("Unknown source edit timeline interval.")
    merged = []
    for segment in out:
        if merged and abs(merged[-1]["end"] - segment["start"]) <= .000001:
            merged[-1]["end"] = segment["end"]
        else:
            merged.append(dict(segment))
    return merged


def compose_transcript(transcript, current_segments, moment, at=None, mode="cutaway",
                       current_transcript=None):
    """Caption the new spoken timeline, including inserted source speech once.

    ``current_segments`` use original-source times in the current clip's order.
    Cutaways keep the current speech. Inserts/appends splice source speech at
    the same point as the picture, shifting the existing remainder forward.
    """
    segments = _current_segments(current_segments)
    duration = sum(segment["end"] - segment["start"] for segment in segments)
    if current_transcript is None:
        current = recut.virtual_transcript(transcript, segments)
    else:
        if not isinstance(current_transcript, dict) or not isinstance(current_transcript.get("segments"), list):
            raise ValueError("Edited captions need a valid clip-relative transcript.")
        current = recut.virtual_transcript(current_transcript, [{"start": 0, "end": duration}])
    record = build_timeline(duration, moment, at, mode)
    if mode == "cutaway":
        return current
    out, offset = [], 0.0
    for part in record["timeline"]:
        if part["kind"] == "current":
            section = recut.virtual_transcript(current, [{
                "start": part["input_start"], "end": part["input_end"],
            }])
        else:
            section = recut.virtual_transcript(transcript, [{
                "start": part["source_start"], "end": part["source_end"],
            }])
        for segment in section["segments"]:
            moved = copy.deepcopy(segment)
            moved["start"] = round(moved["start"] + offset, 6)
            moved["end"] = round(moved["end"] + offset, 6)
            for word in moved.get("words", []):
                word["start"] = round(word["start"] + offset, 6)
                word["end"] = round(word["end"] + offset, 6)
            out.append(moved)
        offset += part["end"] - part["start"]
    return {"language": (transcript or {}).get("language", "en"), "segments": out}


def _source_picture(index, width, height, fps, duration):
    """Fit the whole source frame, rather than cropping away the reveal."""
    return [
        f"[{index}:v]trim=duration={duration:.6f},setpts=PTS-STARTPTS,"
        f"fps={fps:.9g},split=2[sbg][sfg]",
        f"[sbg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=20[blur]",
        f"[sfg]scale={width}:{height}:force_original_aspect_ratio=decrease[fit]",
        "[blur][fit]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[source_picture]",
    ]


def _audio(index, start, end, label, has_audio, normalize=False):
    duration = end - start
    if has_audio:
        # The current short has already been mastered by the clip pipeline.
        # Only newly sourced speech needs the same loudness target. loudnorm
        # may output 192 kHz, so resample afterwards for a uniform concat.
        loudness = f"{LOUDNORM_FILTER}," if normalize else ""
        return (f"[{index}:a:0]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS,{loudness}aresample=48000:async=1:first_pts=0,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration={duration:.6f}[{label}]")
    return f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f}[{label}]"


def _layouts(input_path, record, source_info, width, height):
    original = layout_ranges.read(str(input_path)) or [{
        "start": 0.0, "end": record["old_duration"], "layout": "general",
    }]
    visible_height = min(height, width * source_info["height"] / source_info["width"])
    top = (height - visible_height) / (2 * height)
    out = []
    for part in record["timeline"]:
        if part["kind"] == "source":
            out.append({"start": part["start"], "end": part["end"],
                        "layout": "wide", "content": [top, 1 - top]})
        else:
            for item in layout_ranges.remap(original, [{
                "start": part["input_start"], "end": part["input_end"],
            }]):
                out.append({**item, "start": round(item["start"] + part["start"], 6),
                            "end": round(item["end"] + part["start"], 6)})
    return out


def apply_source_edit(input_path, output_path, *, source_path, moment, at=None,
                      mode="cutaway", info=None):
    """Create a new rendered file and return its validated timeline metadata."""
    input_path, output_path, source_path = map(Path, (input_path, output_path, source_path))
    if not input_path.is_file() or not source_path.is_file():
        raise ValueError("The clip and retained source video must both be available.")
    if (output_path.resolve() in {input_path.resolve(), source_path.resolve()}
            or output_path.exists() or output_path.is_symlink()):
        raise ValueError("Choose a new output file; the original videos are kept.")
    current_info = probe(input_path)
    source_info = probe(source_path)
    source_start, source_end = _moment(moment, source_info["duration"])
    record = build_timeline(current_info["duration"],
                            {"start": source_start, "end": source_end}, at, mode)
    width, height = current_info["width"], current_info["height"]
    width, height = width // 2 * 2, height // 2 * 2
    fps = current_info["fps"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # The source seek applies only to input 1. It is accurate with decoding,
    # without requiring FFmpeg to decode the whole original for every insert.
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", str(input_path), "-ss", f"{source_start:.6f}",
               "-t", f"{record['duration']:.6f}", "-i", str(source_path)]
    graph = _source_picture(1, width, height, fps, record["duration"])
    if mode == "cutaway":
        end = record["at"] + record["duration"]
        graph.extend([
            f"[0:v]setpts=PTS-STARTPTS,fps={fps:.9g},scale={width}:{height},setsar=1[current_picture]",
            f"[source_picture]setpts=PTS-STARTPTS+{record['at']:.6f}/TB[placed_source]",
            f"[current_picture][placed_source]overlay=0:0:eof_action=pass:repeatlast=0:"
            f"enable='gte(t,{record['at']:.6f})*lt(t,{end:.6f})'[picture]",
        ])
        audio_args = ["-map", "0:a?", "-c:a", "copy"]
    else:
        labels = []
        for index, part in enumerate(record["timeline"]):
            video_label, audio_label = f"v{index}", f"a{index}"
            if part["kind"] == "source":
                graph.append(f"[source_picture]null[{video_label}]")
                graph.append(_audio(1, 0.0, record["duration"], audio_label, source_info["has_audio"],
                                    normalize=os.environ.get("AUDIO_NORMALIZE", "1").strip() != "0"))
            else:
                start, end = part["input_start"], part["input_end"]
                graph.append(f"[0:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
                             f"fps={fps:.9g},scale={width}:{height},setsar=1,format=yuv420p[{video_label}]")
                graph.append(_audio(0, start, end, audio_label, current_info["has_audio"]))
            labels.append(f"[{video_label}][{audio_label}]")
        graph.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=1[picture][sound]")
        audio_args = ["-map", "[sound]", "-c:a", "aac", "-b:a", "192k"]
    with tempfile.NamedTemporaryFile(prefix="temp_source_edit_", suffix=".mp4",
                                     dir=str(output_path.parent), delete=False) as handle:
        temporary = Path(handle.name)
    command.extend(["-filter_complex_threads", "1", "-filter_complex", ";".join(graph),
                    "-map", "[picture]", *audio_args,
                    "-t", f"{record['new_duration']:.6f}",
                    *video_encode_args(QUALITY_FAST), "-pix_fmt", "yuv420p",
                    *METADATA_SCRUB, "-movflags", "+faststart", str(temporary)])
    try:
        rendered = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if rendered.returncode or not temporary.is_file() or not temporary.stat().st_size:
            raise RuntimeError("The source footage could not be rendered. Your original clip is unchanged.")
        if output_path.exists() or output_path.is_symlink():
            raise ValueError("The chosen output already exists. Your original clip is unchanged.")
        temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
    record["layout_ranges"] = _layouts(input_path, record, source_info, width, height)
    layout_ranges.write(str(output_path), [
        (item["start"], item["end"], item["layout"], item.get("content"))
        for item in record["layout_ranges"]
    ])
    record["file"] = output_path.name
    return record
