"""Manual middle cuts join picture/audio and retain editable timing."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import ai_clip_edit as ai
from test_ai_trim import trim_endpoint, trim_media, pixel, frequency, samples


def removal(start, end, duration):
    return ai.validate_plan({'operations': [{'type': 'remove', 'start': start, 'end': end}]}, duration, False)


def test_remove_13_to_16_rebases_words_recipe_hook_and_layout():
    transcript = {'segments': [{'words': [
        {'word': 'before', 'start': 12, 'end': 13},
        {'word': 'removed', 'start': 14, 'end': 15},
        {'word': 'after', 'start': 17, 'end': 18}]}]}
    clip = {'start': 0, 'end': 40, 'auto_hook': {'text': 'Hook', 'duration_seconds': 20}}
    result = ai.prepare_trim(clip, transcript, [{'start': 0, 'end': 40}], 13, 16, 40,
        [{'start': 0, 'end': 40, 'layout': 'general'}], remove=True)
    update = result['clip_updates']
    assert update['duration_seconds'] == 37
    assert update['recipe']['segments'] == [{'start': 0, 'end': 13}, {'start': 16, 'end': 40}]
    assert [word['word'].strip() for word in update['caption_words']] == ['before', 'after']
    assert update['caption_words'][1]['start'] == 14
    assert update['auto_hook']['duration_seconds'] == 17
    assert update['layout_ranges'][-1]['end'] == 37


@pytest.mark.parametrize('start,end', [(0, 6), (3, 2), (1, 8), (2, 2.01)])
def test_invalid_removal_is_rejected(start, end):
    with pytest.raises(ValueError):
        removal(start, end, 6)


def test_middle_cut_endpoint_removes_frames_and_keeps_sound_in_sync(trim_endpoint, trim_media, monkeypatch):
    endpoint = trim_endpoint
    shutil.copyfile(trim_media, endpoint.directory / 'clip.mp4')
    monkeypatch.setattr(ai, 'probe', endpoint.real_probe)
    monkeypatch.setattr(ai, 'render_edit', endpoint.real_render)
    monkeypatch.setattr(ai, 'plan_edit', lambda *args, **kwargs: pytest.fail('Manual cut called AI'))
    response = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', start=1.5, end=4, mode='remove'), None))
    output = endpoint.directory / Path(response['new_video_url']).name
    assert response['duration_seconds'] == pytest.approx(3.5)
    assert ai.probe(output)['duration'] == pytest.approx(3.5, abs=.11)
    assert pixel(output, .5)[0] > 200  # Retain the red intro.
    assert pixel(output, 1.7)[1] > 80  # Jump directly to green; blue was removed.
    assert frequency(samples(output, .4)) == pytest.approx(440, abs=10)
    assert frequency(samples(output, 1.8)) == pytest.approx(880, abs=10)
    saved = json.loads(endpoint.meta.read_text())['shorts'][0]
    assert saved['recipe']['segments'] == [{'start': 10, 'end': 11.5}, {'start': 14, 'end': 16}]
    assert [word['word'].strip() for word in saved['caption_words']] == ['intro']


def test_cut_rebases_crossing_video_layer_and_its_clean_base(trim_media, tmp_path):
    base = tmp_path / 'base.mp4'
    shutil.copyfile(trim_media, base)
    clip = {'broll': {'mode': 'manual', 'base_file': base.name, 'file': base.name,
        'cues': [{'id': 'overlay', 'media_type': 'video', 'start': 1, 'end': 5,
                  'media_start': 2, 'animation': 'fade', 'animation_elapsed': 0}]}}
    plan = removal(2, 4, 6)
    record = ai.prepare_manual_media_trim(clip, tmp_path / 'cut.mp4', plan, ai.probe(base))
    assert [(c['start'], c['end'], c['media_start']) for c in record['cues']] == [(1, 2, 2), (2, 3, 5)]
    assert record['cues'][0]['id'] != record['cues'][1]['id']
    assert ai.probe(tmp_path / record['base_file'])['duration'] == pytest.approx(4, abs=.11)
    assert pixel(tmp_path / record['base_file'], 2.2)[1] > 80


def test_removal_supports_video_without_audio(trim_media, tmp_path):
    silent, output = tmp_path / 'silent.mp4', tmp_path / 'cut.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(trim_media), '-an', '-c:v', 'copy', str(silent)], check=True)
    ai.render_edit(silent, output, removal(1.5, 4, 6), ai.probe(silent))
    assert ai.probe(output)['duration'] == pytest.approx(3.5, abs=.11)
    assert 'audio_duration' not in ai.probe(output)


def test_repeated_cuts_handle_zero_length_transcription_words():
    from ai_source_edit import _caption_override
    transcript = {'segments': [{'words': [
        {'word': 'hello', 'start': 1, 'end': 1},
        {'word': 'world', 'start': 2, 'end': 3}]}]}
    original = {'start': 0, 'end': 6}
    first = ai.prepare_trim(original, transcript, [{'start': 0, 'end': 6}], 0.5, 6, 6, [])
    updated = {**original, **first['clip_updates']}
    assert all(word['end'] > word['start'] for word in updated['caption_words'])
    # Existing projects containing zero-duration overrides remain editable too.
    updated['caption_words'][0]['end'] = updated['caption_words'][0]['start']
    second = ai.prepare_trim(updated, transcript, updated['recipe']['segments'], .2, 5.5, 5.5, [])
    assert [w['word'].strip() for w in second['clip_updates']['caption_words']] == ['hello', 'world']
    assert all(w['end'] > w['start'] for w in second['clip_updates']['caption_words'])
    with pytest.raises(ValueError, match='invalid timings'):
        _caption_override({'caption_words': [{'word': 'bad', 'start': 2, 'end': 1}]}, 'en')
