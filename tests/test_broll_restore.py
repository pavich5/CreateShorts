"""Nested B-roll caches survive a project restore without unsafe downloads.

Run the actual restore function against a local fake object store. Extracting
the small function set keeps these tests independent of cloud credentials,
the database and the video/ML dependencies imported by app.py.
"""
import ast
import asyncio
import glob
import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import ModuleType, SimpleNamespace
from typing import Optional
from unittest.mock import patch

import watermarked


ROOT = Path(__file__).resolve().parent.parent
USER = "restore-user"
JOB = "broll-restore-job"
PREFIX = f"users/{USER}/{JOB}/"
CLEAN = "video_clip_1.mp4"
BROLL = f"broll_123_{CLEAN}"
METADATA = "video_metadata.json"
ASSET = "broll_assets_123456abcdef/" + "a" * 20 + ".jpg"


class RestoreHTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _restore_functions(output_dir):
    tree = ast.parse((ROOT / "app.py").read_text())
    names = {"_safe_under", "_canonical_clip_file", "_restore_job_files"}
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    namespace = {
        "asyncio": asyncio, "glob": glob, "json": json, "os": os,
        "re": re, "shutil": shutil, "Optional": Optional,
        "watermarked": watermarked, "HTTPException": RestoreHTTPError,
        "OUTPUT_DIR": str(output_dir), "_restore_locks": {}, "jobs": {},
        "_TimedLog": list,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    return namespace


def _objects():
    cue = {"asset": ASSET, "source_start": 14.0, "source_end": 16.5,
           "start": 4.0, "end": 6.5, "duration": 2.5, "prompt": "A moving train"}
    metadata = {"shorts": [{"start": 10.0, "end": 40.0,
                            "broll": {"status": "applied", "cues": [cue],
                                      "source_cues": [dict(cue)]}}],
                "transcript": {"language": "en", "segments": []},
                "cost_analysis": {"total": 0}}
    return {PREFIX + METADATA: json.dumps(metadata).encode(),
            PREFIX + CLEAN: b"clean-video", PREFIX + BROLL: b"broll-video",
            PREFIX + ASSET: b"generated-image"}


def _restorer(tmp_path, objects=None):
    objects = _objects() if objects is None else objects
    output_dir = tmp_path / "output"
    output_dir.mkdir(exist_ok=True)
    namespace = _restore_functions(output_dir)
    calls = {"listed": [], "downloads": []}

    def list_keys(prefix):
        calls["listed"].append(prefix)
        return list(objects)

    def download_file(key, path):
        calls["downloads"].append((key, path))
        Path(path).write_bytes(objects[key])

    cloud = ModuleType("cloud")
    cloud.storage = SimpleNamespace(
        job_key=lambda user, job, name: f"users/{user}/{job}/{name}",
        list_keys=list_keys, download_file=download_file)
    project = SimpleNamespace(metadata_r2_key=PREFIX + METADATA,
                              state={"clips": [{"original_file": BROLL,
                                                "server_file": BROLL}]})

    def run():
        with patch.dict(sys.modules, {"cloud": cloud}):
            return asyncio.run(namespace["_restore_job_files"](JOB, project, USER))

    return SimpleNamespace(run=run, namespace=namespace, calls=calls, project=project,
                           output=output_dir, job=output_dir / JOB)


def test_deleted_remote_clip_cannot_restore_media_or_reindex_surviving_clip(tmp_path):
    objects = _objects()
    second = "video_clip_2.mp4"
    metadata = json.loads(objects[PREFIX + METADATA])
    metadata["shorts"].append({"start": 40, "end": 50})
    objects[PREFIX + METADATA] = json.dumps(metadata).encode()
    objects[PREFIX + second] = b"surviving video"
    # An old remote marker must never overwrite the authoritative cloud state.
    objects[PREFIX + ".deleted_clips.json"] = b'{"clips":{}}'
    restore = _restorer(tmp_path, objects)
    deleted = [CLEAN, BROLL, ASSET]
    restore.project.state = {"clips": [{"index": 1, "server_file": second, "original_file": second}],
                             "deleted_indices": [0], "deleted_files": {"0": deleted}}
    assert restore.run() is True
    clips = restore.namespace["jobs"][JOB]["result"]["clips"]
    assert clips[0] == {"deleted": True, "index": 0, "video_url": None}
    assert clips[1]["index"] == 1
    assert clips[1]["video_url"].endswith(second)
    assert not any((restore.job / name).exists() for name in deleted)
    downloaded = {key for key, _ in restore.calls["downloads"]}
    assert not downloaded.intersection({PREFIX + name for name in deleted})
    marker = json.loads((restore.job / ".deleted_clips.json").read_text())
    assert "0" in marker["clips"] and set(marker["blocked_files"]) == set(deleted)
    disk = json.loads((restore.job / METADATA).read_text())
    assert disk["shorts"][0]["deleted"] is True


def test_restores_nested_images_and_registers_complete_broll_metadata(tmp_path):
    restore = _restorer(tmp_path)
    assert restore.run() is True
    assert (restore.job / ASSET).read_bytes() == b"generated-image"
    assert not (restore.job / Path(ASSET).name).exists()
    assert (restore.job / ".owner").read_text() == USER
    assert not Path(str(restore.job) + ".restoring").exists()
    job = restore.namespace["jobs"][JOB]
    assert job["status"] == "completed" and job["user_id"] == USER
    clip = job["result"]["clips"][0]
    assert clip["video_url"] == f"/videos/{JOB}/{BROLL}"
    assert clip["broll"]["cues"][0]["asset"] == ASSET
    assert clip["broll"]["source_cues"][0]["source_start"] == 14.0
    assert job["result"]["cost_analysis"] == {"total": 0}


def test_stock_video_asset_and_playback_offset_survive_restore(tmp_path):
    objects = _objects()
    video_asset = ASSET.removesuffix(".jpg") + ".mp4"
    objects.pop(PREFIX + ASSET)
    objects[PREFIX + video_asset] = b"stock-video"
    metadata = json.loads(objects[PREFIX + METADATA])
    for key in ("cues", "source_cues"):
        cue = metadata["shorts"][0]["broll"][key][0]
        cue.update(asset=video_asset, media_type="video", media_start=1.25)
    objects[PREFIX + METADATA] = json.dumps(metadata).encode()
    restore = _restorer(tmp_path, objects)
    assert restore.run() is True
    assert (restore.job / video_asset).read_bytes() == b"stock-video"
    assert not (restore.job / Path(video_asset).name).exists()
    record = restore.namespace["jobs"][JOB]["result"]["clips"][0]["broll"]
    for key in ("cues", "source_cues"):
        assert record[key][0]["asset"] == video_asset
        assert record[key][0]["media_type"] == "video"
        assert record[key][0]["media_start"] == 1.25


def test_restoring_into_an_existing_job_merges_nested_assets(tmp_path):
    restore = _restorer(tmp_path)
    cache = restore.job / Path(ASSET).parent
    cache.mkdir(parents=True)
    retained = cache / ("b" * 20 + ".jpg")
    retained.write_bytes(b"existing-image")
    (cache / Path(ASSET).name).write_bytes(b"stale-image")
    (restore.job / "local-note.txt").write_text("retain")
    assert restore.run() is True
    assert (restore.job / ASSET).read_bytes() == b"generated-image"
    assert retained.read_bytes() == b"existing-image"
    assert (restore.job / "local-note.txt").read_text() == "retain"
    assert (restore.job / BROLL).read_bytes() == b"broll-video"
    assert not Path(str(restore.job) + ".restoring").exists()


def test_complete_restored_project_uses_the_existing_files(tmp_path):
    restore = _restorer(tmp_path)
    assert restore.run() is True
    prior_downloads = list(restore.calls["downloads"])
    assert restore.run() is False
    assert restore.calls["downloads"] == prior_downloads
    assert (restore.job / ASSET).read_bytes() == b"generated-image"
    assert restore.namespace["jobs"][JOB]["result"]["clips"][0]["broll"]["status"] == "applied"


def test_a_storage_key_outside_the_project_prefix_is_refused(tmp_path):
    restore = _restorer(tmp_path, {f"users/another-user/{JOB}/secret.jpg": b"private"})
    try:
        restore.run()
    except RestoreHTTPError as exc:
        assert exc.status_code == 502 and "storage prefix" in exc.detail
    else:
        raise AssertionError("A foreign storage key was accepted")
    assert restore.calls["downloads"] == []
    assert not restore.job.exists()
    assert not Path(str(restore.job) + ".restoring").exists()
    assert JOB not in restore.namespace["jobs"]


def test_a_traversal_storage_key_cannot_escape_the_restore_directory(tmp_path):
    restore = _restorer(tmp_path, {PREFIX + "../../escaped.jpg": b"private"})
    try:
        restore.run()
    except RestoreHTTPError as exc:
        assert exc.status_code == 502 and "invalid storage path" in exc.detail
    else:
        raise AssertionError("A traversal storage key was accepted")
    assert restore.calls["downloads"] == []
    assert not (tmp_path / "escaped.jpg").exists()
    assert not Path(str(restore.job) + ".restoring").exists()


def test_an_absolute_storage_suffix_cannot_escape_the_restore_directory(tmp_path):
    target = tmp_path / "escaped.jpg"
    restore = _restorer(tmp_path, {PREFIX + str(target): b"private"})
    try:
        restore.run()
    except RestoreHTTPError as exc:
        assert exc.status_code == 502 and "invalid storage path" in exc.detail
    else:
        raise AssertionError("An absolute storage suffix was accepted")
    assert restore.calls["downloads"] == []
    assert not target.exists()


def test_an_existing_cache_directory_symlink_cannot_redirect_the_merge(tmp_path):
    restore = _restorer(tmp_path)
    restore.job.mkdir()
    (restore.job / METADATA).write_bytes(b"original-metadata")
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    protected = outside / Path(ASSET).name
    protected.write_bytes(b"untouched")
    (restore.job / Path(ASSET).parent).symlink_to(outside, target_is_directory=True)
    try:
        restore.run()
    except RestoreHTTPError as exc:
        assert exc.status_code == 502 and "invalid restore path" in exc.detail
    else:
        raise AssertionError("A destination cache symlink redirected the restore")
    assert protected.read_bytes() == b"untouched"
    assert (restore.job / METADATA).read_bytes() == b"original-metadata"
    assert not (restore.job / BROLL).exists()
    assert not Path(str(restore.job) + ".restoring").exists()
    assert JOB not in restore.namespace["jobs"]


def test_an_existing_image_symlink_cannot_redirect_the_merge(tmp_path):
    restore = _restorer(tmp_path)
    asset = restore.job / ASSET
    asset.parent.mkdir(parents=True)
    protected = tmp_path / "outside-image.jpg"
    protected.write_bytes(b"untouched")
    asset.symlink_to(protected)
    try:
        restore.run()
    except RestoreHTTPError as exc:
        assert exc.status_code == 502 and "invalid restore path" in exc.detail
    else:
        raise AssertionError("A destination file symlink redirected the restore")
    assert protected.read_bytes() == b"untouched"
    assert not (restore.job / METADATA).exists()
    assert not Path(str(restore.job) + ".restoring").exists()
