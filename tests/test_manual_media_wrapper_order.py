"""Saved hook/caption layer order must not make editable media look stale.

Real UI reproduction: below-media captions save as hooked_subtitled_broll_base;
the old resolver only handled subtitled_hooked_broll_base and immediately 409ed.
These endpoint-state regressions isolate overlay encoding with file copies.
"""
import asyncio
import json
from pathlib import Path
import shutil

import httpx
import pytest

from test_manual_broll_api import project, make_image


def wrapped_project(project, order):
    app, root, source, metadata, runtime = project
    import app as real_app
    runtime['_strip_burned_captions'] = real_app._strip_burned_captions
    runtime['_strip_burned_hook'] = real_app._strip_burned_hook
    media_name = 'broll_101_' + source.name
    shutil.copyfile(source, root / media_name)
    if order == 'below':
        inner = 'subtitled_202_' + media_name
        served = 'hooked_303_' + inner
    else:
        inner = 'hooked_303_' + media_name
        served = 'subtitled_202_' + inner
    shutil.copyfile(source, root / inner)
    shutil.copyfile(source, root / served)
    asset = root / 'broll_assets_test' / 'image.png'
    asset.parent.mkdir()
    asset.write_bytes(make_image('blue'))
    data = json.loads(metadata.read_text())
    clip = data['shorts'][0]
    clip.update(video_url='/videos/job/' + served, duration_seconds=4,
                auto_hook={'text': 'One hook', 'position': 'top', 'style': 'pill', 'duration_seconds': 2},
                caption_style={'style': 'karaoke', 'font_name': 'Arial'},
                broll={'mode': 'manual', 'file': media_name, 'base_file': source.name,
                       'captions_above_media': order == 'above', 'cues': [
                           {'id': 'image-1', 'asset': 'broll_assets_test/image.png', 'kind': 'image',
                            'media_type': 'image', 'start': .5, 'end': 2, 'duration': 1.5,
                            'x': 0, 'y': 0, 'width': 100, 'height': 100, 'opacity': 1,
                            'fit': 'stretch', 'background': 'none', 'layer': 0}]})
    data['transcript'] = {'segments': [{'start': 100, 'end': 104,
                                      'words': [{'word': ' recognized', 'start': 100, 'end': 104}]}]}
    metadata.write_text(json.dumps(data))
    runtime['jobs']['job']['result']['clips'][0].update(clip)

    def add_hook(path, text, output, **kwargs):
        shutil.copyfile(path, output)
        return True

    def restyle(path, *args):
        output = Path(path).with_name('subtitled_404_' + Path(path).name)
        shutil.copyfile(path, output)
        return str(output)

    runtime['add_hook_to_video'] = add_hook
    runtime['_restyle_captions'] = restyle
    return app, root, source, metadata, runtime, served


@pytest.mark.parametrize('order', ['above', 'below'])
@pytest.mark.parametrize('live', [False, True])
def test_media_reopens_with_caption_and_hook_wrappers_in_either_order(project, order, live):
    app, root, source, metadata, _, served = wrapped_project(project, order)
    original = metadata.read_bytes(), source.read_bytes(), (root / served).read_bytes()

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/clip/job/0/broll' + ('?live_preview=true' if live else ''))
            assert response.status_code == 200, response.text
            state = response.json()
            assert state['captions_above_media'] is (order == 'above')
            assert len(state['placements']) == 1
            assert state['placements'][0]['id'] == 'image-1'
            assert state['captions'][0]['text'] == 'recognized'
            assert state['duration'] == pytest.approx(4, abs=.05)
            if live:
                assert state['base_video_url'] == '/videos/job/' + source.name
                assert state['preview_hook']['text'] == 'One hook'
            else:
                assert (root / state['base_video_url'].rsplit('/', 1)[-1]).is_file()

    asyncio.run(run())
    assert (metadata.read_bytes(), source.read_bytes(), (root / served).read_bytes()) == original


def test_unrecognized_later_visual_edit_still_requires_explicit_rebase(project):
    app, root, source, metadata, _, _ = wrapped_project(project, 'below')
    data = json.loads(metadata.read_text())
    later = 'edited_999_' + data['shorts'][0]['broll']['file']
    shutil.copyfile(source, root / later)
    data['shorts'][0]['video_url'] = '/videos/job/' + later
    metadata.write_text(json.dumps(data))
    before = metadata.read_bytes()

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/clip/job/0/broll?live_preview=true')
            assert response.status_code == 409
            assert 'later visual edit' in response.json()['detail']

    asyncio.run(run())
    assert metadata.read_bytes() == before
