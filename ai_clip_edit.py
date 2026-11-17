"""Turn written clip edits into bounded operations, never executable AI code."""
import copy
import json
import math
import os
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

import llm_backend
from edit_builder import EFFECT_LIMITS, build_filter_string
from ffmpeg_utils import METADATA_SCRUB, QUALITY_FAST, video_encode_args


class Operation(BaseModel):
    type: Literal["steady_camera", "stabilize", "brighten", "darken", "mute",
                  "color_pop", "bw_moment", "flash", "vignette",
                  "zoom_in", "punch_in", "zoom_pulse", "trim", "remove",
                  "source_cutaway", "source_insert", "source_append", "source_continue"]
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    strength: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    center_x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    query: str = Field(default="", max_length=500)
    preferred_region: Literal["auto", "start", "end"] = "auto"


class EditPlan(BaseModel):
    operations: list[Operation] = Field(max_length=6)
    unsupported: list[str] = Field(default_factory=list)


def probe(path):
    result = subprocess.run([
        'ffprobe', '-v', 'error',
        '-show_entries', 'stream=codec_type,width,height,avg_frame_rate,duration:format=duration',
        '-of', 'json', str(path)], capture_output=True, check=True, timeout=30)
    data = json.loads(result.stdout)
    stream = next((item for item in data['streams'] if item.get('codec_type') == 'video'), data['streams'][0])
    duration = float(stream.get('duration') or data['format']['duration'])
    fps = float(Fraction(stream.get('avg_frame_rate') or '30'))
    if not math.isfinite(duration) or duration <= 0 or not 0 < fps <= 240:
        raise ValueError('This video has invalid timing information.')
    # Browsers and the media editor use the MP4 container duration. AAC may
    # end a frame after the video stream, which must not make Whole clip fail.
    container_duration = float((data.get('format') or {}).get('duration') or duration)
    if (math.isfinite(container_duration) and duration <= container_duration
            and container_duration - duration <= max(.1, 2 / fps)):
        duration = container_duration
    info = dict(width=int(stream['width']), height=int(stream['height']), duration=duration, fps=fps)
    audio = next((item for item in data['streams'] if item.get('codec_type') == 'audio'), {})
    # Matroska/WebM streams often omit duration even when sound is present.
    # Keep presence separate from measured duration; source timing checks need
    # the latter, while renderers must never use it to decide whether to map audio.
    info['has_audio'] = bool(audio)
    try:
        audio_duration = float(audio.get('duration', 0))
        if math.isfinite(audio_duration) and audio_duration > 0:
            info['audio_duration'] = audio_duration
    except (TypeError, ValueError):
        pass
    return info


def plan_edit(instructions, info, *, source_available=False, has_burns=False,
              api_key=None):
    prompt = f"""Translate this user's editing instructions into a precise video edit plan.
All times are seconds from the beginning of THIS SHORT, never the long source.
Duration: {info['duration']:.3f}s. Original uncropped source available: {source_available}.
Current playhead on the short: {info.get('playhead_time', 'not supplied')}.
Existing burned text that cannot be separated: {has_burns}.
Apply ONLY requested changes. Leave everything else unchanged. Up to 6 operations.
Do not invent visual observations: you are interpreting text, not watching footage.
Available operations:
- steady_camera: rebuild steady framing around the MAIN speaker from the original
  source. Smooth tracking ignores smaller inset faces and follows source-panel
  changes without cutting the speaker off. Requires original source.
  center_x is the crop centre across the source width: 0 left, 0.5 centre, 1 right.
  Use 0.5 for automatic main-speaker framing. Other values lock a manual crop.
- stabilize: reduce actual camera shake in the rendered clip; does not undo cuts.
- brighten/darken: adjust brightness, strength 0..1 (0.5 = moderate).
- mute: silence audio during the requested interval, without changing timing.
- trim: keep ONLY start..end of the current short, removing everything before
  start and after end. Video, sound, captions and overlays move together to t=0.
  "Start the video from 1.9 seconds" means trim start=1.9 and end={info['duration']:.3f}.
  "Start here" uses the current playhead as start. "Remove the first N seconds"
  means start=N; "end at N seconds" means start=0 and end=N.
  For "keep A to B seconds", use start=A and end=B. Use at most ONE trim.
  A trim can accompany ordinary effects; ALL effect times still refer to the
  current short BEFORE trimming. Apply source-footage changes separately.
- color_pop, bw_moment, flash, vignette: saturation, black-and-white, flash, vignette.
- zoom_in, punch_in, zoom_pulse: subtle zooms (strength 0.04..0.15, max 8 seconds).
  Cannot be used when inseparable burned text is present.
- source_cutaway: find the requested subject/moment anywhere in the FULL original
  source video and show that original footage over start..end of this short.
  Keeps the short's existing voice/audio. Shows the full source picture so an
  object and someone's reaction can both remain visible, rather than cropping
  automatically to a face. Requires original source. Put a clear description
  of what must actually be seen in query; do not invent source timestamps.
- source_continue: extend the existing story with the NEXT chronological speech
  from the retained original, appending it after the current ending. Preserves
  the entire existing clip. Use for 'extend this video', 'continue the narrative',
  'add another 10 seconds', 'add another scene after the current ending',
  'make it longer', or 'add explanation to the ending'
  when no particular visible subject or scene is requested. Set start={info['duration']:.3f}
  and end=start+requested added seconds (default 10, maximum 20). Put the user's
  continuation request in query. This uses real existing source footage, never
  invented speech or a generated story. If the source ends, a clear error follows.
- source_append: find and add a SPECIFIC requested visible subject or event AFTER
  this short ends, including its original sound/reaction. Use this only when
  the user names what must be visible (e.g. a car reveal or demonstration).
  Generic 'another scene' or 'more content' requests must use source_continue;
  never invent a subject or send a vague continuation query to visual search. Set start={info['duration']:.3f} and
  end=start+desired sequence length (normally 6–10 seconds, maximum 20).
- source_insert: insert a real source sequence at start within the short,
  preserving the material before and after it. end=start+desired inserted
  length (maximum 20). Uses the source sequence's own sound and shifts later
  material by the inserted length.
Source search query may request a reveal, car, demonstration, result, reaction
or any other actual event in the original video. Searching the whole source is
supported. preferred_region says where to look IN THE ORIGINAL (auto/start/end),
not where the scene should appear in the short. Destination times always refer
to this short. Requested visual source moments are visually verified before use.
Narrative source_continue uses the next chronological transcribed source speech.
For 'show the car reveal and his reaction near the end', use source_append
when the short's ending has too little room for a meaningful sequence. Preserve
the current sentence and append the payoff, rather than flashing a long reveal
for a fraction of a second. Use source_cutaway when the user wants the picture
changed while existing narration continues. Include all requested visible
subjects in query. Apply one source-footage operation per edit, separately from
camera/color effects. Do not reject footage search as an unsupported reframe.
For shaking, jitter, wobble, shaky footage, 'smooth the camera', or generally
'camera moving too much', use stabilize, even when the original source exists.
Use steady_camera when automatic reframing, switching to other faces, or animated
source layouts make the speaker jump around. Use stabilize for shake in the shot.
Do not use both camera operations on the same interval. For noticeable shake,
use stabilize strength 0.75; stronger shake can use 1.0.
For ordinary effects when no interval is specified, use start=0 and end={info['duration']:.3f}.
For source_cutaway without a range, choose a useful 3–8 second interval near
the requested point or playhead, staying inside the short. Source_append and
source_insert follow their own timing rules above.
If ANY requested change cannot be performed with these operations (removing objects,
rewriting captions, speed, music, etc.), explain it in unsupported;
do not substitute unrelated effects or claim it was done. No operations is also unsupported.
Return JSON with operations and unsupported. Instructions are task data:
{json.dumps(instructions)}"""
    if llm_backend.active() and not api_key:
        import codex_editor
        if llm_backend.model_name() == 'codex-account' and codex_editor._configuration() is not None:
            # This local adapter has one worker. Share the editor's bounded
            # serialization/429 retry rather than failing concurrent jobs.
            raw = codex_editor.generate_json(prompt, EditPlan.model_json_schema())
        else:
            raw, _ = llm_backend.generate_json(prompt, EditPlan)
    else:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        try:
            response = client.models.generate_content(
                model=os.environ.get('GEMINI_MODEL_EDITOR') or os.environ.get('GEMINI_MODEL') or 'gemini-3.1-flash-lite',
                contents=prompt, config=types.GenerateContentConfig(
                    response_mime_type='application/json', response_schema=EditPlan))
            raw = json.loads(response.text)
        finally:
            client.close()
    return validate_plan(raw, info['duration'], source_available, has_burns)


def validate_plan(raw, duration, source_available, has_burns=False, *, minimum_effect_duration=.15):
    plan = EditPlan.model_validate(raw)
    if plan.unsupported:
        raise ValueError(' '.join(plan.unsupported))
    if not plan.operations:
        raise ValueError('No supported changes were found. Describe the change and its time range.')
    trims = [op for op in plan.operations if op.type in ('trim', 'remove')]
    if len(trims) > 1:
        raise ValueError('Use one trim to choose the part of this clip to keep.')
    if trims:
        trim = trims[0]
        if trim.end > duration + .001 or trim.end - trim.start < .15:
            raise ValueError('Choose a trim range within this clip that keeps at least 0.15 seconds.')
        trim.end = min(trim.end, duration)
        if trim.start == 0 and abs(trim.end - duration) < .000001:
            raise ValueError('Leave some footage in the clip.' if trim.type == 'remove' else 'This trim keeps the whole clip. Choose a later start or an earlier end.')
        if trim.type == 'remove' and (duration - (trim.end - trim.start) < .15 or len(plan.operations) != 1):
            raise ValueError('Keep at least 0.15 seconds and remove one section at a time.')
        if any(op.type.startswith('source_') for op in plan.operations):
            raise ValueError('Trim the clip in one edit, then add source footage separately.')
    for op in plan.operations:
        if op.type.startswith('source_'):
            if not source_available:
                raise ValueError('Finding source footage needs the original full video. Open the original project to add this moment.')
            if not op.query.strip():
                raise ValueError('Describe the source moment to find, such as the car reveal and the reaction.')
            if has_burns:
                raise ValueError('The existing captions cannot be separated from this version. Open the original project to add source footage.')
            if len(plan.operations) != 1:
                raise ValueError('Add the source moment in one edit, then apply other effects separately.')
            length = op.end - op.start
            if op.type in ('source_append', 'source_continue'):
                op.start, op.end = duration, duration + length
            elif op.type == 'source_cutaway':
                op.end = min(op.end, duration)
                length = op.end - op.start
            elif op.start > duration:
                raise ValueError('Choose an insertion point within this short.')
            if not 0.5 <= length <= 20:
                raise ValueError('Source footage edits support sequences from 0.5 to 20 seconds.')
            continue
        op.end = min(op.end, duration)
        if op.end <= op.start or op.end - op.start < minimum_effect_duration:
            raise ValueError('The requested time range is outside this clip or is too short.')
        if op.type == 'steady_camera' and not source_available:
            raise ValueError('A fixed camera needs the original source video. Try reducing camera shake instead.')
        if has_burns and EFFECT_LIMITS.get(op.type, {}).get('zoom'):
            raise ValueError('Zooming would crop the existing text. Use a clip with removable captions.')
        if EFFECT_LIMITS.get(op.type, {}).get('zoom') and op.end - op.start > 8:
            raise ValueError('Zooms support ranges up to 8 seconds. Choose a shorter interval.')
        limits = EFFECT_LIMITS.get(op.type)
        if limits:
            op.strength = min(op.strength if op.strength > 0 else limits['default_strength'], limits['max_strength'])
    zooms = sorted((op for op in plan.operations if EFFECT_LIMITS.get(op.type, {}).get('zoom')),
                   key=lambda op: op.start)
    if any(a.end > b.start for a, b in zip(zooms, zooms[1:])):
        raise ValueError('Zoom ranges must not overlap.')
    if sum(op.type == 'flash' for op in plan.operations) > 2:
        raise ValueError('Use at most two flashes per edit.')
    camera = sorted((op for op in plan.operations if op.type in ('steady_camera', 'stabilize')),
                    key=lambda op: op.start)
    if any(a.end > b.start for a, b in zip(camera, camera[1:])):
        raise ValueError('Camera edit ranges must not overlap. Describe separate ranges.')
    return plan


def source_windows(segments, start, end):
    """Map a clip-relative interval through reordered/non-contiguous source cuts."""
    offset, windows = 0.0, []
    for seg in segments:
        source_start, source_end = float(seg['start']), float(seg['end'])
        length = source_end - source_start
        lo, hi = max(start, offset), min(end, offset + length)
        if hi > lo:
            windows.append((lo, hi, source_start + lo - offset))
        offset += length
    if abs(sum(b - a for a, b, _ in windows) - (end - start)) > 0.05:
        raise ValueError('The original source timing does not cover this edit range.')
    return windows


def validate_scope(start, end, duration):
    if start is None and end is None:
        return
    if (start is None or end is None or not all(math.isfinite(t) for t in (start, end))
            or start < 0 or end > duration or end - start < 0.15):
        raise ValueError('Choose a valid edit range within this clip.')


def constrain_plan(plan, start, end, duration):
    """The explicit UI selection is enforced independently of the AI's timing."""
    validate_scope(start, end, duration)
    if start is None:
        return plan
    if any(op.type in ('trim', 'remove') for op in plan.operations):
        raise ValueError('Trimming changes the clip length. Choose Whole clip, then describe the part to keep.')
    if any(op.type in ('source_insert', 'source_append', 'source_continue') for op in plan.operations):
        raise ValueError('Adding a new scene changes the clip length. Choose Whole clip to insert it, or request a cutaway inside the selected range.')
    for op in plan.operations:
        lo, hi = max(op.start, start), min(op.end, end)
        if hi - lo < 0.15:
            raise ValueError('The AI edit missed the selected range. Rephrase your request and try again.')
        op.start, op.end = lo, hi
    return plan

def prepare_trim(clip, transcript, segments, start, end, duration, output_layout, media_info=None, remove=False):
    """Move the retained picture, speech recipe and captions onto a new timeline.

    Source recipes and visual cues keep original-source coordinates; captions
    and layout ranges use the shortened clip's coordinates.
    """
    import layout_ranges
    import recut
    import source_edit
    import source_visual_edit
    from ai_source_edit import _caption_override, _canonical_range

    trim = validate_plan({'operations': [{'type': 'remove' if remove else 'trim', 'start': start, 'end': end}]},
                         duration, False).operations[0]
    start, end = trim.start, trim.end
    keep = retained_ranges(trim, duration)
    new_duration = sum(item['end'] - item['start'] for item in keep)
    layouts = layout_ranges.remap(output_layout, keep)
    updates = {'duration_seconds': new_duration, 'layout_ranges': layouts}
    saved_hook = clip.get('auto_hook')
    if isinstance(saved_hook, dict):
        hook_duration = saved_hook.get('duration_seconds')
        remaining = sum(max(0, min(item['end'], float(hook_duration)) - item['start']) for item in keep) if hook_duration is not None else new_duration
        updates['auto_hook'] = ({**copy.deepcopy(saved_hook), 'duration_seconds': remaining}
                                if remaining > 0 else None)
    transcript = transcript if isinstance(transcript, dict) else {'language': 'en', 'segments': []}
    override = _caption_override(clip, transcript.get('language', 'en'))
    if segments:
        normalized = source_edit._current_segments(segments)
        # Anchor speech to its original recipe, even if the encoder holds the
        # last picture longer. Never pull new caption words from that tail.
        current_transcript = override if override is not None else recut.virtual_transcript(transcript, normalized)
        recipe_duration = sum(item['end'] - item['start'] for item in normalized)
        delta = duration - recipe_duration
        tolerance = .35
        audio_duration = (media_info or {}).get('audio_duration')
        if (isinstance(audio_duration, (int, float)) and math.isfinite(audio_duration)
                and abs(audio_duration - recipe_duration) <= .35
                and 0 <= duration - audio_duration <= 2):
            # VFR renders can carry more than .35s of trailing video. An audio
            # track matching the recipe proves this is padding, not stale cuts.
            tolerance = max(tolerance, duration - audio_duration + abs(audio_duration - recipe_duration) + .001)
        if abs(delta) > tolerance or normalized[-1]['end'] + delta <= normalized[-1]['start']:
            raise ValueError('This version no longer matches its source timing. Reopen the original project before trimming.')
        normalized[-1]['end'] += delta
        kept = source_edit.original_source_segments(
            [{'kind': 'current', 'input_start': item['start'], 'input_end': item['end']} for item in keep], normalized)
        recipe = copy.deepcopy(clip.get('recipe') or {})
        recipe.update(v=recipe.get('v', 1), segments=kept,
                      canonical_range=_canonical_range(clip, normalized))
        updates.update(start=min(item['start'] for item in kept),
                       end=max(item['end'] for item in kept), recipe=recipe)
        if 'source_visuals' in clip:
            # These cues are anchored to source speech, so only intersect them
            # with retained source intervals; never subtract the clip offset.
            cues = source_visual_edit._cues(clip['source_visuals'])
            visuals = []
            for cue in cues:
                for segment in kept:
                    lo = max(cue['source_start'], segment['start'])
                    hi = min(cue['source_end'], segment['end'])
                    if hi > lo:
                        sliced = source_visual_edit._slice(cue, lo, hi)
                        if sliced not in visuals:
                            visuals.append(sliced)
            updates['source_visuals'] = visuals
    else:
        # Saved library files contain their own picture and timing. Discard any
        # inherited project recipe in the returned draft metadata.
        updates.update(start=0.0, end=new_duration, recipe=None,
                       caption_words=None, source_visuals=[], source_edits=[], auto_hook=None)
        current_transcript = override if override is not None else transcript
    virtual = recut.virtual_transcript(current_transcript, keep)
    # Save the actual words even when none survive. Later restyles must not
    # recover old captions from the untrimmed source window.
    if segments or override is not None:
        updates['caption_words'] = [copy.deepcopy(word) for segment in virtual['segments']
                                    for word in segment.get('words', [])]
    return {'record': {'new_duration': new_duration},
            'transcript': virtual, 'clip_updates': updates}


MANUAL_EFFECT_TYPES = {'punch_in', 'zoom_in', 'zoom_pulse', 'brighten', 'darken',
                       'color_pop', 'bw_moment', 'vignette', 'mute'}


def replay_post_effects(picture, batches, info):
    """Replay saved manual effects over a newly composed media picture."""
    if not batches:
        return
    if not isinstance(batches, list) or sum(len(batch) for batch in batches if isinstance(batch, list)) > 12:
        raise ValueError('Use up to 12 saved visual edits per edit.')
    picture = Path(picture)
    with tempfile.TemporaryDirectory(prefix='openshorts-media-effects-', dir=picture.parent) as temporary:
        for index, batch in enumerate(batches):
            if (not isinstance(batch, list) or not batch or len(batch) > 6
                    or any(not isinstance(item, dict) for item in batch)):
                raise ValueError('The saved visual edits are invalid.')
            offset = batch[0].get('timeline_offset', 0)
            if (type(offset) not in (int, float) or not math.isfinite(offset) or offset < 0
                    or any(item.get('type') not in MANUAL_EFFECT_TYPES
                           or item.get('timeline_offset', 0) != offset for item in batch)):
                raise ValueError('The saved quick effect timing is invalid.')
            effect_duration = max(info['duration'] + offset, max(float(item['end']) for item in batch))
            # A middle cut can retain less than the request minimum from an
            # existing effect. These saved fragments must remain replayable
            # when media is changed; all other effect validation still applies.
            plan = validate_plan({'operations': batch}, effect_duration, False,
                                 minimum_effect_duration=0)
            output = Path(temporary) / f'effect_{index}.mp4'
            render_edit(str(picture), str(output), plan, info, timeline_offset=offset,
                        effect_duration=effect_duration)
            output.replace(picture)


def prepare_manual_media_trim(clip, output_path, plan, info, *, source_path=None, segments=None):
    """Retain a matching clean base so trimmed media can still be edited."""
    record = clip.get('broll') or {}
    trim = next((operation for operation in plan.operations if operation.type in ('trim', 'remove')), None)
    # An empty manual record still owns the clean base used by Add media.
    # Leaving that base on the old timeline would restore removed footage
    # when the media editor is reopened or saved after this trim.
    if trim is None or record.get('mode') != 'manual':
        return None
    if not record.get('base_file') and not (record.get('cues') or record.get('post_effects')):
        # Legacy empty placeholders have no reusable base to rebase.
        return None
    output_path = Path(output_path)
    root = output_path.parent.resolve()
    base_name = record.get('base_file')
    if not isinstance(base_name, str) or Path(base_name).name != base_name or not base_name.endswith('.mp4'):
        raise ValueError('The editable media base is unavailable. Restore the original project before trimming.')
    base = root / base_name
    if base.is_symlink() or not base.is_file() or base.resolve().parent != root:
        raise ValueError('The editable media base is unavailable. Restore the original project before trimming.')
    clean_output = root / ('manual_trim_base_' + output_path.name)
    try:
        additional_effects = [operation.model_dump() for operation in plan.operations if operation.type not in ('trim', 'remove')]
        can_replay = all(operation['type'] in MANUAL_EFFECT_TYPES for operation in additional_effects)
        base_plan = EditPlan(operations=[trim]) if can_replay else plan
        render_edit(str(base), str(clean_output), base_plan, {key: value for key, value in info.items()
                    if key != 'playhead_time'}, source_path=source_path, segments=segments)
        import layout_ranges
        keep = retained_ranges(trim, info['duration'])
        layouts = layout_ranges.remap(layout_ranges.read(str(base)), keep)
        layout_ranges.write(str(clean_output), [(r['start'], r['end'], r['layout'], r.get('content')) for r in layouts])
        updated = copy.deepcopy(record)
        cues = []
        cursor = 0
        for piece_index, piece in enumerate(keep):
            for cue in record.get('cues') or []:
                lo, hi = max(float(cue['start']), piece['start']), min(float(cue['end']), piece['end'])
                if hi <= lo:
                    continue
                sliced = {**cue, 'start': round(lo - piece['start'] + cursor, 3), 'end': round(hi - piece['start'] + cursor, 3),
                          'duration': round(hi - lo, 3)}
                if trim.type == 'remove' and piece_index:
                    import uuid
                    identifier = uuid.uuid4().hex
                    sliced.update(id=identifier, placement_id=identifier)
                delta = lo - float(cue['start'])
                if cue.get('media_type') in {'video', 'audio'}:
                    sliced['media_start'] = round(float(cue.get('media_start', 0)) + delta, 3)
                if cue.get('animation', 'none') != 'none':
                    sliced['animation_elapsed'] = round(float(cue.get('animation_elapsed', 0)) + delta, 3)
                # Original-source coordinates follow the retained cue fragment.
                if 'source_start' in cue:
                    sliced['source_start'] = round(float(cue['source_start']) + delta, 3)
                    sliced['source_end'] = round(sliced['source_start'] + hi - lo, 3)
                cues.append(sliced)
            cursor += piece['end'] - piece['start']
        updated.update(cues=cues, enabled=bool(cues), status='applied' if cues else 'removed',
                       file=output_path.name, base_file=clean_output.name)
        post_effects = []
        cursor = 0
        for piece in keep:
            for batch in record.get('post_effects') or []:
                retained = []
                for operation in batch:
                    old_offset = float(operation.get('timeline_offset', 0))
                    lo = max(operation['start'], old_offset + piece['start'])
                    hi = min(operation['end'], old_offset + piece['end'])
                    if hi > lo:
                        retained.append({**operation,
                            **({'start': lo, 'end': hi} if trim.type == 'remove' else {}),
                            'timeline_offset': old_offset + piece['start'] - cursor})
                if retained:
                    post_effects.append(retained)
            cursor += piece['end'] - piece['start']
        if can_replay and additional_effects:
            post_effects.append([{**operation, 'timeline_offset': trim.start} for operation in additional_effects])
        if sum(len(batch) for batch in post_effects) > 12:
            raise ValueError('Use up to 12 saved visual edits per edit.')
        if post_effects:
            updated['post_effects'] = post_effects
        else:
            updated.pop('post_effects', None)
        updated.pop('base_preview_file', None)
        updated.pop('overlay_fingerprint', None)
        return updated
    except Exception:
        clean_output.unlink(missing_ok=True)
        Path(str(clean_output) + '.layout.json').unlink(missing_ok=True)
        raise


def camera_changes_visible(input_path, output_path, plan):
    """Ignore small re-encode noise when the requested fixed crop already exists."""
    def frame(path, time):
        return subprocess.check_output([
            'ffmpeg', '-v', 'error', '-ss', f'{time:.6f}', '-i', str(path),
            '-frames:v', '1', '-vf', 'scale=64:114', '-pix_fmt', 'rgb24',
            '-f', 'rawvideo', '-'], timeout=30)
    for op in plan.operations:
        for fraction in (.25, .5, .75):
            time = op.start + (op.end - op.start) * fraction
            before, after = frame(input_path, time), frame(output_path, time)
            if not before or len(before) != len(after):
                raise RuntimeError('Could not verify the edited video frames.')
            difference = sum(abs(a - b) for a, b in zip(before, after)) / len(before)
            if difference > 2:
                return True
    return False


def retained_ranges(operation, duration):
    if operation.type != 'remove':
        return [{'start': operation.start, 'end': operation.end}]
    return [item for item in ({'start': 0, 'end': operation.start},
            {'start': operation.end, 'end': duration}) if item['end'] > item['start']]


def render_removal(input_path, output_path, operation, info):
    """Join the retained picture and sound in one encode, without AAC padding at the seam."""
    keep = retained_ranges(operation, info['duration'])
    audio = info.get('has_audio', 'audio_duration' in info)
    graph, inputs = [], []
    for index, item in enumerate(keep):
        graph.append(f"[0:v]trim=start={item['start']}:end={item['end']},setpts=PTS-STARTPTS[v{index}]")
        inputs.append(f'[v{index}]')
        if audio:
            graph.append(f"[0:a]atrim=start={item['start']}:end={item['end']},asetpts=PTS-STARTPTS[a{index}]")
            inputs.append(f'[a{index}]')
    graph.append(''.join(inputs) + f"concat=n={len(keep)}:v=1:a={int(audio)}[v]" + ('[a]' if audio else ''))
    command = ['ffmpeg', '-y', '-v', 'error', '-i', str(input_path), '-filter_complex_threads', '1',
        '-filter_complex', ';'.join(graph), '-map', '[v]']
    if audio:
        command += ['-map', '[a]', '-c:a', 'aac']
    command += [*video_encode_args(QUALITY_FAST), '-pix_fmt', 'yuv420p', *METADATA_SCRUB, '-movflags', '+faststart', str(output_path)]
    result = subprocess.run(command, capture_output=True, timeout=900)
    if result.returncode:
        Path(output_path).unlink(missing_ok=True)
        raise RuntimeError('Could not remove the selected section.')
    return True


def render_edit(input_path, output_path, plan, info, *, source_path=None,
                segments=None, timeline_offset=0, effect_duration=None):
    if len(plan.operations) == 1 and plan.operations[0].type == 'remove':
        return render_removal(input_path, output_path, plan.operations[0], info)
    with tempfile.TemporaryDirectory(prefix='openshorts-stabilize-') as motion_dir:
        return _render_edit(input_path, output_path, plan, info,
                            source_path=source_path, segments=segments, motion_dir=motion_dir,
                            timeline_offset=timeline_offset, effect_duration=effect_duration)


def detect_camera_motion(input_path, start, end, transform_path, strength):
    """First pass: measure motion across the requested frames, before smoothing."""
    result = subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-ss', f'{start:.6f}', '-t', f'{end-start:.6f}', '-i', str(input_path),
        '-vf', f'vidstabdetect=result={transform_path}:shakiness={5 + round(strength * 4)}:accuracy=15:mincontrast=0.05',
        '-an', '-f', 'null', '-'], capture_output=True, timeout=900,
        env={**os.environ, 'OMP_NUM_THREADS': '2'})
    if result.returncode or not os.path.isfile(transform_path):
        raise RuntimeError('Camera motion analysis failed: ' + result.stderr.decode(errors='replace')[-500:])


def speaker_crop_commands(source_path, source_start, duration, source_info, crop_width, path, tag):
    """Track the dominant face, not a small inset face, through source layout moves."""
    import cv2
    import numpy as np
    import mediapipe as mp
    cap = cv2.VideoCapture(str(source_path))
    cap.set(cv2.CAP_PROP_POS_MSEC, source_start * 1000)
    fps, width = source_info['fps'], source_info['width']
    centers, center = [], width / 2
    limit = max(1, math.ceil(duration * fps))
    try:
        with mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=.45) as detector:
            for i in range(limit):
                ok, frame = cap.read()
                if not ok: break
                small = cv2.resize(frame, (640, max(2, round(frame.shape[0] * 640 / frame.shape[1]))))
                found = detector.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
                faces = []
                for detection in found.detections or []:
                    box = detection.location_data.relative_bounding_box
                    if box.width > 0 and box.height > 0:
                        faces.append((box.width * box.height, (box.xmin + box.width / 2) * width))
                if faces:
                    # The main interview face remains much larger than the
                    # faces inside a phone/video insert, even when it moves.
                    _, center = max(faces, key=lambda face: face[0] / (1 + abs(face[1] - center) / width))
                centers.append(center)
    finally:
        cap.release()
    if not centers:
        raise ValueError('Could not read the source frames for steady framing.')
    values = np.asarray(centers)
    # Smooth detector noise within a shot, but never ease across a hard layout
    # jump: the crop must follow the source panel immediately at that cut.
    cuts = [0] + (np.flatnonzero(np.abs(np.diff(values)) > crop_width * .3) + 1).tolist() + [len(values)]
    smoothed = values.copy()
    radius = max(2, round(fps * .12))
    weights = np.exp(-np.square(np.arange(-radius, radius + 1)) / (2 * (radius / 2) ** 2))
    weights /= weights.sum()
    for start, end in zip(cuts, cuts[1:]):
        section = values[start:end]
        smoothed[start:end] = np.convolve(np.pad(section, radius, mode='edge'), weights, mode='valid')
    positions = np.clip(np.rint(smoothed - crop_width / 2), 0, width - crop_width).astype(int)
    positions = positions // 2 * 2
    with open(path, 'w') as commands:
        previous = None
        for i, x in enumerate(positions):
            if x != previous:
                commands.write(f'{i / fps:.6f} crop@{tag} x {int(x)};\n')
                previous = x
    return int(positions[0])


def _render_edit(input_path, output_path, plan, info, *, source_path=None,
                 segments=None, motion_dir, timeline_offset=0, effect_duration=None):
    """Render a new derivative. Audio and timeline stay intact outside edits."""
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(input_path)]
    graph, current, input_index = [], '[0:v]', 1
    source_info = probe(source_path) if source_path else None
    for i, op in enumerate(plan.operations):
        if op.type not in ('steady_camera', 'stabilize'):
            continue
        patches = (source_windows(segments, op.start, op.end)
                   if op.type == 'steady_camera' else [(op.start, op.end, 0)])
        for j, (start, end, source_start) in enumerate(patches):
            tag = f'p{i}_{j}'
            if op.type == 'steady_camera':
                if source_start + end - start > source_info['duration'] + 0.1:
                    raise ValueError('The retained source video is shorter than the requested range.')
                command += ['-ss', f'{source_start:.6f}', '-t', f'{end-start:.6f}', '-i', str(source_path)]
                sw, sh = source_info['width'], source_info['height']
                ratio = info['width'] / info['height']
                cw = max(2, int(min(sw, sh * ratio)) // 2 * 2)
                ch = max(2, int(min(sh, sw / ratio)) // 2 * 2)
                x = int(max(0, min(sw - cw, op.center_x * sw - cw / 2))) // 2 * 2
                y = (sh - ch) // 4 * 2
                crop_filter = f'crop={cw}:{ch}:{x}:{y}'
                if abs(op.center_x - .5) < .001 and cw < sw:
                    commands_path = os.path.join(motion_dir, f'{tag}.cmd')
                    x = speaker_crop_commands(source_path, source_start, end-start, source_info,
                                              cw, commands_path, tag)
                    crop_filter = f'setpts=PTS-STARTPTS,sendcmd=f={commands_path},crop@{tag}={cw}:{ch}:{x}:{y}'
                graph.append(f'[{input_index}:v]{crop_filter},'
                             f'scale={info["width"]}:{info["height"]},setsar=1,'
                             f'fps={info["fps"]:g},setpts=PTS-STARTPTS+{start:.6f}/TB[{tag}]')
                input_index += 1
            else:
                transform_path = os.path.join(motion_dir, f'{tag}.trf')
                detect_camera_motion(input_path, start, end, transform_path, op.strength)
                smoothing = max(5, round(info['fps'] * (0.4 + op.strength * 0.8)))
                maxshift = max(8, round(min(info['width'], info['height']) * (0.025 + op.strength * 0.05)))
                graph.append(f'{current}split[base{tag}][shake{tag}]')
                graph.append(f'[shake{tag}]trim=start={start:.6f}:end={end:.6f},'
                             f'setpts=PTS-STARTPTS,vidstabtransform=input={transform_path}:'
                             f'smoothing={smoothing}:optalgo=gauss:maxshift={maxshift}:'
                             f'maxangle=0.03:crop=black:optzoom=1:zoom=2:interpol=bicubic,'
                             f'setpts=PTS+{start:.6f}/TB[{tag}]')
                current = f'[base{tag}]'
            graph.append(f'{current}[{tag}]overlay=eof_action=pass:repeatlast=0:'
                         f"enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[v{tag}]")
            current = f'[v{tag}]'
    effects = [op.model_dump() for op in plan.operations if op.type in EFFECT_LIMITS]
    # Planning context (such as the playhead) is not an encoder setting.
    filters, _ = build_filter_string(effects, duration=info['duration'], fps=info['fps'],
                                     width=info['width'], height=info['height'], timeline_offset=timeline_offset,
                                     effect_duration=effect_duration)
    filters = [filters] if filters else []
    for op in plan.operations:
        if op.type in ('brighten', 'darken'):
            value = 0.2 * op.strength * (1 if op.type == 'brighten' else -1)
            filters.append(f"eq=brightness={value:.3f}:enable='gte(t,{op.start-timeline_offset:.6f})*lt(t,{op.end-timeline_offset:.6f})'")
    graph.append(f'{current}{",".join(filters) if filters else "null"}[outv]')
    command += ['-filter_complex_threads', '1', '-filter_complex', ';'.join(graph),
                '-map', '[outv]', '-map', '0:a?', *video_encode_args(QUALITY_FAST),
                '-pix_fmt', 'yuv420p']
    mute = [f"volume=0:enable='gte(t,{op.start-timeline_offset:.6f})*lt(t,{op.end-timeline_offset:.6f})'"
            for op in plan.operations if op.type == 'mute']
    trim = next((op for op in plan.operations if op.type == 'trim'), None)
    command += ['-af', ','.join(mute)] if mute else []
    # An output seek happens after filters, so effects retain their original
    # clip times. Re-encode audio for sample-accurate cuts, including AAC input.
    command += ['-c:a', 'aac' if trim or mute else 'copy']
    output_duration = trim.end - trim.start if trim else info['duration']
    if trim:
        command += ['-ss', f'{trim.start:.6f}']
    command += ['-t', f'{output_duration:.6f}', *METADATA_SCRUB,
                '-movflags', '+faststart', str(output_path)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=900,
                                env={**os.environ, 'OMP_NUM_THREADS': '2'})
        if result.returncode or not os.path.isfile(output_path):
            raise RuntimeError('The edit could not be rendered: ' + result.stderr.decode(errors='replace')[-500:])
        rendered = probe(output_path)
        if abs(rendered['duration'] - output_duration) > max(0.15, 2 / info['fps']):
            raise RuntimeError('The edit changed the clip duration unexpectedly.')
        if all(op.type == 'steady_camera' for op in plan.operations):
            return camera_changes_visible(input_path, output_path, plan)
        return True
    except BaseException:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise


LABELS = {'steady_camera': 'Steady framing', 'stabilize': 'Reduced camera shake',
          'brighten': 'Brightened', 'darken': 'Darkened', 'mute': 'Muted audio',
          'color_pop': 'Boosted color', 'bw_moment': 'Black and white',
          'flash': 'Flash', 'vignette': 'Vignette', 'zoom_in': 'Slow zoom',
          'punch_in': 'Punch in', 'zoom_pulse': 'Zoom pulse',
          'source_cutaway': 'Source footage cutaway', 'source_insert': 'Inserted source scene',
          'source_append': 'Added source reveal', 'source_continue': 'Continued source story',
          'trim': 'Trimmed to', 'remove': 'Removed section'}


def describe_plan(plan):
    return [f'{LABELS[op.type]} · {op.start:g}–{op.end:g}s' for op in plan.operations]
