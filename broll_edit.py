"""Reuse saved image placements when an existing clip is recut or reframed.

Cue source times stay in the original video's timeline. Each edit maps those
intervals onto its own ordered segments, using only already-saved media.
"""
from __future__ import annotations

import math
import subprocess
import time
from pathlib import Path

MIN_CUTAWAY_SECONDS = 0.5
_CUE_FIELDS = ("start", "end", "duration", "prompt", "anchor", "timing", "asset",
               "source_start", "source_end", "search_query", "reason", "credit", "selection_reason",
               "media_type", "media_duration", "media_start", "context", "clip_context",
               "target_aspect_ratio", "selected_by", "visual_verified", "selection_version",
               "quality_score", "relevance_score", "specificity_score", "visible_subject",
               "asset_sha256", "id", "placement_id", "mode", "motion", "source_occurrence",
               "x", "y", "width", "height", "fit", "opacity", "background",
               "kind", "volume", "has_audio", "media_width", "media_height",
               "animation", "animation_duration", "animation_elapsed", "layer")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _segments(segments):
    if not isinstance(segments, (list, tuple)) or not segments:
        raise ValueError("An edit needs source segments")
    normalized = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Invalid source segment")
        start, end = _number(segment.get("start")), _number(segment.get("end"))
        if start is None or end is None or start < 0 or end <= start:
            raise ValueError("Invalid source segment bounds")
        normalized.append((start, end))
    return normalized


def _source_cues(cues):
    """Keep valid source intervals, deduplicating repeated edit placements."""
    if not isinstance(cues, (list, tuple)):
        return []
    out, seen = [], set()
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        start = _number(cue.get("source_start"))
        end = _number(cue.get("source_end"))
        if start is None or end is None or start < 0 or end <= start:
            continue
        asset = cue.get("asset")
        # An invalid asset is retained as a missing visual, never as an input
        # path. This lets the caller report partial output accurately.
        asset = asset if isinstance(asset, str) else ""
        key = ((start, end, asset, cue.get("placement_id", cue.get("id")), cue.get("source_occurrence"))
               if cue.get("mode") == "manual" else (start, end, asset))
        if key in seen:
            continue
        seen.add(key)
        clean = {key: cue[key] for key in _CUE_FIELDS if key in cue}
        clean.update(source_start=start, source_end=end, asset=asset)
        out.append(clean)
    return sorted(out, key=lambda cue: (cue["source_start"], cue["source_end"]))


def remap_cues(cues, segments):
    """Map source-time cutaways onto ordered, possibly repeated edit segments.

    Invalid cue times are ignored. Invalid segment bounds raise instead of
    silently shifting every later placement onto the wrong timeline.
    """
    source_cues = _source_cues(cues)
    mapped, offset, occurrences = [], 0.0, {}
    for segment_start, segment_end in _segments(segments):
        for index, cue in enumerate(source_cues):
            source_start = max(segment_start, cue["source_start"])
            source_end = min(segment_end, cue["source_end"])
            start = round(offset + source_start - segment_start, 3)
            end = round(offset + source_end - segment_start, 3)
            duration = round(end - start, 3)
            minimum = 0.01 if cue.get("mode") == "manual" else MIN_CUTAWAY_SECONDS
            if duration < minimum:
                continue
            if cue.get("mode") == "manual" and type(cue.get("source_occurrence")) is int:
                occurrence = occurrences.get(index, 0)
                occurrences[index] = occurrence + 1
                if occurrence != cue["source_occurrence"]:
                    continue
            mapped.append({**cue, "start": start, "end": end,
                           "duration": duration,
                           "source_start": round(source_start, 3),
                           "source_end": round(source_end, 3),
                           **({"media_start": round(max(0.0, _number(cue.get("media_start")) or 0.0)
                                                    + source_start - cue["source_start"], 3)}
                              if cue.get("media_type") in {"video", "audio"} else {}),
                           **({"animation_elapsed": round(max(0.0, _number(cue.get("animation_elapsed")) or 0.0)
                                                           + source_start - cue["source_start"], 3)}
                              if cue.get("animation", "none") != "none" else {})})
        offset += segment_end - segment_start
    mapped.sort(key=lambda cue: (cue["start"], cue["end"]))
    # The original generator makes disjoint cues. Do not let old or manually
    # altered metadata place multiple full-screen images over the same frames.
    accepted = []
    previous_visual = None
    for cue in mapped:
        if cue.get("media_type") == "audio" or cue.get("mode") == "manual":
            accepted.append(cue)
            continue
        if previous_visual and cue["start"] < previous_visual["end"]:
            continue
        accepted.append(cue)
        previous_visual = cue
    return accepted


def _asset_path(job_root, asset, media_type=None):
    """Accept verified images or videos strictly inside this job's directory."""
    if not isinstance(asset, str) or not asset or "\x00" in asset:
        return None
    relative = Path(asset)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    try:
        path = (job_root / relative).resolve(strict=True)
        path.relative_to(job_root)
        if not path.is_file():
            return None
        if media_type in {"video", "audio"}:
            if path.suffix.lower() != (".mp4" if media_type == "video" else ".m4a"):
                return None
            if media_type == "video":
                import broll
                broll._probe_video(str(path))
            else:
                from manual_broll import probe_media
                if probe_media(path)["kind"] != "audio":
                    return None
            return path
        from PIL import Image
        with Image.open(path) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                return None
            image.verify()
        return path
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def _failure_count(value):
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def reapply_broll(input_path, record, segments):
    """Return ``(delivery_path, metadata)`` without generating new media.

    The clean input remains untouched on every path. Original cue definitions
    survive trims in ``source_cues`` so a later extension can restore them.
    """
    input_path = str(input_path)
    record = record if isinstance(record, dict) else {}
    original = _source_cues(record.get("source_cues", record.get("cues")))
    generation_failures = _failure_count(record.get(
        "generation_failed_cues", record.get("failed_cues", 0)))
    updated = {"enabled": bool(record.get("enabled", True)), "status": "skipped",
               "cues": [], "source_cues": original,
               "media_provider": record.get("media_provider", "stock"),
               "generation_failed_cues": generation_failures,
               "failed_cues": generation_failures}
    if record.get("mode") == "manual":
        updated.update(mode="manual", media_provider="manual",
                       base_file=Path(input_path).name)
    if not original:
        updated["reason"] = "No saved B-roll cutaways are available for this edit."
        return input_path, updated

    output_path = None
    try:
        mapped = remap_cues(original, segments)
        if not mapped:
            updated["reason"] = "No saved B-roll falls within the selected footage."
            return input_path, updated
        job_root = Path(input_path).parent.resolve(strict=True)
        valid, missing = [], 0
        for cue in mapped:
            path = _asset_path(job_root, cue.get("asset"), cue.get("media_type"))
            if path is None:
                missing += 1
                continue
            valid.append({**cue, "asset": str(path.relative_to(job_root)),
                          "path": str(path)})
        updated["failed_cues"] = generation_failures + missing
        if not valid:
            updated.update(status="failed", reason=(
                "Saved B-roll media is unavailable; the original footage was kept."))
            return input_path, updated

        import broll
        output_path = str(Path(input_path).with_name(
            f"broll_{time.time_ns()}_{Path(input_path).name}"))
        print("   🎨 [B-roll] Reusing saved visuals for this edit…")
        broll.render_cutaways(input_path, output_path, valid)
        if not Path(output_path).is_file() or Path(output_path).stat().st_size == 0:
            raise RuntimeError("B-roll renderer did not create a video")
        updated.update(status="partial" if updated["failed_cues"] else "applied",
                       file=Path(output_path).name,
                       cues=[{key: value for key, value in cue.items() if key != "path"}
                             for cue in valid])
        if missing:
            updated["reason"] = "Some saved B-roll media is unavailable."
        return output_path, updated
    except Exception as exc:
        if output_path:
            for artifact in (output_path, output_path + ".layout.json"):
                try:
                    Path(artifact).unlink(missing_ok=True)
                except OSError:
                    pass
        updated.update(status="failed", reason=(
            "B-roll could not be restored; the original footage was kept."),
                       error_type=type(exc).__name__)
        print(f"   ⚠️ [B-roll] Saved visuals could not be restored ({type(exc).__name__}) "
              "— keeping the speaker footage.")
        return input_path, updated
