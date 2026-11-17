"""Opt-in FFmpeg integration using the user's required real video, never mocks.

Run inside the configured backend with OPENSHORTS_REAL_TEST_VIDEO pointing at
the Alex Hormozi MP4. This complements browser E2E; it does not replace it.
"""
import json
import os
from pathlib import Path
import subprocess

import numpy as np
import pytest

import ai_clip_edit as ai


SOURCE = os.environ.get('OPENSHORTS_REAL_TEST_VIDEO')
pytestmark = pytest.mark.skipif(not SOURCE, reason='Set OPENSHORTS_REAL_TEST_VIDEO for real-video integration')


def _audio(path, start, duration=.3):
    return np.frombuffer(subprocess.check_output([
        'ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(path),
        '-t', str(duration), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-']), dtype='<f4')


def _frame(path, time):
    return np.frombuffer(subprocess.check_output([
        'ffmpeg', '-v', 'error', '-ss', str(time), '-i', str(path),
        '-frames:v', '1', '-vf', 'scale=45:80', '-pix_fmt', 'rgb24',
        '-f', 'rawvideo', '-']), dtype=np.uint8).astype(float)


def test_ten_consecutive_real_video_trims_keep_duration_picture_and_audio(tmp_path):
    source = Path(SOURCE)
    assert source.is_file(), f'Required real source is inaccessible: {source}'
    base = tmp_path / 'alex_9x16.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-ss', '311', '-i', str(source), '-t', '12',
        '-vf', 'scale=180:320:force_original_aspect_ratio=increase,crop=180:320,setsar=1',
        '-r', '24', '-c:v', 'libx264', '-crf', '18', '-c:a', 'aac', str(base)], check=True)
    current = base
    initial = ai.probe(base)
    assert initial['duration'] == pytest.approx(12, abs=.1)
    clip = {'start': 0, 'end': initial['duration']}
    segments = [{'start': 0, 'end': initial['duration']}]
    results = []
    for index in range(10):
        info = ai.probe(current)
        if index % 3 == 2:
            operation = {'type': 'remove', 'start': info['duration'] / 2,
                         'end': info['duration'] / 2 + .25}
        else:
            operation = {'type': 'trim', 'start': .125 if index % 3 == 0 else 0,
                         'end': info['duration'] - .125}
        plan = ai.validate_plan({'operations': [operation]}, info['duration'], False)
        expected = sum(part['end'] - part['start'] for part in ai.retained_ranges(plan.operations[0], info['duration']))
        output = tmp_path / f'alex_trim_{index + 1:02d}.mp4'
        ai.render_edit(current, output, plan, info)
        updates = ai.prepare_trim(clip, None, segments, operation['start'], operation['end'],
                                  info['duration'], [], media_info=info,
                                  remove=operation['type'] == 'remove')['clip_updates']
        rendered = ai.probe(output)
        assert rendered['width'] * 16 == rendered['height'] * 9
        assert rendered['has_audio'] is True
        assert rendered['duration'] == pytest.approx(expected, abs=.1)
        assert sum(part['end'] - part['start'] for part in updates['recipe']['segments']) == pytest.approx(expected)
        source_time = updates['recipe']['segments'][0]['start'] + .2
        picture_error = float(np.abs(_frame(base, source_time) - _frame(output, .2)).mean())
        assert picture_error < 20
        original, edited = _audio(base, source_time), _audio(output, .2)
        length = min(len(original), len(edited))
        correlation = float(np.corrcoef(original[:length], edited[:length])[0, 1])
        assert correlation > .75
        results.append({'iteration': index + 1, 'operation': operation,
                        'expected_seconds': expected, 'actual_seconds': rendered['duration'],
                        'audio_correlation': correlation, 'picture_mean_error': picture_error})
        clip.update(updates)
        segments, current = clip['recipe']['segments'], output
    report = {'source': source.name, 'source_start_seconds': 311,
              'aspect_ratio': '9:16', 'iterations': results}
    (tmp_path / 'trim-stress-results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
