"""Orchestrate a verified source-footage request without changing originals."""
from __future__ import annotations

import copy
import json
import math
import subprocess
import time
from pathlib import Path

import recut
import source_edit
import source_moments

CODEC_TAIL_TOLERANCE = .35
INSERT_SNAP_SECONDS = .3
_MODES = {"source_cutaway": "cutaway", "source_insert": "insert", "source_append": "append",
          "source_continue": "append"}


def _number(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number.")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be a finite number.") from None
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number.")
    return value


def normalize_current_segments(segments, duration):
    """Account only for a small mux/codec tail; never invent a missing edit."""
    normalized = source_edit._current_segments(segments)
    delta = duration - sum(item["end"] - item["start"] for item in normalized)
    if abs(delta) > CODEC_TAIL_TOLERANCE + 1e-6:
        raise ValueError("This version's timing no longer matches its source recipe. Reopen the original project before adding source footage.")
    if normalized[-1]["end"] + delta <= normalized[-1]["start"]:
        raise ValueError("The clip has no valid final source interval.")
    normalized[-1]["end"] = round(normalized[-1]["end"] + delta, 6)
    return normalized


def _caption_override(clip, language):
    words = clip.get("caption_words")
    if not isinstance(words, list):
        return None
    clean = []
    for item in words:
        if not isinstance(item, dict) or not isinstance(item.get("word"), str):
            raise ValueError("The saved caption words are invalid. Restore the captions before adding footage.")
        start, end = _number(item.get("start"), "Caption start"), _number(item.get("end"), "Caption end")
        if start < 0 or end < start:
            raise ValueError("The saved caption words have invalid timings.")
        # Whisper can assign an instantaneous word, and older cuts persisted
        # those timestamps. Give it one millisecond instead of blocking every
        # subsequent timeline edit. Reversed/non-finite times remain invalid.
        if end == start:
            end = start + .001
        clean.append({"word": item["word"], "start": start, "end": end})
    clean.sort(key=lambda item: item["start"])
    return {"language": language, "segments": [{
        "start": 0.0, "end": max((word["end"] for word in clean), default=0),
        "words": clean, "text": " ".join(word["word"] for word in clean),
    }]}


def _snap_insert(at, transcript, duration):
    if not 0 <= at <= duration:
        raise ValueError("Choose an insertion point within the current short.")
    for word in recut.transcript_words(transcript):
        if word["s"] < at < word["e"]:
            options = [edge for edge in (word["s"], word["e"])
                       if 0 <= edge <= duration and abs(edge - at) <= INSERT_SNAP_SECONDS]
            if not options:
                raise ValueError("The insertion falls inside a spoken word. Choose a nearby word boundary or append the scene.")
            # Prefer the end on a tie: finish the current word before adding
            # the source sequence, rather than separating its first syllable.
            return min(options, key=lambda edge: (abs(edge - at), -edge))
    return at


def _public_moment(moment):
    if (isinstance(moment, dict) and isinstance(moment.get("evidence"), dict)
            and moment["evidence"].get("kind") == "transcript_continuation"):
        start, end = source_edit._moment(moment)
        count = moment["evidence"].get("word_count")
        if type(count) is not int or count <= 0:
            raise ValueError("The source continuation has no transcribed speech.")
        return {"start": start, "end": end,
                "summary": " ".join(str(moment.get("summary") or "").split())[:800],
                "evidence": {"kind": "transcript_continuation", "word_count": count}}
    if not isinstance(moment, dict) or not isinstance(moment.get("evidence"), dict) or moment["evidence"].get("verified") is not True:
        raise ValueError("The requested source scene was not visually verified. Your current clip is unchanged.")
    start, end = source_edit._moment(moment)
    evidence = moment["evidence"]
    subject, reaction = [], []
    for name, destination, required in [("subject_frames", subject, True),
                                         ("reaction_frames", reaction, False)]:
        values = evidence.get(name)
        if not isinstance(values, list) or len(values) > 20 or (required and not values):
            raise ValueError("The source scene has no usable visual evidence.")
        for value in values:
            value = _number(value, "Evidence time")
            if not start <= value < end:
                raise ValueError("Visual evidence falls outside the selected source sequence.")
            if value not in destination:
                destination.append(value)
    if evidence.get("subject_view") != "recognizable":
        raise ValueError("The requested subject is not clearly visible in the selected source scene.")
    confidence = _number(evidence.get("confidence"), "Visual confidence")
    if not .65 <= confidence <= 1:
        raise ValueError("The source scene could not be verified confidently.")
    result = {"start": start, "end": end,
              "summary": " ".join(str(moment.get("summary") or "").split())[:800],
              "evidence": {"verified": True, "subject_view": "recognizable",
                           "subject_frames": subject, "reaction_frames": reaction,
                           "confidence": confidence}}
    for key, limit in [("query", 500), ("source_fingerprint", 128)]:
        if isinstance(moment.get(key), str):
            result[key] = moment[key][:limit]
    if isinstance(moment.get("verified_range"), dict):
        verified_start, verified_end = source_edit._moment(moment["verified_range"])
        if not verified_start <= start < end <= verified_end:
            raise ValueError("Speech boundary adjustment exceeds its verified source range.")
        result["verified_range"] = {"start": verified_start, "end": verified_end}
    return result


def continuation_moment(transcript, current_segments, requested, source_duration):
    """Append the next retained speech, with no invented narrative or frame claims."""
    segments = source_edit._current_segments(current_segments)
    requested = _number(requested, "Extension duration")
    source_duration = _number(source_duration, "Original video duration")
    if not .5 <= requested <= source_edit.MAX_MOMENT_SECONDS:
        raise ValueError("Choose an extension between 0.5 and 20 seconds.")
    start = segments[-1]["end"]
    remaining = source_duration - start
    if remaining < .5:
        raise ValueError("This clip already reaches the end of the original video. There is no more source content to append.")
    if requested > remaining + .025:
        raise ValueError(f"Only {remaining:g} seconds remain after this clip in the original video. Request a shorter extension.")
    end = min(source_duration, start + requested)
    words = recut.transcript_words(transcript)
    # Complete the last word when it fits within the allowed duration, or
    # stop before that word. Never cut a newly added word in half.
    for word in words:
        if word["s"] < end < word["e"]:
            if word["e"] <= min(source_duration, start + source_edit.MAX_MOMENT_SECONDS) and word["e"] - end <= CODEC_TAIL_TOLERANCE:
                end = word["e"]
            else:
                end = word["s"]
            break
    if end - start < .5:
        raise ValueError("The next source words do not fit this extension. Request a longer section.")
    if any(max(start, segment["start"]) < min(end, segment["end"]) - .001 for segment in segments):
        raise ValueError("The next source section is already included elsewhere in this clip. Choose a different source scene to append.")
    spoken = [word for word in words if word["e"] > start and word["s"] < end]
    if not spoken:
        raise ValueError("The next source section has no transcribed speech to continue the story. Choose a specific scene or add media instead.")
    return {"start": round(start, 6), "end": round(end, 6),
            "summary": "Continuation: " + " ".join(word["w"].strip() for word in spoken)[:780],
            "evidence": {"kind": "transcript_continuation", "word_count": len(spoken)}}


def continuation_source_segments(clean_path, clip, segments, info):
    """Keep the output clock while removing only a proven source-coordinate tail."""
    current = source_edit._current_segments(segments)
    last = current[-1]
    candidate = None
    canonical_start = None
    # A retained append is newer than the original clip range. A subsequent
    # trim inside it must use that trim's end, never the old canonical end.
    for edit in reversed(clip.get("source_edits") or []):
        if not isinstance(edit, dict) or edit.get("mode") != "append":
            continue
        # A trim can remove this append entirely while its immutable history
        # remains. Original encoder padding can overlap its source coordinates;
        # the retained output interval still stops before the append began.
        edit_at = edit.get("at")
        if (len(current) == 1 and isinstance(edit_at, (int, float))
                and not isinstance(edit_at, bool) and math.isfinite(edit_at)
                and sum(segment["end"] - segment["start"] for segment in current) <= edit_at + .002):
            continue
        try:
            start, end = source_edit._moment(edit.get("source_moment"))
        except (ValueError, TypeError):
            continue
        if start + .002 < last["end"] < end:
            return current
        if start + .002 < last["end"] and last["start"] < end:
            candidate = end
            break
    if candidate is None:
        canonical = (clip.get("recipe") or {}).get("canonical_range")
        if isinstance(canonical, dict):
            try:
                canonical_start = _number(canonical.get("start"), "Canonical start")
                canonical_end = _number(canonical.get("end"), "Canonical end")
                if 0 <= canonical_start < canonical_end:
                    candidate = canonical_end
            except ValueError:
                pass
    if candidate is None:
        return current
    delta = last["end"] - candidate
    if not 0 < delta <= CODEC_TAIL_TOLERANCE + 1e-6 or candidate <= last["start"]:
        return current
    expected_speech = sum(segment["end"] - segment["start"] for segment in current) - delta
    paths = [Path(clean_path)]
    base_name = (clip.get("broll") or {}).get("base_file")
    if isinstance(base_name, str) and Path(base_name).name == base_name:
        base = paths[0].parent / base_name
        if base != paths[0] and base.is_file() and not base.is_symlink():
            paths.append(base)
    from clip_timing import _render_timing
    from ai_clip_edit import probe as probe_with_audio
    proven = False
    explicit_recut = "framing" in (clip.get("recipe") or {})
    for path in paths:
        recorded = _render_timing(path)
        if (len(current) == 1 and recorded
                and abs(recorded[0]["start"] - last["start"]) < .002
                and abs(recorded[-1]["end"] - candidate) < .002):
            proven = True
            break
        if explicit_recut:
            # The user selected these source ranges explicitly. A source
            # can contain a real visual ending after its audio track ends.
            continue
        try:
            media = info if path == paths[0] and "audio_duration" in info else probe_with_audio(path)
            audio_duration = media.get("audio_duration")
            # One AAC packet is about21 ms. A broad video-tail tolerance here
            # would mistake a genuine short source extension for padding.
            if (isinstance(audio_duration, (int, float)) and math.isfinite(audio_duration)
                    and abs(media["duration"] - info["duration"]) <= CODEC_TAIL_TOLERANCE
                    and 0 <= media["duration"] - audio_duration <= CODEC_TAIL_TOLERANCE + .025
                    and abs(audio_duration - expected_speech) <= .025):
                proven = True
                break
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
            continue
    if (not proven and not explicit_recut and len(current) == 1
            and canonical_start is not None and last["start"] > canonical_start + .002):
        # Head trims keep the original source ending, but subsequent media
        # mixing can pad its audio too. Require the original render's exact
        # stat-bound provenance before treating this retained tail as padding.
        for record_path in list(paths[0].parent.glob(".source_timing_*.json"))[:128]:
            try:
                if record_path.is_symlink() or record_path.stat().st_size > 2 * 1024 * 1024:
                    continue
                filename = json.loads(record_path.read_text()).get("file")
                if not isinstance(filename, str) or Path(filename).name != filename:
                    continue
                original = paths[0].parent / filename
                if original.is_symlink():
                    continue
                recorded = _render_timing(original)
                if (recorded and abs(recorded[0]["start"] - canonical_start) < .002
                        and abs(recorded[-1]["end"] - candidate) < .002):
                    proven = True
                    break
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    if proven:
        last["end"] = candidate
    return current


def join_continuation_caption(transcript, current_segments, virtual, at):
    """Keep one word event when chronological source speech crosses the seam."""
    source_end = current_segments[-1]["end"]
    boundary = next((word for word in recut.transcript_words(transcript)
                     if word["s"] < source_end < word["e"]), None)
    if boundary is None:
        return
    # Match the original boundary word's retained interval, not just its
    # text: a user correction must cover the new remainder of that same word
    # without extending an unrelated manually timed phrase.
    last = current_segments[-1]
    prefix = sum(segment["end"] - segment["start"] for segment in current_segments[:-1])
    expected_start = prefix + max(boundary["s"], last["start"]) - last["start"]
    expected_end = prefix + last["end"] - last["start"]
    words = [(segment, word) for segment in virtual["segments"]
             for word in segment.get("words", [])]
    for (left_segment, left), (right_segment, right) in zip(words, words[1:]):
        if (right["word"].strip() == boundary["w"].strip()
                and abs(left["start"] - expected_start) < .002
                and abs(left["end"] - expected_end) < .002
                and left["start"] < at <= right["end"]
                and abs(right["start"] - at) < .001
                and 0 <= at - left["end"] <= CODEC_TAIL_TOLERANCE):
            left["end"] = right["end"]
            right_segment["words"].remove(right)
            for segment in (left_segment, right_segment):
                if segment["words"]:
                    segment["start"] = min(word["start"] for word in segment["words"])
                    segment["end"] = max(word["end"] for word in segment["words"])
                    segment["text"] = " ".join(word["word"].strip() for word in segment["words"])
            virtual["segments"] = [segment for segment in virtual["segments"] if segment["words"]]
            return


def prepare_manual_media_append(clip, output_path, source_path, moment, record, segments):
    """Extend the separate clean base so later media saves keep new footage.

    Existing placements and effects stay on their original output times. The
    already-rendered picture is preserved, while the clean base receives the
    identical append for future layer changes.
    """
    previous = clip.get("broll") or {}
    if previous.get("mode") != "manual" or record["mode"] != "append":
        return None
    output_path = Path(output_path)
    root = output_path.parent.resolve()
    base_name = previous.get("base_file")
    if not isinstance(base_name, str) or Path(base_name).name != base_name or not base_name.endswith(".mp4"):
        raise ValueError("The editable media base is unavailable. Restore the original project before extending it.")
    base = root / base_name
    if base.is_symlink() or not base.is_file() or base.resolve().parent != root:
        raise ValueError("The editable media base is unavailable. Restore the original project before extending it.")
    if abs(source_edit.probe(base)["duration"] - record["old_duration"]) > CODEC_TAIL_TOLERANCE:
        raise ValueError("The editable media base no longer matches this clip. Reopen the project before extending it.")
    updated = copy.deepcopy(previous)
    clean_output = root / ("manual_append_base_" + output_path.name)
    try:
        source_edit.apply_source_edit(base, clean_output, source_path=source_path,
                                      moment=moment, mode="append")
        if abs(source_edit.probe(clean_output)["duration"] - record["new_duration"]) > CODEC_TAIL_TOLERANCE:
            raise ValueError("The extended media layers have unexpected timing. Your original clip is unchanged.")
        import manual_broll
        updated.update(file=output_path.name, base_file=clean_output.name,
                       source_cues=manual_broll.source_cues(updated.get("cues") or [], segments))
        updated.pop("base_preview_file", None)
        updated.pop("overlay_fingerprint", None)
        return updated
    except Exception:
        clean_output.unlink(missing_ok=True)
        Path(str(clean_output) + ".layout.json").unlink(missing_ok=True)
        raise


def compose_captions_below_media(captioned_base, current_output, media, hook=None):
    """Restore saved caption/media/hook order while retaining strip-able twins."""
    import broll
    import hooks
    import manual_broll
    from ai_clip_edit import replay_post_effects

    current_output = Path(current_output)
    root = current_output.parent
    # The outer subtitle prefix walks back to the already-kept uncaptioned
    # media/hook twin. Later caption/hook replacement therefore stays editable.
    output = root / (f"subtitled_{time.time_ns()}_" + current_output.name)
    layered = root / ("caption_media_" + output.name) if hook else output
    info = source_edit.probe(captioned_base)
    raw = copy.deepcopy(media.get("cues") or [])
    for cue in raw:
        if cue.get("media_type") in {"video", "audio"}:
            cue["source_start"] = cue.get("media_start", 0)
    placements = manual_broll.validate_placements(raw, info["duration"], root)
    try:
        if placements:
            broll.render_cutaways(str(captioned_base), str(layered), placements, info)
        else:
            import shutil
            shutil.copyfile(captioned_base, layered)
        replay_post_effects(layered, media.get("post_effects") or [], info)
        if hook:
            result = hooks._run_encode(hooks._overlay_cmd(str(layered), hook["image"],
                hooks._overlay_graph(hook["x"], hook["y"], hook.get("duration")), str(output)),
                str(layered), [str(output)])
            if result.returncode or not output.is_file():
                raise RuntimeError("Could not preserve the hook above added media.")
        return str(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        if hook:
            layered.unlink(missing_ok=True)


def trim_source_speech_edges(moment, transcript):
    """Trim partial boundary words only when every verified picture survives."""
    result = copy.deepcopy(moment)
    start, end = result["start"], result["end"]
    evidence = result["evidence"]
    frames = evidence["subject_frames"] + evidence["reaction_frames"]
    for word in recut.transcript_words(transcript):
        if word["s"] < start < word["e"] and word["e"] - start <= CODEC_TAIL_TOLERANCE:
            if word["e"] <= min(frames) and end - word["e"] >= .5:
                start = word["e"]
        if word["s"] < end < word["e"] and end - word["s"] <= CODEC_TAIL_TOLERANCE:
            if word["s"] > max(frames) and word["s"] - start >= .5:
                end = word["s"]
    if start != result["start"] or end != result["end"]:
        result["verified_range"] = {"start": result["start"], "end": result["end"]}
        result.update(start=start, end=end)
    return result


def _canonical_range(clip, segments):
    recipe = clip.get("recipe")
    old = recipe.get("canonical_range") if isinstance(recipe, dict) else None
    if not isinstance(old, dict):
        old = {"start": clip.get("start", min(item["start"] for item in segments)),
               "end": clip.get("end", max(item["end"] for item in segments))}
    start, end = _number(old.get("start"), "Canonical start"), _number(old.get("end"), "Canonical end")
    if not 0 <= start < end:
        raise ValueError("This clip has an invalid original source range.")
    return {"start": start, "end": end}


def _provenance(record, moment):
    return {"mode": record["mode"], "at": record["at"], "duration": record["duration"],
            "old_duration": record["old_duration"], "new_duration": record["new_duration"],
            "source_moment": moment}


def render_source_operation(clean_path, new_path, *, source_path, transcript,
                            current_segments, operation, current_clip,
                            playhead_time=None, cache_dir=None):
    """Find, verify and apply one source operation, returning caption/edit state."""
    transcript = transcript if isinstance(transcript, dict) else {"language": "en", "segments": []}
    if not isinstance(current_clip, dict):
        raise ValueError("The current clip's metadata is unavailable.")
    raw = operation.model_dump() if hasattr(operation, "model_dump") else operation
    if not isinstance(raw, dict) or raw.get("type") not in _MODES:
        raise ValueError("Choose a supported source-footage operation.")
    mode = _MODES[raw["type"]]
    query = raw.get("query")
    if not isinstance(query, str) or not 3 <= len(query.strip()) <= 500:
        raise ValueError("Describe the actual source moment you want to show.")
    start, end = _number(raw.get("start"), "Edit start"), _number(raw.get("end"), "Edit end")
    requested = round(end - start, 6)
    if not .5 <= requested <= source_edit.MAX_MOMENT_SECONDS:
        raise ValueError("Choose a source sequence between 0.5 and 20 seconds.")
    info = source_edit.probe(clean_path)
    duration = info["duration"]
    normalized = normalize_current_segments(current_segments, duration)
    canonical = _canonical_range(current_clip, current_segments)
    spoken_segments = (continuation_source_segments(clean_path, current_clip, current_segments, info)
                       if raw["type"] == "source_continue" else current_segments)
    language = (transcript or {}).get("language", "en")
    override = _caption_override(current_clip, language)
    current_transcript = override if override is not None else recut.virtual_transcript(transcript, spoken_segments)
    at = duration if mode == "append" else start
    if not 0 <= at <= duration:
        raise ValueError("Choose an edit point within the current short.")
    if mode == "insert":
        at = _snap_insert(at, current_transcript, duration)
    preferred = raw.get("preferred_region", "auto")
    if preferred not in {"auto", "start", "end"}:
        raise ValueError("Choose where to search in the original video.")
    current_time = _number(playhead_time, "Playhead time") if playhead_time is not None else start
    if not 0 <= current_time <= duration + .05:
        raise ValueError("The playhead falls outside the current short.")
    # Retain the original recipe for search/cache identity. Tiny codec tails
    # affect caption/render timing, not which source moment was requested.
    if raw["type"] == "source_continue":
        found = continuation_moment(transcript, spoken_segments, requested,
                                    source_edit.probe(source_path)["duration"])
        moment = _public_moment(found)
    else:
        found = source_moments.find_source_moment(
            source_path, transcript, query.strip(), preferred_region=preferred,
            duration=min(12.0, max(2.0, requested)),
            current_segments=copy.deepcopy(current_segments), current_time=current_time,
            cache_dir=cache_dir)
        moment = _public_moment(found)
        if mode != "cutaway":
            moment = trim_source_speech_edges(moment, transcript)
    if mode == "cutaway" and moment["end"] - moment["start"] > min(end, duration) - at + .000001:
        raise ValueError("The verified source scene does not fit in this range. Choose a longer range or ask to append the scene so its reveal and reaction stay intact.")
    created = False
    try:
        record = source_edit.apply_source_edit(
            clean_path, new_path, source_path=source_path, moment=moment,
            at=at, mode=mode, info=info)
        created = True
        actual_duration = source_edit.probe(new_path)["duration"]
        if abs(actual_duration - record["new_duration"]) > CODEC_TAIL_TOLERANCE:
            raise ValueError("The rendered source edit has unexpected timing. Your current clip is unchanged.")
        record["new_duration"] = actual_duration
        record["source_moment"] = moment
        virtual = source_edit.compose_transcript(
            transcript, normalized, moment, at=at, mode=mode,
            current_transcript=current_transcript)
        if raw["type"] == "source_continue":
            join_continuation_caption(transcript, spoken_segments, virtual, at)
        updated_segments = source_edit.original_source_segments(record["timeline"], normalized)
        recipe = current_clip.get("recipe")
        recipe = copy.deepcopy(recipe) if isinstance(recipe, dict) else {}
        recipe.update(v=recipe.get("v", 1), segments=updated_segments, canonical_range=canonical)
        history = []
        previous_edits = current_clip.get("source_edits")
        previous_edits = previous_edits if isinstance(previous_edits, list) else []
        for previous in previous_edits[-19:]:
            try:
                if isinstance(previous, dict) and previous.get("mode") in source_edit.MODES:
                    old_moment = _public_moment(previous.get("source_moment"))
                    history.append(_provenance({key: _number(previous.get(key), key) if key != "mode" else previous[key]
                                                for key in ("mode", "at", "duration", "old_duration", "new_duration")}, old_moment))
            except ValueError:
                continue
        history.append(_provenance(record, moment))
        from source_visual_edit import make_source_visuals
        updates = {"duration_seconds": actual_duration,
                   "start": min(item["start"] for item in updated_segments),
                   "end": max(item["end"] for item in updated_segments),
                   "recipe": recipe, "layout_ranges": record["layout_ranges"],
                   "source_edits": history,
                   "source_visuals": make_source_visuals(current_clip, record, normalized, moment)}
        words = [copy.deepcopy(word) for segment in virtual["segments"]
                 for word in segment.get("words", [])]
        # Preserve this spoken timeline across later caption restyles. A mux
        # tail is silence, not permission to caption the next source word.
        if words or override is not None:
            updates["caption_words"] = words
        media = prepare_manual_media_append(current_clip, new_path, source_path,
                                            moment, record, updated_segments)
        if media is not None:
            updates["broll"] = media
        return {"record": record, "transcript": virtual, "clip_updates": updates,
                "source_moment": moment}
    except Exception:
        if created:
            Path(new_path).unlink(missing_ok=True)
            Path(str(new_path) + ".layout.json").unlink(missing_ok=True)
        raise
