"""Uploaded-video in/out points remain independent of reel placement timing."""
import asyncio
import shutil
import subprocess

import httpx
import pytest

import broll
from test_manual_broll_api import project

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg unavailable')


def pixel(path, at):
    return subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path),
        '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
        capture_output=True, check=True).stdout[:3]


def test_only_source_ten_to_thirteen_is_rendered_saved_and_reopened(project):
    app, root, _, _, _ = project
    upload_path = root / 'forty_seconds.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=red:s=120x180:r=10:d=40', '-vf',
        "drawbox=color=blue:t=fill:enable='gte(t,10)*lt(t,13)'", '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(upload_path)], capture_output=True, check=True)

    async def run():
        endpoint = '/api/clip/job/0/broll'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            original = (await client.get(endpoint)).json()
            upload = await client.post(endpoint + '/assets', files={
                'file': ('forty_seconds.mp4', upload_path.read_bytes(), 'video/mp4')})
            assert upload.status_code == 200, upload.text
            asset = upload.json()
            assert asset['media_duration'] == pytest.approx(40, abs=.1)
            placement = {'id': 'selected_three_seconds', 'kind': 'video', 'asset': asset['asset'],
                'source_start': 10, 'start': .5, 'end': 3.5, 'volume': 0,
                'fit': 'stretch', 'background': 'none'}
            response = await client.put(endpoint, json={
                'revision': original['revision'], 'placements': [placement]})
            assert response.status_code == 200, response.text
            output = root / response.json()['new_video_url'].rsplit('/', 1)[1]
            assert broll._probe_video(str(output))['duration'] == pytest.approx(4, abs=.1)
            for time in (.8, 1.8, 3.2):
                red, green, blue = pixel(output, time)
                assert blue > 180 and blue > red + 100 and blue > green + 100
            for time in (.2, 3.8):
                red, green, blue = pixel(output, time)
                assert green > 90 and green > red + 50 and green > blue + 50

            reopened = (await client.get(endpoint)).json()
            selected = reopened['placements'][0]
            assert selected['source_start'] == 10
            assert selected['end'] - selected['start'] == 3
            assert selected['source_start'] + selected['end'] - selected['start'] == 13
            assert selected['media_duration'] == pytest.approx(40, abs=.1)
            selected.update(start=.75, end=3.75)
            moved = await client.put(endpoint, json={
                'revision': reopened['revision'], 'placements': [selected]})
            assert moved.status_code == 200, moved.text
            again = (await client.get(endpoint)).json()['placements'][0]
            assert (again['start'], again['end'], again['source_start']) == (.75, 3.75, 10)

    asyncio.run(run())
