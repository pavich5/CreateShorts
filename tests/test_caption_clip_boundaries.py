"""Caption events must stay inside the picture after every trim."""
import asyncio
import json
import re

import pytest

from subtitles import _collect_word_blocks, generate_ass, generate_srt
from test_ai_trim import trim_endpoint


def transcript():
    return {'segments': [{'start': 9, 'end': 14, 'words': [
        {'word': ' beginning', 'start': 9.8, 'end': 10.5},
        {'word': ' ending', 'start': 11.5, 'end': 12.3},
        {'word': ' outside', 'start': 12.5, 'end': 13},
    ]}]}


@pytest.mark.parametrize('iteration', range(10))
def test_repeated_trim_clips_words_to_both_boundaries(iteration):
    start, end = 10 + iteration * .02, 12 - iteration * .02
    words = [word for block in _collect_word_blocks(transcript(), start, end)
             for word in block]
    assert [word['word'] for word in words] == ['beginning', 'ending']
    assert words[0]['start'] == 0
    assert words[-1]['end'] == pytest.approx(end - start)
    assert all(0 <= word['start'] < word['end'] <= end - start for word in words)


@pytest.mark.parametrize('renderer,extension,end_stamp', [
    (generate_srt, 'srt', '00:00:02,000'),
    (generate_ass, 'ass', '0:00:02.00'),
])
def test_caption_files_stop_at_trim_end(tmp_path, renderer, extension, end_stamp):
    output = tmp_path / f'captions.{extension}'
    assert renderer(transcript(), 10, 12, str(output))
    content = output.read_text(encoding='utf-8-sig')
    assert end_stamp in content
    assert 'outside' not in content
    assert not re.search(r'0:00:02[.,]30', content)


def test_preview_caption_words_stop_at_actual_clip_end(trim_endpoint):
    endpoint = trim_endpoint
    endpoint.clip.update(start=10, end=12, duration_seconds=2)
    endpoint.meta.write_text(json.dumps({'shorts': [endpoint.clip], 'transcript': transcript()}))
    result = asyncio.run(endpoint.app.get_clip_transcript('trim-job', 0, None))
    assert result['durationSec'] == 2
    assert result['captions'] == [
        {'text': 'beginning', 'startMs': 0, 'endMs': 500, 'utteranceId': 0},
        {'text': 'ending', 'startMs': 1500, 'endMs': 2000, 'utteranceId': 0},
    ]


@pytest.mark.parametrize('use_recipe', [False, True])
def test_preview_duration_keeps_encoded_tail_without_selecting_more_speech(trim_endpoint, use_recipe):
    endpoint = trim_endpoint
    endpoint.clip.update(start=10, end=12, duration_seconds=2.125)
    if use_recipe:
        endpoint.clip['recipe'] = {'segments': [{'start': 10, 'end': 12}]}
    endpoint.meta.write_text(json.dumps({'shorts': [endpoint.clip], 'transcript': transcript()}))
    result = asyncio.run(endpoint.app.get_clip_transcript('trim-job', 0, None))
    assert result['durationSec'] == 2.125
    assert [word['text'] for word in result['captions']] == ['beginning', 'ending']
    assert result['captions'][-1]['endMs'] == 2000


def test_preview_words_clamp_to_a_shorter_encoded_file(trim_endpoint):
    endpoint = trim_endpoint
    endpoint.clip.update(start=10, end=12, duration_seconds=1.75)
    endpoint.meta.write_text(json.dumps({'shorts': [endpoint.clip], 'transcript': transcript()}))
    result = asyncio.run(endpoint.app.get_clip_transcript('trim-job', 0, None))
    assert result['durationSec'] == 1.75
    assert result['captions'][-1]['endMs'] == 1750
