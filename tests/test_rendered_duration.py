import json

import pytest

from clip_timing import rendered_duration, reconcile_rendered_clip, write_render_timing


def test_duration_cache_is_bound_to_the_actual_file_version(tmp_path, monkeypatch):
    video = tmp_path / 'clip.mp4'
    video.write_bytes(b'first render')
    calls = []
    def probe(path):
        calls.append(path)
        return {'duration': 29.5295 if len(calls) == 1 else 48.4}
    monkeypatch.setattr('ai_clip_edit.probe', probe)
    assert rendered_duration(video) == 29.5295
    assert rendered_duration(video) == 29.5295
    assert len(calls) == 1
    video.write_bytes(b'a newer and longer render')
    assert rendered_duration(video) == 48.4
    assert len(calls) == 2


def test_recovered_project_uses_finished_file_and_persists_real_duration(tmp_path, monkeypatch):
    app = pytest.importorskip('app')
    root = tmp_path / 'job'
    root.mkdir()
    video = root / 'finished_clip_1.mp4'
    video.write_bytes(b'completed render')
    (root / 'subtitled_999_video_clip_1.mp4').write_bytes(b'unrelated newer candidate')
    metadata = root / 'video_metadata.json'
    metadata.write_text(json.dumps({'shorts': [{
        'start': 0, 'end': 48.4, 'rendered_file': video.name,
    }], 'transcript': {'segments': []}}))
    monkeypatch.setattr(app, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(app, 'jobs', {})
    monkeypatch.setattr(app, '_job_cancelled', lambda _: False)
    monkeypatch.setattr('ai_clip_edit.probe', lambda _: {'duration': 29.5295})
    app._recover_jobs_from_disk()
    clip = app.jobs['job']['result']['clips'][0]
    assert clip['video_url'] == '/videos/job/finished_clip_1.mp4'
    assert clip['duration_seconds'] == 29.5295
    saved = json.loads(metadata.read_text())['shorts'][0]
    assert saved['duration_seconds'] == 29.5295
    # A file duration alone does not justify inventing a source range.
    assert (saved['start'], saved['end']) == (0, 48.4)


def test_completed_result_keeps_current_edit_and_measures_its_file(tmp_path, monkeypatch):
    app = pytest.importorskip('app')
    (tmp_path / 'edited.mp4').write_bytes(b'current edited render')
    (tmp_path / 'original.mp4').write_bytes(b'earlier generation')
    monkeypatch.setattr(app, 'jobs', {})
    monkeypatch.setattr('ai_clip_edit.probe', lambda _: {'duration': 19.23})
    clips, missing = app._clips_actually_rendered('job', str(tmp_path), 'video', [{
        'video_url': '/videos/job/edited.mp4', 'rendered_file': 'original.mp4',
        'start': 0, 'end': 48.4,
    }])
    assert missing == 0
    assert clips[0]['video_url'] == '/videos/job/edited.mp4'
    assert clips[0]['duration_seconds'] == 19.23


def test_generation_restart_restores_metadata_bound_to_the_finished_render(tmp_path, monkeypatch):
    video = tmp_path / 'finished.mp4'
    video.write_bytes(b'finished generation')
    finished = {'start': 10, 'end': 39.5, 'duration_seconds': 29.5,
                'video_title_for_youtube_short': 'Finished clip', 'rendered_file': video.name}
    write_render_timing(video, 10, 39.5, clip=finished)
    planned = {'start': 0, 'end': 48.4, 'video_title_for_youtube_short': 'New unfinished selection',
               'video_url': '/videos/job/finished.mp4'}
    monkeypatch.setattr('ai_clip_edit.probe', lambda _: {'duration': 29.5})
    repaired = reconcile_rendered_clip(tmp_path, planned)
    assert repaired['start'] == 10 and repaired['end'] == 39.5
    assert repaired['video_title_for_youtube_short'] == 'Finished clip'
    assert repaired['duration_seconds'] == 29.5
    assert 'video_url' not in repaired
    # A later edit's metadata remains authoritative over its original snapshot.
    edited = {**planned, 'ai_edit': {'changes': ['Trimmed']}, 'duration_seconds': 29.5}
    assert reconcile_rendered_clip(tmp_path, edited) == {'duration_seconds': 29.5}
