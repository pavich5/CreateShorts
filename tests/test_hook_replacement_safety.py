"""Hook replacement cannot stack text or overwrite an earlier served version.

These are endpoint state regressions with the encoder isolated; real rendering
is exercised separately by the application E2E workflow.
"""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from test_ai_trim import trim_endpoint


@pytest.mark.parametrize('remove', [False, True])
@pytest.mark.parametrize('captioned', [False, True])
def test_hook_replacement_rejects_missing_clean_picture(trim_endpoint, monkeypatch, remove, captioned):
    endpoint = trim_endpoint
    hooked = endpoint.directory / 'hooked_1_clip.mp4'
    hooked.write_bytes(b'picture with old hook')
    current = hooked
    if captioned:
        current = endpoint.directory / ('subtitled_2_' + hooked.name)
        current.write_bytes(b'picture with captions and old hook')
    (endpoint.directory / 'clip.mp4').unlink()
    endpoint.clip.update(video_url='/videos/trim-job/' + current.name,
                         auto_hook={'text': 'Original hook'})
    metadata = json.loads(endpoint.meta.read_text())
    metadata['shorts'][0] = endpoint.clip.copy()
    endpoint.meta.write_text(json.dumps(metadata))
    encoded = []
    monkeypatch.setattr(endpoint.app, 'add_hook_to_video',
                        lambda source, text, output, **kwargs: encoded.append(source)
                        or Path(output).write_bytes(b'overlaid'))
    with pytest.raises(HTTPException) as error:
        asyncio.run(endpoint.app.add_hook(endpoint.app.HookRequest(
            job_id='trim-job', clip_index=0, text='Replacement hook', remove=remove), None))
    assert error.value.status_code == 409
    assert not encoded
    assert endpoint.clip['auto_hook']['text'] == 'Original hook'
    assert endpoint.clip['video_url'].endswith(current.name)


def test_ten_rapid_hook_updates_keep_unique_urls_and_one_hook(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    sources, urls = [], []
    monkeypatch.setattr(endpoint.app.time, 'time', lambda: 1234567890)

    def encode(source, text, output, **kwargs):
        sources.append(Path(source).name)
        Path(output).write_bytes(text.encode())

    monkeypatch.setattr(endpoint.app, 'add_hook_to_video', encode)
    for iteration in range(10):
        result = asyncio.run(endpoint.app.add_hook(endpoint.app.HookRequest(
            job_id='trim-job', clip_index=0, text=f'Hook {iteration}', position='top'), None))
        urls.append(result['new_video_url'])
        assert result['burned_hook']['text'] == f'Hook {iteration}'
        assert json.loads(endpoint.meta.read_text())['shorts'][0]['auto_hook']['text'] == f'Hook {iteration}'
    assert sources == ['clip.mp4'] * 10
    assert len(set(urls)) == 10
    for iteration, url in enumerate(urls):
        assert (endpoint.directory / url.rsplit('/', 1)[-1]).read_text() == f'Hook {iteration}'


def test_clean_picture_with_stale_hook_metadata_can_still_be_updated(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    endpoint.clip['auto_hook'] = {'text': 'Already removed'}
    metadata = json.loads(endpoint.meta.read_text())
    metadata['shorts'][0] = endpoint.clip.copy()
    endpoint.meta.write_text(json.dumps(metadata))
    monkeypatch.setattr(endpoint.app, 'add_hook_to_video',
                        lambda source, text, output, **kwargs: Path(output).write_bytes(text.encode()))
    result = asyncio.run(endpoint.app.add_hook(endpoint.app.HookRequest(
        job_id='trim-job', clip_index=0, text='New hook'), None))
    assert result['burned_hook']['text'] == 'New hook'


def test_hook_response_and_saved_state_retain_complete_appearance(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    monkeypatch.setattr(endpoint.app, 'add_hook_to_video',
                        lambda source, text, output, **kwargs: Path(output).write_bytes(text.encode()))
    result = asyncio.run(endpoint.app.add_hook(endpoint.app.HookRequest(
        job_id='trim-job', clip_index=0, text='Remember these settings', position='bottom',
        size='L', style='red', font='serif', duration_seconds=3.5), None))
    expected = {'text': 'Remember these settings', 'position': 'bottom', 'size': 'L',
                'style': 'red', 'font': 'serif', 'duration_seconds': 3.5}
    assert result['burned_hook'] == expected
    assert json.loads(endpoint.meta.read_text())['shorts'][0]['auto_hook'] == expected
    assert endpoint.clip['auto_hook'] == expected


@pytest.mark.parametrize('position', ['auto', 'center'])
def test_saved_hook_resolved_geometry_matches_its_render_recipe(trim_endpoint, monkeypatch, position):
    import hooks
    from PIL import Image
    endpoint = trim_endpoint

    def encode(source, text, output, **kwargs):
        Path(output).write_bytes(b'new hook')
        recipe, picture = hooks.recipe_paths(str(endpoint.directory), Path(output).name)
        Path(recipe).write_text(json.dumps({'source': Path(source).name, 'x': 10, 'y': 36, 'duration': 3}))
        Image.new('RGBA', (100, 24), 'white').save(picture)
        return {'y': .375, 'scale': .8, 'height': .25} if position == 'auto' else True

    monkeypatch.setattr(endpoint.app, 'add_hook_to_video', encode)
    result = asyncio.run(endpoint.app.add_hook(endpoint.app.HookRequest(
        job_id='trim-job', clip_index=0, text='Resolved hook', position=position), None))
    assert result['burned_hook']['y'] == .375  # fixture source height: 96px
    assert result['burned_hook']['height'] == .25
    if position == 'auto':
        assert result['burned_hook']['scale'] == .8
    saved = json.loads(endpoint.meta.read_text())['shorts'][0]['auto_hook']
    assert saved['y'] == .375 and saved['height'] == .25


@pytest.mark.parametrize('request_name', ['HookRequest', 'SubtitleRequest'])
@pytest.mark.parametrize('clip_index', [-1, True, .5, '0'])
def test_caption_and_hook_requests_reject_invalid_clip_indexes(trim_endpoint, request_name, clip_index):
    request_type = getattr(trim_endpoint.app, request_name)
    with pytest.raises(ValidationError):
        request_type(job_id='trim-job', clip_index=clip_index)


def test_transcript_rejects_negative_clip_index(trim_endpoint):
    with pytest.raises(HTTPException) as error:
        asyncio.run(trim_endpoint.app.get_clip_transcript('trim-job', -1, None))
    assert error.value.status_code == 404
