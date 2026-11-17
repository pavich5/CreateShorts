"""A middle cut may retain a valid effect fragment shorter than a new request."""
import subprocess

import numpy as np
import pytest

import ai_clip_edit as ai


def _pixel(path, time):
    return np.frombuffer(subprocess.check_output([
        'ffmpeg', '-v', 'error', '-ss', str(time), '-i', str(path),
        '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24',
        '-f', 'rawvideo', '-']), dtype=np.uint8).astype(float)


def test_middle_cut_effect_fragment_replays_without_changing_preview(tmp_path):
    source, effected, cut = (tmp_path / name for name in ('base.mp4', 'effect.mp4', 'cut.mp4'))
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=gray:s=90x160:r=24:d=4', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(source)], check=True)
    batch = [{'type': 'brighten', 'start': 1.9, 'end': 2.2, 'strength': .5}]
    ai.render_edit(source, effected, ai.validate_plan({'operations': batch}, 4, False), ai.probe(source))
    clip = {'broll': {'mode': 'manual', 'base_file': source.name,
                     'cues': [], 'post_effects': [batch]}}
    plan = ai.validate_plan({'operations': [{'type': 'remove', 'start': 2, 'end': 3}]}, 4, False)
    ai.render_edit(effected, cut, plan, ai.probe(effected))
    record = ai.prepare_manual_media_trim(clip, cut, plan, ai.probe(effected))
    fragment = record['post_effects'][0][0]
    assert fragment['end'] - fragment['start'] == pytest.approx(.1)
    rebuilt = tmp_path / record['base_file']
    ai.replay_post_effects(rebuilt, record['post_effects'], ai.probe(rebuilt))
    assert ai.probe(rebuilt)['duration'] == pytest.approx(ai.probe(cut)['duration'], abs=.05)
    assert np.abs(_pixel(rebuilt, 1.95) - _pixel(cut, 1.95)).mean() < 3
    assert (_pixel(rebuilt, 1.95) - _pixel(rebuilt, 1.5)).mean() > 10
    # The user-facing minimum for new edits remains unchanged.
    with pytest.raises(ValueError, match='too short'):
        ai.validate_plan({'operations': [{'type': 'brighten', 'start': 1.9, 'end': 2}]}, 4, False)
