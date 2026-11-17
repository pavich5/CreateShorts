"""Imported library videos become local drafts without touching originals."""
from pathlib import Path
import json
from types import SimpleNamespace
import threading

import pytest
from fastapi import HTTPException

import manual_broll_library as library


def test_library_video_import_is_stable_and_never_overwrites_original(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original library video")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(library, "_probe_video", lambda path: {"duration": 8, "width": 160, "height": 240})
    state = {"BILLING_ENABLED": False, "OUTPUT_DIR": str(output), "jobs": {},
             "LOCAL_FOLDER_LIBRARY": SimpleNamespace(locate=lambda fid: (original, {"title": "My video"}))}
    job_id = library.materialize_library_job(state, "a" * 64)
    assert original.read_bytes() == b"original library video"
    assert state["jobs"][job_id]["result"]["clips"][0]["duration_seconds"] == 8
    draft = output / job_id / (job_id + "_clip_1.mp4")
    assert draft.read_bytes() == original.read_bytes()
    draft.write_bytes(b"independent draft")
    assert library.materialize_library_job(state, "a" * 64) == job_id
    assert draft.read_bytes() == b"independent draft"
    assert original.read_bytes() == b"original library video"


def test_explicit_ai_draft_continues_in_media_editor_once_without_resetting_later_edits(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original video")
    output = tmp_path / "output"
    output.mkdir()
    fid = "a" * 64
    filename = "ai_" + "b" * 32 + ".mp4"
    ai_root = output / ("ai_" + fid)
    ai_root.mkdir()
    (ai_root / filename).write_bytes(b"existing AI edit")
    monkeypatch.setattr(library, "_probe_video", lambda path: {"duration": 5, "width": 160, "height": 240})
    state = {"BILLING_ENABLED": False, "OUTPUT_DIR": str(output), "jobs": {},
             "LOCAL_FOLDER_LIBRARY": SimpleNamespace(locate=lambda _: (original, {"title": "My video"}))}
    job_id = library.materialize_library_job(state, fid, filename)
    clip = state["jobs"][job_id]["result"]["clips"][0]
    assert clip["video_url"].endswith("manual_import_" + filename)
    root = output / job_id
    assert (root / ("manual_import_" + filename)).read_bytes() == b"existing AI edit"
    metadata = root / (job_id + "_metadata.json")
    saved = json.loads(metadata.read_text())
    saved["shorts"][0]["video_url"] = f"/videos/{job_id}/later_media.mp4"
    metadata.write_text(json.dumps(saved))
    state["jobs"].pop(job_id)
    assert library.materialize_library_job(state, fid, filename) == job_id
    assert state["jobs"][job_id]["result"]["clips"][0]["video_url"].endswith("later_media.mp4")
    assert original.read_bytes() == b"original video"
    assert (ai_root / filename).read_bytes() == b"existing AI edit"


@pytest.mark.parametrize("filename", ["../secret.mp4", "ai_bad.mp4", "/tmp/private.mp4"])
def test_media_library_import_cannot_select_arbitrary_ai_paths(tmp_path, monkeypatch, filename):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original video")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(library, "_probe_video", lambda path: {"duration": 5, "width": 160, "height": 240})
    state = {"BILLING_ENABLED": False, "OUTPUT_DIR": str(output), "jobs": {},
             "LOCAL_FOLDER_LIBRARY": SimpleNamespace(locate=lambda _: (original, {"title": "My video"}))}
    job_id = library.materialize_library_job(state, "a" * 64, filename)
    assert state["jobs"][job_id]["result"]["clips"][0]["video_url"].endswith(job_id + "_clip_1.mp4")


@pytest.mark.parametrize("identifier,cloud", [("../private", False), ("a" * 64, True)])
def test_library_import_is_local_only_and_rejects_unsafe_ids(identifier, cloud):
    state = {"BILLING_ENABLED": cloud}
    with pytest.raises(HTTPException) as error:
        library.materialize_library_job(state, identifier)
    assert error.value.status_code == 404


@pytest.mark.parametrize("marker", ["ledger", "draft"])
def test_removed_library_video_cannot_be_imported_again(tmp_path, marker):
    """An old browser cannot rebuild an erased draft, even if a file remains."""
    fid = "a" * 64
    state = {"BILLING_ENABLED": False, "OUTPUT_DIR": str(tmp_path), "jobs": {}}
    if marker == "ledger":
        state["LOCAL_CAMPAIGNS"] = SimpleNamespace(
            lock=threading.RLock(), snapshot=lambda: {"video_removals": {fid: {}}})
    else:
        draft = tmp_path / ("manual_" + fid)
        draft.mkdir()
        (draft / ".deleted_clips.json").write_text('{"clips":{"0":{}}}')
    with pytest.raises(HTTPException) as error:
        library.materialize_library_job(state, fid)
    assert error.value.status_code == 404
    assert "removed" in error.value.detail
