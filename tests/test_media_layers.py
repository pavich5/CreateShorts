import asyncio
import subprocess

import httpx
import broll_edit
import manual_broll
from test_manual_broll_api import project, make_image


def test_multiple_images_overlap_reorder_and_reopen(project):
    app, root, source, metadata, runtime = project

    def pixel(path, x):
        return tuple(subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '1', '-i', str(path),
            '-frames:v', '1', '-vf', f'crop=2:2:{x}:90,scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])[:3])

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            endpoint = '/api/clip/job/0/broll'
            assets = []
            for color in ('red', 'blue', 'yellow'):
                upload = await client.post(endpoint+'/assets', files={'file': ('image.png', make_image(color), 'image/png')})
                assert upload.status_code == 200
                assets.append(upload.json()['asset'])
            placements = [dict(id=str(index), asset=asset, layer=index, start=0, end=3,
                x=x, y=0, width=width, height=100, fit='stretch', background='none')
                for index, (asset, x, width) in enumerate(zip(assets, (0, 40, 45), (60, 60, 10)))]
            saved = await client.put(endpoint, json={'placements': placements})
            assert saved.status_code == 200, saved.text
            path = root/saved.json()['video_url'].split('/')[-1]
            assert pixel(path, 10)[0] > 180
            assert pixel(path, 110)[2] > 180
            middle = pixel(path, 60)
            assert middle[0] > 180 and middle[1] > 180 and middle[2] < 80
            reopened = (await client.get(endpoint)).json()
            assert len(reopened['placements']) == 3
            assert [item['layer'] for item in reopened['placements']] == [0, 1, 2]
            placements[0]['layer'] = 3
            reordered = await client.put(endpoint, json={'revision': reopened['revision'], 'placements': placements})
            assert reordered.status_code == 200, reordered.text
            middle = pixel(root/reordered.json()['video_url'].split('/')[-1], 60)
            assert middle[0] > 180 and middle[1] < 80
            state = (await client.get(endpoint)).json()
            assert next(item['layer'] for item in state['placements'] if item['id'] == '0') == 3

    asyncio.run(run())


def test_overlapping_manual_layers_survive_source_mapping(tmp_path):
    asset = manual_broll.save_image(tmp_path, 0, make_image('red'))
    placements = manual_broll.validate_placements([
        dict(id='back', asset=asset, layer=0, start=0, end=3),
        dict(id='front', asset=asset, layer=1, start=1, end=4)], 4, tmp_path)
    segments = [{'start': 100, 'end': 102}, {'start': 110, 'end': 112}]
    cues = manual_broll.source_cues(placements, segments)
    mapped = broll_edit.remap_cues(cues, segments)
    reopened = manual_broll.displayed_placements({'mode': 'manual', 'cues': mapped})
    assert {(item['id'], item['layer'], item['start'], item['end']) for item in reopened} == {
        ('back', 0, 0, 3), ('front', 1, 1, 4)}
