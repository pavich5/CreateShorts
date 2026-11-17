"""Trim outputs retain sound and publish their actual encoded duration."""
import asyncio
import json
from pathlib import Path
import subprocess

import pytest

import ai_clip_edit as ai
from test_ai_trim import trim_endpoint


def test_middle_cut_keeps_matroska_audio_without_stream_duration(tmp_path):
    source, output = tmp_path / 'source.mkv', tmp_path / 'removed.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=blue:s=90x160:r=24:d=4', '-f', 'lavfi', '-i',
        'sine=frequency=440:duration=4', '-c:v', 'libx264', '-pix_fmt',
        'yuv420p', '-c:a', 'aac', str(source)], check=True)
    streams = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(source)]))['streams']
    audio = next(stream for stream in streams if stream['codec_type'] == 'audio')
    assert 'duration' not in audio  # Real container behavior, not a probe mock.
    info = ai.probe(source)
    assert info['has_audio'] is True
    assert 'audio_duration' not in info

    plan = ai.validate_plan({'operations': [
        {'type': 'remove', 'start': 1, 'end': 2}]}, info['duration'], False)
    ai.render_edit(source, output, plan, info)
    rendered = ai.probe(output)
    assert rendered['has_audio'] is True
    assert rendered['duration'] == pytest.approx(info['duration'] - 1, abs=.15)
    assert rendered['width'] * 16 == rendered['height'] * 9
    samples = subprocess.check_output([
        'ffmpeg', '-v', 'error', '-ss', '1.5', '-i', str(output),
        '-t', '0.2', '-vn', '-ac', '1', '-f', 's16le', '-'])
    assert len(samples) > 1000
    assert any(samples)


def test_trim_response_and_saved_duration_use_actual_encoded_frames(trim_endpoint, monkeypatch):
    endpoint = trim_endpoint
    source = endpoint.directory / 'clip.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=blue:s=90x160:r=24:d=6', '-f', 'lavfi', '-i',
        'sine=frequency=440:duration=6', '-c:v', 'libx264', '-pix_fmt',
        'yuv420p', '-c:a', 'aac', str(source)], check=True)
    monkeypatch.setattr(ai, 'probe', endpoint.real_probe)
    monkeypatch.setattr(ai, 'render_edit', endpoint.real_render)
    result = asyncio.run(endpoint.app.trim_clip(endpoint.app.ClipTrimRequest(
        job_id='trim-job', start=.125, end=5.104002), None))
    output = endpoint.directory / Path(result['new_video_url']).name
    actual = ai.probe(output)['duration']
    assert actual == pytest.approx(5, abs=.001)
    # Theoretical 4.979002 seconds rounds into five seconds of encoded video.
    # Every visible duration and the persisted state must describe that file.
    assert result['duration_seconds'] == actual
    assert result['preview_end'] == actual
    assert endpoint.clip['duration_seconds'] == actual
    saved = json.loads(endpoint.meta.read_text())['shorts'][0]
    assert saved['duration_seconds'] == actual
