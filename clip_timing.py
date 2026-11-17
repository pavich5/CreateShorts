"""Recover legacy generated timing from captions tied to the rendered file."""
import copy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import subprocess
import uuid


@lru_cache(maxsize=512)
def _cached_duration(filename, size, modified):
    import ai_clip_edit
    return ai_clip_edit.probe(filename)['duration']


def rendered_duration(video_path):
    """Read the completed file's duration, caching only this exact version."""
    video = Path(video_path)
    try:
        stat = video.stat()
        duration = _cached_duration(str(video.resolve()), stat.st_size, stat.st_mtime_ns)
        return duration if math.isfinite(duration) and duration > 0 else None
    except (OSError, ValueError, KeyError, ZeroDivisionError, subprocess.SubprocessError):
        return None


def reconcile_rendered_clip(output_dir, clip, transcript=None):
    """Attach file timing and verified source timing to an existing result."""
    root = Path(output_dir).resolve()
    filename = Path(str(clip.get('video_url') or '').split('?', 1)[0]).name
    if not filename:
        return {}
    video = root / filename
    if video.is_symlink() or not video.is_file():
        return {}
    updates = {}
    record = _render_record(video)
    snapshot = (record or {}).get('clip')
    untouched = not any(clip.get(key) for key in ('recipe', 'ai_edit', 'broll', 'source_edits', 'source_visuals'))
    if (untouched and clip.get('caption_words') is None and isinstance(snapshot, dict)
            and (clip.get('start'), clip.get('end')) != (snapshot.get('start'), snapshot.get('end'))):
        # A restarted generation can publish new selections before replacing
        # this render. Restore the metadata saved with this exact file.
        updates.update({key: value for key, value in copy.deepcopy(snapshot).items()
                        if key not in ('video_url', 'deleted', 'index')})
    candidate = {**clip, **updates}
    if transcript:
        updates.update(recover_caption_timing(root, candidate, transcript))
    duration = rendered_duration(video)
    if duration is not None:
        updates['duration_seconds'] = duration
    return updates


def _tokens(text):
    return re.findall(r"\w+(?:['’]\w+)*", text.casefold())


def _caption_files(root, served):
    match = re.match(r'^(?:wm_)?subtitled_(\d+)_', served)
    if not match:
        return []
    # Both formats may be emitted for the same caption burn. Prefer the SRT
    # companion; duplicate files within a format are ambiguous.
    for extension in ('srt', 'ass'):
        files = list(root.glob(f'autosubs_{match[1]}_*.{extension}'))
        if files:
            return files
    return []


def _seconds(stamp):
    hours, minutes, seconds = stamp.replace(',', '.').split(':')
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _caption_cues(path, text):
    if path.suffix.lower() == '.srt':
        for block in re.split(r'\r?\n\s*\r?\n', text):
            cue = re.search(r'(\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*'
                            r'(\d+:\d{2}:\d{2}[,.]\d{3})[^\n]*\n(.*)', block, re.S)
            if cue:
                yield _seconds(cue[1]), _seconds(cue[2]), _tokens(cue[3])
        return
    active_words = []
    for line in text.splitlines():
        if not line.startswith('Dialogue:'):
            continue
        parts = line.split(',', 9)
        if len(parts) != 10:
            continue
        try:
            start, end = _seconds(parts[1]), _seconds(parts[2])
        except ValueError:
            continue
        # Karaoke rows also contain previous/future words. Only the active
        # highlighted span has this row's start/end; stripping every ASS tag
        # would incorrectly treat the whole line as speech at that instant.
        active = re.findall(r'\{[^{}]*\\c&H[0-9a-fA-F]+&[^{}]*\}([^{}]+)\{\\r\}', parts[9])
        if len(active) == 1:
            active_words.append((start, end, _tokens(active[0])))
        elif not re.search(r'\\(?:c|1c|k|kf)\b', parts[9]):
            plain = re.sub(r'\{[^}]*\}', '', parts[9]).replace(r'\N', ' ')
            tokens = _tokens(plain)
            if len(tokens) == 1:
                active_words.append((start, end, tokens))
            else:
                yield start, end, tokens
    # Combine consecutive active words into phrases to obtain unique source
    # matches even for common single words such as "the" and "business".
    for index in range(len(active_words) - 4):
        phrase = active_words[index:index + 5]
        if all(a[0] < b[0] and a[1] <= b[0] + .025 for a, b in zip(phrase, phrase[1:])):
            yield phrase[0][0], phrase[-1][1], [token for item in phrase for token in item[2]]


def _timing_path(video):
    return video.parent / ('.source_timing_' + hashlib.sha256(video.name.encode()).hexdigest()[:24] + '.json')


def write_render_timing(video_path, start, end, clip=None):
    """Bind the source interval to this completed render, not a mutable slot."""
    video = Path(video_path)
    stat = video.stat()
    record = {'v': 1, 'file': video.name, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
              'segments': [{'start': float(start), 'end': float(end)}]}
    if clip is not None:
        record['clip'] = copy.deepcopy(clip)
    target = _timing_path(video)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(record))
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _render_record(video_path):
    video = Path(video_path)
    record_path = _timing_path(video)
    try:
        if record_path.is_symlink() or record_path.stat().st_size > 2 * 1024 * 1024:
            return None
        record = json.loads(record_path.read_text())
        stat = video.stat()
        if (record.get('v') != 1 or record.get('file') != video.name
                or record.get('size') != stat.st_size or record.get('mtime_ns') != stat.st_mtime_ns):
            return None
        segments = record['segments']
        if (len(segments) != 1 or not all(math.isfinite(segment[key]) for segment in segments for key in ('start', 'end'))
                or not 0 <= segments[0]['start'] < segments[0]['end']):
            return None
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _render_timing(video_path):
    return (_render_record(video_path) or {}).get('segments')


def recover_generated_timing(video_path, served_filename, clip, transcript, info):
    """Return a verified source range when regenerated metadata replaced it.

    Legacy generation could save new moment selections before replacing old
    rendered clips. The rendered version's own SRT still records the words and
    relative times it actually used. Require unique, consistent source matches
    throughout that SRT; never stretch the stale range to fit the video.
    """
    if any(clip.get(key) for key in ('recipe', 'ai_edit', 'source_edits', 'source_visuals', 'broll')):
        return None
    if clip.get('caption_words') is not None:
        return None
    recorded = _render_timing(video_path)
    if recorded:
        return recorded
    duration = info['duration']
    try:
        if abs(duration - (float(clip['end']) - float(clip['start']))) <= .35:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    root = Path(video_path).parent
    try:
        files = _caption_files(root, Path(served_filename).name)
        if len(files) != 1 or files[0].is_symlink() or files[0].stat().st_size > 2 * 1024 * 1024:
            return None
        text = files[0].read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        return None
    words, positions = [], {}
    for segment in (transcript or {}).get('segments', []):
        for word in segment.get('words') or []:
            try:
                start, end = float(word['start']), float(word['end'])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(end) or end < start:
                continue
            for token in _tokens(str(word.get('word', ''))):
                positions.setdefault(token, []).append(len(words))
                words.append((token, start, end))
    anchors, eligible = [], 0
    for start, end, tokens in _caption_cues(files[0], text):
        if start <= .05 or end <= start or end > duration + .025 or len(tokens) < 3:
            continue
        eligible += 1
        candidates = []
        for index in positions.get(tokens[0], []):
            phrase = words[index:index + len(tokens)]
            if [word[0] for word in phrase] != tokens:
                continue
            offset = phrase[0][1] - start
            if offset >= 0 and abs(phrase[-1][2] - end - offset) <= .025:
                candidates.append(offset)
        if len(candidates) == 1:
            anchors.append((start, end, candidates[0]))
    if len(anchors) < 3 or len(anchors) < eligible * .8:
        return None
    offset = median(anchor[2] for anchor in anchors)
    if (max(abs(anchor[2] - offset) for anchor in anchors) > .025
            or min(anchor[0] for anchor in anchors) > duration * .25
            or max(anchor[1] for anchor in anchors) < duration * .75):
        return None
    # Preserve a verified encoder tail through prepare_trim's audio check.
    length = info.get('audio_duration', duration)
    if not math.isfinite(length) or not 0 <= duration - length <= 2:
        length = duration
    return [{'start': round(offset, 6), 'end': round(offset + length, 6)}]


def recover_caption_timing(output_dir, clip, transcript):
    """Repair captions using the unchanged base beneath manual media layers.

    Return draft metadata only. The caption save commits it after rendering,
    so previewing cannot modify the project or discard the user's edits.
    """
    if any(clip.get(key) for key in ('recipe', 'ai_edit', 'source_edits', 'source_visuals')):
        return {}
    if clip.get('caption_words') is not None:
        return {}
    root = Path(output_dir).resolve()
    record = clip.get('broll') or {}
    served = Path(clip.get('video_url') or '').name
    base_name = served
    if record:
        if record.get('mode') != 'manual':
            return {}
        # Manual media does not change the base clock. Its original preview
        # retains the generation's caption evidence even after later restyles.
        served = record.get('base_preview_file') or ''
        base_name = record.get('base_file') or ''
    if not served or not base_name or Path(base_name).name != base_name or Path(served).name != served:
        return {}
    if not _render_timing(root / base_name) and len(_caption_files(root, served)) != 1:
        return {}
    base = root / base_name
    if base.is_symlink() or not base.is_file():
        return {}
    import ai_clip_edit
    import layout_ranges
    import recut
    info = ai_clip_edit.probe(base)
    segments = recover_generated_timing(base, served,
        {key: value for key, value in clip.items() if key != 'broll'}, transcript, info)
    if not segments:
        return {}
    # Captions stop with the verified speech, even when the video holds its
    # last picture beyond that point. The edit recipe covers the full video.
    virtual = recut.virtual_transcript(transcript, segments)
    words = [copy.deepcopy(word) for segment in virtual['segments'] for word in segment.get('words', [])]
    start = segments[0]['start']
    full_segments = [{'start': start, 'end': round(start + info['duration'], 6)}]
    updates = {'start': start, 'end': full_segments[0]['end'],
        'duration_seconds': info['duration'], 'caption_words': words,
        'recipe': {'v': 1, 'segments': full_segments,
                   'canonical_range': {'start': start, 'end': segments[-1]['end']}},
        'layout_ranges': layout_ranges.read(str(base))}
    if record:
        import manual_broll
        updated_record = copy.deepcopy(record)
        updated_record['source_cues'] = manual_broll.source_cues(record.get('cues') or [], full_segments)
        updates['broll'] = updated_record
    return updates
