"""Rendered result filtering must never change a clip's source metadata slot."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from test_ai_trim import trim_endpoint
from clip_identity import find_clip_by_index


def test_explicit_clip_indices_survive_filtering_reordering_and_tombstones():
    deleted = {'index': 0, 'deleted': True, 'video_url': None}
    second, fourth = {'index': 1}, {'index': 3}
    clips = [fourth, deleted, second]
    assert find_clip_by_index(clips, 1) is second
    assert find_clip_by_index(clips, 3) is fourth
    assert find_clip_by_index(clips, 0) is deleted
    assert find_clip_by_index(clips, 2) is None
    assert find_clip_by_index(clips, -1) is None
    assert find_clip_by_index(clips, True) is None


def test_legacy_unfiltered_result_lists_remain_compatible():
    clips = [{}, {'index': None}]
    assert find_clip_by_index(clips, 0) is clips[0]
    assert find_clip_by_index(clips, 1) is clips[1]


def test_trim_of_only_ready_second_clip_updates_only_its_original_slot(trim_endpoint):
    endpoint = trim_endpoint
    endpoint.clip['index'] = 1
    unavailable = {'start': 50, 'end': 56, 'title': 'Not rendered yet'}
    data = json.loads(endpoint.meta.read_text())
    data['shorts'] = [unavailable, deepcopy(endpoint.clip)]
    endpoint.meta.write_text(json.dumps(data))
    result = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', clip_index=1, start=1, end=5), None))
    assert result['success']
    saved = json.loads(endpoint.meta.read_text())['shorts']
    assert saved[0] == unavailable
    assert saved[1]['duration_seconds'] == 4
    assert endpoint.clip['video_url'] == saved[1]['video_url']
    assert endpoint.job['ready_files'][1] == result['new_video_url'].split('/')[-1]


def test_missing_original_slot_does_not_edit_a_different_ready_clip(trim_endpoint):
    endpoint = trim_endpoint
    endpoint.clip['index'] = 1
    before = endpoint.meta.read_bytes(), deepcopy(endpoint.clip)
    with pytest.raises(endpoint.app.HTTPException) as caught:
        asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
            job_id='trim-job', clip_index=0, start=1, end=5), None))
    assert caught.value.status_code == 404
    assert (endpoint.meta.read_bytes(), endpoint.clip) == before


def test_unmark_updates_original_slot_in_a_compacted_runtime(trim_endpoint):
    endpoint = trim_endpoint
    endpoint.clip['index'] = 1
    endpoint.app._unmark_local_job('trim-job', 1, 'clean.mp4')
    assert endpoint.clip['video_url'] == '/videos/trim-job/clean.mp4'


def test_hook_updates_second_clip_and_keeps_deleted_first_slot(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    endpoint.clip['index'] = 1
    deleted = {'deleted': True, 'index': 0, 'video_url': None}
    data = json.loads(endpoint.meta.read_text())
    data['shorts'] = [deleted, deepcopy(endpoint.clip)]
    endpoint.meta.write_text(json.dumps(data))
    monkeypatch.setattr(endpoint.app, 'add_hook_to_video',
                        lambda source, text, output, **kwargs: Path(output).write_bytes(text.encode()))
    result = asyncio.run(endpoint.app.add_hook(endpoint.app.HookRequest(
        job_id='trim-job', clip_index=1, text='Second clip hook'), None))
    saved = json.loads(endpoint.meta.read_text())['shorts']
    assert saved[0] == deleted
    assert endpoint.clip['auto_hook']['text'] == saved[1]['auto_hook']['text'] == 'Second clip hook'
    assert endpoint.clip['video_url'] == result['new_video_url']


def test_caption_removal_updates_second_runtime_clip(trim_endpoint):
    endpoint = trim_endpoint
    endpoint.clip['index'] = 1
    data = json.loads(endpoint.meta.read_text())
    data['shorts'] = [{'deleted': True, 'index': 0}, deepcopy(endpoint.clip)]
    endpoint.meta.write_text(json.dumps(data))
    result = asyncio.run(endpoint.app.remove_subtitles(endpoint.app.RemoveSubtitlesRequest(
        job_id='trim-job', clip_index=1), None))
    assert result['success']
    assert endpoint.clip['caption_preset'] == 'cc_none'


def test_webhook_keeps_original_indices_after_render_failures(trim_endpoint):
    endpoint = trim_endpoint
    endpoint.clip['index'] = 3
    entries = asyncio.run(endpoint.app._webhook_clip_entries('trim-job', endpoint.job))
    assert [entry['index'] for entry in entries] == [3]


def test_download_bundle_matches_current_files_to_original_slots(trim_endpoint, monkeypatch):
    import zipfile

    endpoint = trim_endpoint
    endpoint.clip.update(index=1, video_url='/videos/trim-job/edited_second.mp4')
    first = {'video_url': '/videos/trim-job/first.mp4'}
    second = {'video_url': '/videos/trim-job/second.mp4'}
    endpoint.meta.write_text(json.dumps({'shorts': [first, second]}))
    for name in ('first.mp4', 'second.mp4', 'edited_second.mp4'):
        (endpoint.directory / name).write_bytes(name.encode())
    monkeypatch.setattr(endpoint.app, '_assert_disk_job_owner', AsyncMock())
    result = asyncio.run(endpoint.app.download_all_clips('trim-job', None))
    with zipfile.ZipFile(result.path) as archive:
        assert sorted(archive.namelist()) == ['clip_01_first.mp4', 'clip_02_edited_second.mp4']
    asyncio.run(result.background())
