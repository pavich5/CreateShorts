"""Keep actual-source cutaways visible when a clip is cut again.

Each cue maps an original spoken interval to an equally long original picture
interval. No media paths come from saved metadata; the caller supplies the
retained source. This also supports reordered and repeated source segments.
"""
from __future__ import annotations

import math
from pathlib import Path
import uuid

import source_edit

MIN_SECONDS = .1
MAX_CUES = 128
_EPSILON = .00001


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Source visual times must be finite numbers.")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Source visual times must be finite numbers.") from None
    if not math.isfinite(value):
        raise ValueError("Source visual times must be finite numbers.")
    return value


def _cue(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid saved source visual.")
    cue = {key: _number(value.get(key)) for key in
           ("source_start", "source_end", "picture_start", "picture_end")}
    source_length = cue["source_end"] - cue["source_start"]
    picture_length = cue["picture_end"] - cue["picture_start"]
    if (min(cue["source_start"], cue["picture_start"]) < 0
            or source_length <= 0 or picture_length <= 0
            or abs(source_length - picture_length) > _EPSILON
            or source_length > source_edit.MAX_MOMENT_SECONDS + _EPSILON):
        raise ValueError("Saved source visuals need matching, bounded source and picture intervals.")
    return {key: round(value, 6) for key, value in cue.items()}


def _slice(cue, start, end):
    picture_start = cue["picture_start"] + start - cue["source_start"]
    return _cue({"source_start": start, "source_end": end,
                 "picture_start": picture_start,
                 "picture_end": picture_start + end - start})


def _replace(cues, new):
    """A newly requested picture wins overlapping original-source anchors."""
    out = []
    for old in cues:
        lo, hi = old["source_start"], old["source_end"]
        if new["source_end"] <= lo or new["source_start"] >= hi:
            out.append(old)
            continue
        if lo < new["source_start"]:
            out.append(_slice(old, lo, new["source_start"]))
        if hi > new["source_end"]:
            out.append(_slice(old, new["source_end"], hi))
    out.append(new)
    if len(out) > MAX_CUES:
        raise ValueError("This clip has too many saved source visuals. Simplify its source edits first.")
    return sorted(out, key=lambda cue: cue["source_start"])


def _cues(values):
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_CUES:
        raise ValueError("Invalid saved source visuals.")
    out = []
    for value in values:
        out = _replace(out, _cue(value))
    return out


def make_source_visuals(current_clip, record, current_segments, moment):
    """Return reusable source-picture cues for a successfully rendered edit."""
    cues = _cues(current_clip.get("source_visuals"))
    segments = source_edit._current_segments(current_segments)
    if not isinstance(record, dict) or record.get("mode") not in source_edit.MODES:
        raise ValueError("The source visual needs a valid source-edit timeline.")
    start, end = source_edit._moment(moment)
    at, length = _number(record.get("at")), _number(record.get("duration"))
    old_duration = sum(item["end"] - item["start"] for item in segments)
    if (not 0 <= at <= old_duration + _EPSILON
            or not MIN_SECONDS <= length <= end - start + _EPSILON):
        raise ValueError("The source visual falls outside its edit timeline.")
    if record["mode"] != "cutaway":
        return _replace(cues, _cue({
            "source_start": start, "source_end": start + length,
            "picture_start": start, "picture_end": start + length,
        }))
    if at + length > old_duration + _EPSILON:
        raise ValueError("The source cutaway falls outside the current clip.")
    offset = 0.0
    for segment in segments:
        duration = segment["end"] - segment["start"]
        lo, hi = max(at, offset), min(at + length, offset + duration)
        if hi - lo > _EPSILON:
            anchor = segment["start"] + lo - offset
            picture = start + lo - at
            cues = _replace(cues, _cue({
                "source_start": anchor, "source_end": anchor + hi - lo,
                "picture_start": picture, "picture_end": picture + hi - lo,
            }))
        offset += duration
    return cues


def _mapped(cues, segments):
    placements, offset = [], 0.0
    for segment in segments:
        for cue in cues:
            lo = max(segment["start"], cue["source_start"])
            hi = min(segment["end"], cue["source_end"])
            # A trim can leave less than a few frames of an old cutaway.
            length = round(hi - lo, 6)
            if length < MIN_SECONDS:
                continue
            picture = cue["picture_start"] + lo - cue["source_start"]
            placements.append({"at": round(offset + lo - segment["start"], 6),
                               "start": round(picture, 6),
                               "end": round(picture + length, 6)})
        offset += segment["end"] - segment["start"]
    return sorted(placements, key=lambda part: part["at"])


def reapply_source_visuals(input_path, source_path, visuals, segments):
    """Restore full-frame source pictures without searching or changing audio.

    Invalid metadata or a failed render aborts the recut, rather than claiming
    a visual was preserved. Only trim remnants below 0.1 seconds are omitted.
    """
    cues = _cues(visuals)
    segments = source_edit._current_segments(segments)
    placements = _mapped(cues, segments)
    if not placements:
        return str(input_path)
    if not source_path or not Path(source_path).is_file():
        raise ValueError("The original source video is needed to preserve these source visuals.")
    source_info = source_edit.probe(source_path)
    current_info = source_edit.probe(input_path)
    if abs(current_info["duration"] - sum(s["end"] - s["start"] for s in segments)) > .35:
        raise ValueError("The recut timing does not match its saved source visuals.")
    if any(cue["source_end"] > source_info["duration"] + .05
           or cue["picture_end"] > source_info["duration"] + .05 for cue in cues):
        raise ValueError("A saved source visual extends past the retained original video.")
    original = Path(input_path)
    current, created = str(original), []
    try:
        for part in placements:
            output = original.with_name("source_visual_" + uuid.uuid4().hex[:16] + ".mp4")
            created.append(output)
            source_edit.apply_source_edit(current, output, source_path=source_path,
                                          moment={"start": part["start"], "end": part["end"]},
                                          at=part["at"], mode="cutaway")
            current = str(output)
        for path in created[:-1]:
            path.unlink(missing_ok=True)
            Path(str(path) + ".layout.json").unlink(missing_ok=True)
        return current
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
            Path(str(path) + ".layout.json").unlink(missing_ok=True)
        raise
