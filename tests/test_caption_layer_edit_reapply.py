"""Changing a hook/effect must retain captions below the user's media layer."""
import asyncio
import json
from pathlib import Path
import subprocess

import numpy as np

import ai_clip_edit as ai
import broll
import manual_broll
from test_ai_trim import trim_endpoint
from test_manual_broll_api import make_image


def _frame(path, time=.5):
    return np.frombuffer(subprocess.check_output([
        'ffmpeg', '-v', 'error', '-ss', str(time), '-i', str(path),
        '-frames:v', '1', '-vf', 'scale=45:80', '-pix_fmt', 'rgb24',
        '-f', 'rawvideo', '-']), dtype=np.uint8).astype(float)


def _media_project(endpoint):
    base, picture = endpoint.directory / 'clip.mp4', endpoint.directory / 'broll_1_clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=green:s=90x160:r=24:d=6', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(base)], check=True)
    asset = manual_broll.save_media(endpoint.directory, 0, make_image('blue'), 'blue.png', 'image/png')
    cues = [{'id': 'cover', 'asset': asset['asset'], 'start': 0, 'end': 6,
             'fit': 'stretch', 'background': 'none'}]
    placements = manual_broll.validate_placements(cues, 6, endpoint.directory)
    broll.render_cutaways(str(base), str(picture), placements)
    endpoint.clip.update(video_url='/videos/trim-job/' + picture.name,
        caption_style={'style': 'classic', 'font_name': 'Arial', 'font_size': 24, 'position': 'middle'},
        broll={'mode': 'manual', 'base_file': base.name, 'file': picture.name,
               'captions_above_media': False, 'cues': cues})
    data = json.loads(endpoint.meta.read_text())
    data['shorts'][0] = endpoint.clip
    endpoint.meta.write_text(json.dumps(data))
    return base, picture


def test_hook_recaption_keeps_captions_below_full_frame_media(trim_endpoint):
    endpoint = trim_endpoint
    base, picture = _media_project(endpoint)
    # This helper is the actual hook replacement path's caption restoration.
    output = endpoint.app._reapply_captions('trim-job', 0, str(picture))
    assert output
    assert np.abs(_frame(output) - _frame(picture)).mean() < .5


def test_recaption_preserves_new_hook_recipe_above_media(trim_endpoint):
    import hooks
    from PIL import Image

    endpoint = trim_endpoint
    base, picture = _media_project(endpoint)
    hook_image = endpoint.directory / 'new-hook.png'
    Image.new('RGBA', (20, 20), 'red').save(hook_image)
    hooked = endpoint.directory / ('hooked_2_' + picture.name)
    result = hooks._run_encode(hooks._overlay_cmd(str(picture), str(hook_image),
        hooks._overlay_graph(0, 0, 1), str(hooked)), str(picture), [str(hooked)])
    assert result.returncode == 0
    hooks._write_recipe(str(hooked), str(picture), str(hook_image), 0, 0, 1)
    output = endpoint.app._reapply_captions('trim-job', 0, str(hooked))
    assert output
    assert np.abs(_frame(output) - _frame(hooked)).mean() < .5


def test_ai_effect_below_media_rebuild_keeps_the_new_effect(trim_endpoint, monkeypatch):
    import shutil

    endpoint = trim_endpoint
    base, picture = _media_project(endpoint)
    captioned = endpoint.directory / ('subtitled_1_' + picture.name)
    shutil.copyfile(picture, captioned)  # Fully covered captions are invisible.
    endpoint.clip['video_url'] = '/videos/trim-job/' + captioned.name
    data = json.loads(endpoint.meta.read_text())
    data['shorts'][0] = endpoint.clip
    endpoint.meta.write_text(json.dumps(data))
    monkeypatch.setattr(ai, 'probe', endpoint.real_probe)
    monkeypatch.setattr(ai, 'render_edit', endpoint.real_render)
    monkeypatch.setattr(ai, 'plan_edit', lambda *args, **kwargs: ai.validate_plan({
        'operations': [{'type': 'bw_moment', 'start': 0, 'end': 6}]}, 6, False))
    result = asyncio.run(endpoint.app.edit_clip_with_ai(endpoint.app.AIEditRequest(
        job_id='trim-job', instructions='Make the whole clip black and white'), None))
    output = endpoint.directory / Path(result['new_video_url']).name
    rgb = _frame(output).reshape(-1, 3)
    assert np.max(np.ptp(rgb, axis=1)) < 5
    assert endpoint.clip['broll']['post_effects'][-1][0]['type'] == 'bw_moment'


def test_same_second_restyles_keep_distinct_files(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    monkeypatch.setattr(endpoint.app.time, 'time', lambda: 1234567890)

    def burn(source, output, subtitles, transcript, *args):
        Path(output).write_text(transcript['text'])
        return True

    monkeypatch.setattr(endpoint.app, '_burn_caption_style', burn)
    source = str(endpoint.directory / 'clip.mp4')
    first = endpoint.app._restyle_captions(source, {'text': 'first'}, 0, 6, {})
    second = endpoint.app._restyle_captions(source, {'text': 'second'}, 0, 6, {})
    assert first != second
    assert Path(first).read_text() == 'first'
    assert Path(second).read_text() == 'second'


def test_same_second_auto_caption_fallback_keeps_distinct_files(trim_endpoint, monkeypatch):
    import main
    import subtitles

    endpoint = trim_endpoint
    monkeypatch.setenv('AUTO_CAPTIONS', '1')
    monkeypatch.setenv('AUTO_CAPTION_PRESET', 'default')
    monkeypatch.setattr(main.time, 'time', lambda: 1234567890)
    monkeypatch.setattr(subtitles, 'generate_ass', lambda *args, **kwargs: True)
    calls = []

    def burn(source, captions, output, **kwargs):
        calls.append(output)
        Path(output).write_text(str(len(calls)))

    monkeypatch.setattr(subtitles, 'burn_subtitles', burn)
    source = str(endpoint.directory / 'clip.mp4')
    transcript = {'segments': [{'words': [{'word': 'word', 'start': 0, 'end': 1}]}]}
    first = main.auto_caption_clip(source, transcript, 0, 6, split_ranges=[])
    second = main.auto_caption_clip(source, transcript, 0, 6, split_ranges=[])
    assert first != second
    assert Path(first).read_text() == '1'
    assert Path(second).read_text() == '2'
