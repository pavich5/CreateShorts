"""A successful video result must disclose an actual automatic-caption failure.

These status regressions isolate encoding. Real current-module caption output
is verified separately in the mandatory-source fresh-process artifact.
"""
import asyncio
import json
from pathlib import Path

import pytest

from test_ai_trim import trim_endpoint


def test_failed_automatic_captions_record_failure_on_the_delivered_clip(tmp_path, monkeypatch):
    import main
    import subtitles
    monkeypatch.setenv('AUTO_CAPTIONS', '1')
    monkeypatch.setenv('AUTO_CAPTION_PRESET', 'cc_mozi')
    def fail(*args, **kwargs):
        raise ValueError('Unknown editing setting: example')
    monkeypatch.setattr(subtitles, 'generate_ass', fail)
    status = {}
    result = main.auto_caption_clip(str(tmp_path / 'clip.mp4'), {'segments': [{}]}, 0, 1,
                                    split_ranges=[], result_status=status)
    clip = {'video_url': '/videos/job/hooked_1_clip.mp4', 'caption_preset': 'cc_mozi'}
    main.record_caption_generation(clip, result, status)
    assert result is None
    assert clip['caption_generation'] == {'status': 'failed', 'error': 'ValueError: Unknown editing setting: example'}
    assert clip['video_url'] == '/videos/job/hooked_1_clip.mp4'


def test_successful_caption_retry_clears_previous_failure(tmp_path, monkeypatch):
    import main
    import subtitles
    monkeypatch.setenv('AUTO_CAPTIONS', '1')
    monkeypatch.setenv('AUTO_CAPTION_PRESET', 'cc_mozi')
    monkeypatch.setattr(subtitles, 'generate_ass', lambda *args, **kwargs: True)
    monkeypatch.setattr(subtitles, 'burn_subtitles', lambda source, ass, output, **kwargs: Path(output).write_bytes(b'captioned'))
    status = {'status': 'failed', 'error': 'old failure'}
    result = main.auto_caption_clip(str(tmp_path / 'clip.mp4'), {'segments': [{}]}, 0, 1,
                                    split_ranges=[], result_status=status)
    clip = {'caption_generation': {'status': 'failed', 'error': 'old failure'}}
    main.record_caption_generation(clip, result, status)
    assert Path(result).is_file() and status == {'status': 'completed'}
    assert 'caption_generation' not in clip


@pytest.mark.parametrize('remove', [False, True])
def test_successful_caption_save_or_removal_clears_persistent_warning(trim_endpoint, monkeypatch, remove):
    endpoint = trim_endpoint
    endpoint.clip['caption_generation'] = {'status': 'failed', 'error': 'old failure'}
    data = json.loads(endpoint.meta.read_text())
    data['shorts'][0] = endpoint.clip.copy()
    endpoint.meta.write_text(json.dumps(data))
    monkeypatch.setattr(endpoint.app, '_burn_caption_style',
                        lambda source, output, *args, **kwargs: Path(output).write_bytes(b'captioned') or True)
    monkeypatch.setattr(endpoint.app, 'burn_subtitles',
                        lambda source, ass, output, **kwargs: Path(output).write_bytes(b'captioned'))
    if remove:
        result = asyncio.run(endpoint.app.remove_subtitles(endpoint.app.RemoveSubtitlesRequest(
            job_id='trim-job', clip_index=0), None))
    else:
        result = asyncio.run(endpoint.app.add_subtitles(endpoint.app.SubtitleRequest(
            job_id='trim-job', clip_index=0, preset='cc_mozi'), None))
    assert result['success'] and result['caption_generation'] is None
    assert 'caption_generation' not in endpoint.clip
    assert 'caption_generation' not in json.loads(endpoint.meta.read_text())['shorts'][0]
