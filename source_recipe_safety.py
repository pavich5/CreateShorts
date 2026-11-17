"""Reject only proven padding that a source rebuild would turn into speech."""
import math

from clip_timing import _render_timing


def _range(item):
    if not isinstance(item, dict):
        return None
    start, end = item.get('start'), item.get('end')
    if (type(start) not in (int, float) or type(end) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end):
        return None
    return start, end


def repeats_proven_padding(clip, requested, canonical_path):
    """Deliberate source overlap stays legal; recorded video-only tails do not."""
    recorded = _render_timing(canonical_path)
    if not recorded:
        return False
    original_start, boundary = recorded[0]['start'], recorded[-1]['end']
    recipe = [_range(item) for item in (clip.get('recipe') or {}).get('segments') or []]
    requested = [_range(item) for item in requested or []]
    for edit in clip.get('source_edits') or []:
        if not isinstance(edit, dict) or edit.get('mode') != 'append':
            continue
        moment = edit.get('source_moment') or {}
        evidence = moment.get('evidence') if isinstance(moment, dict) else None
        moment_range = _range(moment)
        if (not isinstance(evidence, dict) or evidence.get('kind') != 'transcript_continuation'
                or not moment_range or abs(moment_range[0] - boundary) > .002):
            continue
        for index, previous in enumerate(recipe):
            if (previous is None or previous[0] < original_start - .002
                    or not previous[0] < boundary or not 0 < previous[1] - boundary <= .351):
                continue
            if not any(part and abs(part[0] - boundary) < .002 and part[1] > previous[1]
                       and part[1] <= moment_range[1] + .002 for part in recipe[index + 1:]):
                continue
            for index, part in enumerate(requested):
                if (part and original_start - .002 <= part[0] < boundary
                        and boundary < part[1] <= previous[1] + .002
                        and any(other and abs(other[0] - boundary) < .002 and other[1] > part[1]
                                for other_index, other in enumerate(requested) if other_index != index)):
                    return True
    return False


SOURCE_REBUILD_DETAIL = ('This version has a video timing gap before added footage. '
    'Recutting it from the source would repeat speech. Use Trim or AI editing on this version, '
    'or choose source ranges without the overlap.')
