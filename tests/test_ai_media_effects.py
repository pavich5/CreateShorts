"""Saved AI visual/audio edits survive media changes and trimming."""
import asyncio
from array import array
import json
from pathlib import Path
import subprocess

import ai_clip_edit as ai
import httpx
from test_ai_trim import trim_endpoint
from test_manual_broll_api import make_image, project


def test_saved_zoom_strength_matches_the_actual_rendering_cap():
    plan = ai.validate_plan({'operations': [{'type': 'punch_in', 'start': 1, 'end': 3, 'strength': 1}]}, 4, False)
    assert plan.operations[0].strength == .15



def test_prompted_color_effects_also_keep_manual_media_editable(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    endpoint.clip['broll'] = {'mode': 'manual', 'base_file': 'clip.mp4', 'file': 'clip.mp4',
                              'cues': [{'asset': 'picture.png', 'start': 1, 'end': 4}]}
    monkeypatch.setattr(ai, 'plan_edit', lambda *args, **kwargs: ai.validate_plan({'operations': [
        {'type': 'color_pop', 'start': 1, 'end': 3}]}, 6, False))
    result = asyncio.run(endpoint.app.edit_clip_with_ai(endpoint.app.AIEditRequest(
        job_id='trim-job', instructions='More vivid color from 1 to 3 seconds'), None))
    assert result['success']
    assert endpoint.clip['broll']['post_effects'][0][0]['type'] == 'color_pop'



def samples(path, at):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path),
        '-t', '0.2', '-vn', '-ac', '1', '-f', 'f32le', '-'])
    values = array('f')
    values.frombytes(raw)
    return values



def pixel(path, at):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path),
        '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])



def test_media_resave_and_removal_keep_color_and_mute_effects(project):
    app, root, _, metadata, runtime = project
    batch = [{'type': 'bw_moment', 'start': 1, 'end': 3, 'strength': 1},
             {'type': 'mute', 'start': 1, 'end': 2, 'strength': 1}]

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            image = (await client.post('/api/clip/job/0/broll/assets', files={
                'file': ('picture.png', make_image('red'), 'image/png')})).json()
            response = await client.put('/api/clip/job/0/broll', json={'placements': [
                {'id': 'photo', 'asset': image['asset'], 'start': .5, 'end': 3.5}]})
            assert response.status_code == 200, response.text
            saved = json.loads(metadata.read_text())
            clip = saved['shorts'][0]
            current = root / Path(clip['video_url']).name
            output = root / 'ai_effects.mp4'
            info = ai.probe(str(current))
            ai.render_edit(str(current), str(output), ai.validate_plan({'operations': batch}, 4, False), info)
            clip['broll'].update(file=output.name, post_effects=[batch])
            clip['broll'].pop('overlay_fingerprint', None)
            clip['video_url'] = '/videos/job/ai_effects.mp4'
            metadata.write_text(json.dumps(saved))
            runtime['jobs']['job']['result']['clips'][0].update(clip)
            reopened = (await client.get('/api/clip/job/0/broll')).json()
            assert reopened['post_effects'] == [batch]
            response = await client.put('/api/clip/job/0/broll', json={'placements': reopened['placements']})
            assert response.status_code == 200, response.text
            rendered = root / Path(response.json()['video_url']).name
            inside, outside = pixel(rendered, 1.5), pixel(rendered, .75)
            assert max(inside) - min(inside) < 5
            assert outside[0] > outside[1] + 100
            assert max(abs(value) for value in samples(rendered, 1.3)) < .002
            assert max(abs(value) for value in samples(rendered, 2.5)) > .02
            response = await client.put('/api/clip/job/0/broll', json={'placements': []})
            assert response.status_code == 200, response.text
            cleared = root / Path(response.json()['video_url']).name
            assert max(pixel(cleared, 1.5)) - min(pixel(cleared, 1.5)) < 5
            assert max(abs(value) for value in samples(cleared, 1.3)) < .002
    asyncio.run(run())



def test_trimmed_zoom_replay_keeps_the_original_zoom_phase(project):
    _, root, _, _, _ = project
    source = root / 'pattern.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=120x180:r=10:d=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    batch = [{'type': 'zoom_in', 'start': 0, 'end': 4, 'strength': .15}]
    info = ai.probe(source)
    effected = root / 'ai_zoom.mp4'
    ai.render_edit(source, effected, ai.validate_plan({'operations': batch}, 4, False), info)
    clip = {'broll': {'mode': 'manual', 'base_file': source.name, 'file': effected.name,
                      'cues': [], 'post_effects': [batch]}}
    trim = ai.validate_plan({'operations': [{'type': 'trim', 'start': 1, 'end': 3}]}, 4, False)
    output = root / 'ai_zoom_trimmed.mp4'
    ai.render_edit(effected, output, trim, info)
    record = ai.prepare_manual_media_trim(clip, output, trim, info)
    assert record['post_effects'][0][0]['timeline_offset'] == 1
    rebuilt = root / 'rebuilt.mp4'
    rebuilt.write_bytes((root / record['base_file']).read_bytes())
    ai.replay_post_effects(rebuilt, record['post_effects'], ai.probe(rebuilt))
    def frame(path, at):
        return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path),
            '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
    for at in (.5, 1.5):
        actual, expected = frame(rebuilt, at), frame(output, at)
        assert sum(abs(a - b) for a, b in zip(actual, expected)) / len(actual) < 5

