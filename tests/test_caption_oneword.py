"""One-word captions survive real ASS generation, saved metadata, and restyling."""
import asyncio
import json
from pathlib import Path
import re
import shutil

import pytest

from test_ai_trim import trim_endpoint


WORDS = [
    {"text": "Create", "startMs": 0, "endMs": 400},
    {"text": "better", "startMs": 500, "endMs": 900},
    {"text": "reels", "startMs": 1000, "endMs": 1400},
]


def dialogue_text(path):
    return [re.sub(r"\{[^}]*\}", "", line.split(",", 9)[9]).strip()
            for line in Path(path).read_text().splitlines() if line.startswith("Dialogue:")]


@pytest.mark.parametrize('effect', ['clipcutter:mozi', 'clipcutter:marker_sweep', 'clipcutter:negative_keywords', 'pop'])
def test_one_word_saved_captions_remain_single_words_when_reopened(trim_endpoint, monkeypatch, effect):
    endpoint = trim_endpoint
    generated = []

    def burn_without_encoding(source, subtitles, output, **kwargs):
        generated.append(dialogue_text(subtitles))
        shutil.copyfile(source, output)

    monkeypatch.setattr(endpoint.app, 'burn_subtitles', burn_without_encoding)
    monkeypatch.setattr(endpoint.app, '_strip_burned_captions', lambda root, filename: 'clip.mp4')
    request = endpoint.app.SubtitleRequest(
        job_id='trim-job', clip_index=0, preset='cc_mozi', style='karaoke', effect=effect,
        font_name='Arial', font_size=72, max_chars=1, max_duration=1.4, words=WORDS)
    result = asyncio.run(endpoint.app.add_subtitles(request, None))
    assert result['success']
    assert result['caption_style']['max_chars'] == 1
    assert generated[0] == ['CREATE', 'BETTER', 'REELS']

    saved = json.loads(endpoint.meta.read_text())['shorts'][0]
    assert saved['caption_style']['max_chars'] == 1
    assert saved['caption_preset'] == 'cc_mozi'
    reopened = endpoint.app.SubtitleRequest(
        job_id='trim-job', clip_index=0, preset=saved['caption_preset'], **saved['caption_style'])
    reapplied = asyncio.run(endpoint.app.add_subtitles(reopened, None))
    assert reapplied['caption_style']['max_chars'] == 1
    assert generated[-1] == generated[0]
    assert (endpoint.directory / 'clip.mp4').read_bytes() == b'original'


def test_clipcutter_grouped_captions_keep_their_default(tmp_path):
    from subtitles import generate_ass
    transcript = {'segments': [{'start': 0, 'end': 2, 'words': [
        {'word': ' Create', 'start': 0, 'end': .4},
        {'word': ' better', 'start': .5, 'end': .9},
        {'word': ' reels', 'start': 1, 'end': 1.4},
    ]}]}
    path = tmp_path / 'grouped.ass'
    assert generate_ass(transcript, 0, 2, str(path), effect='clipcutter:mozi', font_name='Arial', uppercase=True, max_chars=16)
    assert 'CREATE BETTER REELS' in dialogue_text(path)
