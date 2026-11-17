"""Caption layering changes actual pixels and stays editable across saves."""
import asyncio
import json
from pathlib import Path
import re
import subprocess

import httpx
import pytest
from test_manual_broll_api import project, make_image


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_caption_layer_order_and_reopen(project, kind):
    app, root, source, metadata, runtime = project
    counter = iter(range(100, 200))

    def caption_picture(path, *args):
        output = Path(path).with_name(f'subtitled_{next(counter)}_' + Path(path).name)
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(path), '-vf',
                        'drawbox=x=0:y=140:w=120:h=40:color=white:t=fill',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'copy', str(output)], check=True)
        return str(output)

    initial = Path(caption_picture(source))
    data = json.loads(metadata.read_text())
    data['shorts'][0].update(video_url=f'/videos/job/{initial.name}', caption_style={'font_name': 'Arial'})
    data['transcript'] = {'segments': [{'words': [{'word': 'caption', 'start': 100, 'end': 104}]}]}
    metadata.write_text(json.dumps(data))
    runtime['jobs']['job']['result']['clips'][0].update(data['shorts'][0])
    runtime['_strip_burned_captions'] = lambda root, name, materialize=False: re.sub(r'^subtitled_\d+_', '', name)
    runtime['_restyle_captions'] = caption_picture
    payload, filename, mime = make_image('blue'), 'blue.png', 'image/png'
    if kind == 'video':
        video = root / 'uploaded_blue.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
            'color=c=blue:s=120x180:r=10:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
        payload, filename, mime = video.read_bytes(), video.name, 'video/mp4'

    def bottom_pixel(name):
        return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '0.5', '-i', str(root / name),
            '-frames:v', '1', '-vf', 'crop=2:2:60:150,scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])[:3]

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            endpoint = '/api/clip/job/0/broll'
            upload = await client.post(endpoint+'/assets', files={'file': (filename, payload, mime)})
            assert upload.status_code == 200
            placement = {'asset': upload.json()['asset'], 'start': 0, 'end': 2, 'fit': 'stretch', 'background': 'none'}
            for above in (True, False, True):
                state = (await client.get(endpoint)).json()
                assert state['captions'][0] == {'text': 'caption', 'startMs': 0, 'endMs': 4000, 'utteranceId': 0}
                assert bottom_pixel(state['caption_free_video_url'].split('/')[-1])[1] > 80
                saved = await client.put(endpoint, json={'revision': state['revision'], 'captions_above_media': above, 'placements': [placement]})
                assert saved.status_code == 200, saved.text
                pixel = bottom_pixel(saved.json()['new_video_url'].split('/')[-1])
                assert min(pixel) > 200 if above else pixel[2] > 180 and pixel[0] < 40
                assert (await client.get(endpoint)).json()['captions_above_media'] is above
            invalid = await client.put(endpoint, json={'captions_above_media': 'yes', 'placements': [placement]})
            assert invalid.status_code == 400

    asyncio.run(run())
