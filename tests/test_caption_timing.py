import asyncio
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from clip_timing import recover_caption_timing
from test_ai_trim import trim_endpoint
from test_clip_timing import make_legacy_render


@pytest.fixture
def caption_project(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    _, _, _, transcript, info = make_legacy_render(endpoint.directory)
    cue = {'id': 'sound', 'asset': 'effect.m4a', 'media_type': 'audio',
           'start': 1, 'end': 2, 'duration': 1, 'media_start': .5, 'volume': .72}
    endpoint.clip.update(start=10, end=14,
        video_url='/videos/trim-job/subtitled_999_media.mp4',
        broll={'mode': 'manual', 'base_file': 'clip.mp4',
               'base_preview_file': 'subtitled_123_clip.mp4',
               'cues': [cue], 'source_cues': [{**cue, 'source_start': 11, 'source_end': 12}]})
    (endpoint.directory / 'subtitled_999_media.mp4').write_bytes(b'existing captions and sound')
    endpoint.meta.write_text(json.dumps({'shorts': [endpoint.clip], 'transcript': transcript}))
    monkeypatch.setattr('ai_clip_edit.probe', lambda _: {**info, 'width': 160, 'height': 96, 'fps': 10})
    monkeypatch.setattr(endpoint.app, '_strip_burned_captions', lambda *args, **kwargs: 'clip.mp4')
    monkeypatch.setattr(endpoint.app, 'reserve_managed_action', AsyncMock(return_value=None))
    return endpoint, transcript


def test_recovered_preview_uses_video_words_and_duration_without_saving(caption_project):
    endpoint, _ = caption_project
    before = endpoint.meta.read_bytes(), deepcopy(endpoint.clip)
    response = asyncio.run(endpoint.app.get_clip_transcript('trim-job', 0, None))
    assert response['durationSec'] == pytest.approx(6.2)
    assert [word['text'] for word in response['captions']] == ['phrase0', 'is', 'here',
        'phrase1', 'is', 'here', 'phrase2', 'is', 'here']
    assert response['captions'][0]['startMs'] == 500
    assert (endpoint.meta.read_bytes(), endpoint.clip) == before


@pytest.mark.parametrize('edited_words', [None, [{'text': 'Custom', 'startMs': 500, 'endMs': 1200}]])
def test_caption_save_commits_repaired_timing_and_keeps_media_and_style(caption_project, monkeypatch, edited_words):
    endpoint, _ = caption_project
    original_cues = deepcopy(endpoint.clip['broll']['cues'])
    burned = []
    def burn(source, output, subtitle, transcript, start, end, style, layouts):
        burned.append((transcript, start, end, style))
        Path(output).write_bytes(b'corrected captions')
        return True
    monkeypatch.setattr(endpoint.app, '_burn_caption_style', burn)
    result = asyncio.run(endpoint.app.add_subtitles(endpoint.app.SubtitleRequest(
        job_id='trim-job', clip_index=0, style='karaoke', font_name='Anton',
        max_chars=1, words=edited_words), None))
    assert result['success'] and result['duration_seconds'] == pytest.approx(6.2)
    assert burned[0][1:3] == (0, 6.2)
    assert burned[0][3]['max_chars'] == 1
    saved = json.loads(endpoint.meta.read_text())['shorts'][0]
    assert saved['recipe']['segments'] == [{'start': 100, 'end': 106.2}]
    assert saved['broll']['cues'] == original_cues
    assert saved['broll']['source_cues'][0]['source_start'] == 101
    assert saved['caption_words'] == endpoint.clip['caption_words'] == result['caption_words']
    assert saved['caption_words'][0]['word'].strip() == ('Custom' if edited_words else 'phrase0')
    reopened = asyncio.run(endpoint.app.get_clip_transcript('trim-job', 0, None))
    assert reopened['durationSec'] == pytest.approx(6.2)
    assert reopened['captions'][0]['text'] == ('Custom' if edited_words else 'phrase0')


def test_failed_caption_render_does_not_commit_recovered_metadata(caption_project, monkeypatch):
    endpoint, _ = caption_project
    before = endpoint.meta.read_bytes(), deepcopy(endpoint.clip)
    monkeypatch.setattr(endpoint.app, '_burn_caption_style', lambda *args: False)
    with pytest.raises(endpoint.app.HTTPException):
        asyncio.run(endpoint.app.add_subtitles(endpoint.app.SubtitleRequest(job_id='trim-job', clip_index=0), None))
    assert (endpoint.meta.read_bytes(), endpoint.clip) == before


def test_recovery_requires_manual_media_with_a_safe_base(caption_project):
    endpoint, transcript = caption_project
    for record in ({'mode': 'generated'}, {'mode': 'manual', 'base_file': '../elsewhere.mp4',
                   'base_preview_file': 'subtitled_123_clip.mp4'}):
        assert recover_caption_timing(endpoint.directory, {**endpoint.clip, 'broll': record}, transcript) == {}
