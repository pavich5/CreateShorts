"""Permanent removal exercises disposable files only; never the user's library."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
import httpx
import pytest

from campaigns import Campaigns
from folder_library import FolderLibrary, file_id
from video_removal import (MANIFEST, apply_tombstones, install_video_removal_routes,
                           media_deleted, plan_clip_files, read_deleted_indices, _cloud_remove)


def write(root, name, value=b"disposable test video"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return path


@pytest.fixture
def project(tmp_path):
    output, library_root = tmp_path / "output", tmp_path / "Clips"
    root = output / "job"
    root.mkdir(parents=True)
    library_root.mkdir()
    clips = [{"video_url": f"/videos/job/talk_clip_{i + 1}.mp4", "start": 0, "end": 5} for i in range(2)]
    metadata = {"shorts": copy.deepcopy(clips), "source_video": "source.mp4"}
    metadata_path = root / "talk_metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    for name in ("talk_clip_1.mp4", "talk_clip_2.mp4", "wm_subtitled_12_talk_clip_1.mp4",
                 "talk_clip_1.mp4.layout.json", "source.mp4", "unrelated.mp4"):
        write(root, name)
    store = Campaigns(tmp_path / "campaigns.json")
    library = FolderLibrary(library_root, store)
    campaign = store.create("History")
    store.assign(["job/project"], campaign["id"])
    store.initialize_shorts("job", 2)

    async def owner(request, record):
        if request.headers.get("X-Wrong-Owner"):
            raise HTTPException(404, "Not found")

    async def ensure(*args):
        return True

    runtime = {"OUTPUT_DIR": str(output), "UPLOAD_DIR": str(tmp_path / "uploads"),
        "BILLING_ENABLED": False, "LOCAL_CAMPAIGNS": store, "LOCAL_FOLDER_LIBRARY": library,
        "jobs": {"job": {"status": "completed", "result": {"clips": copy.deepcopy(clips)}}},
        "_rerender_locks": {}, "_assert_job_owner": owner, "_ensure_job_files": ensure}
    app = FastAPI()
    started, finish = asyncio.Event(), asyncio.Event()

    @app.post("/api/clip/rerender")
    async def render(request: Request):
        started.set()
        await finish.wait()
        return {"success": True}

    @app.get("/api/clip/{job_id}/{clip_index}/edl")
    async def edl(job_id: str, clip_index: int):
        return {"should_not_reach_removed_clip": True}

    install_video_removal_routes(app, runtime)
    return app, runtime, root, metadata_path, library, store, started, finish


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_remove_versions_saved_aliases_and_drafts_preserves_sibling_and_source(project):
    app, runtime, root, metadata_path, library, store, *_ = project
    copies = [library.save_copy(root / "talk_clip_1.mp4", name, source_key="job/clip/0")
              for name in ("first.mp4", "second.mp4")]
    ids = [file_id(relative) for relative in copies]
    for fid in ids:
        for prefix in ("manual_", "ai_"):
            draft = Path(runtime["OUTPUT_DIR"]) / (prefix + fid)
            write(draft, "draft.mp4")
            write(draft, "nested/image.png")
            runtime["jobs"][prefix + fid] = {"status": "completed"}
    cache = "broll_assets_" + hashlib.sha256(b"manual:0").hexdigest()[:12] + "/unused.png"
    write(root, cache)

    async def run():
        async with client(app) as http:
            response = await http.delete("/api/clip/job/0")
            assert response.status_code == 200, response.text
            assert set(response.json()["removed"]["file_ids"]) == set(ids)
            assert response.json()["remaining_clips"] == 1
            assert (await http.get("/api/clip/job/0/edl")).status_code == 404
            assert (await http.get("/api/clip/job/1/edl")).status_code == 200
            # A retry succeeds using the durable plan, without recovering a clip.
            assert (await http.delete("/api/clip/job/0")).status_code == 200
    asyncio.run(run())
    assert not (root / "talk_clip_1.mp4").exists()
    assert not (root / "wm_subtitled_12_talk_clip_1.mp4").exists()
    assert not (root / cache).exists()
    for name in ("talk_clip_2.mp4", "source.mp4", "unrelated.mp4"):
        assert (root / name).exists()
    assert all(not (library.root / relative).exists() for relative in copies)
    for fid in ids:
        assert "manual_" + fid not in runtime["jobs"]
        assert not list((Path(runtime["OUTPUT_DIR"]) / ("manual_" + fid)).rglob("*.mp4"))
    data = json.loads(metadata_path.read_text())
    assert data["shorts"][0] == {"deleted": True, "index": 0, "video_url": None}
    assert data["shorts"][1]["video_url"].endswith("clip_2.mp4")
    assert len(runtime["jobs"]["job"]["result"]["clips"]) == 2
    assert read_deleted_indices(root) == {0}
    assert media_deleted(root, "talk_clip_1.mp4")
    store.initialize_shorts("job", 3)
    assert "job/clip/0" not in store.snapshot()["clips"]


def test_remove_saved_alias_removes_logical_origin_and_all_saved_copies(project):
    app, runtime, root, _, library, *_ = project
    copies = [library.save_copy(root / "talk_clip_1.mp4", name, source_key="job/clip/0")
              for name in ("one.mp4", "two.mp4")]
    async def run():
        async with client(app) as http:
            response = await http.delete("/api/clip/file:" + file_id(copies[0]) + "/0")
            assert response.status_code == 200, response.text
            assert {"job_id": "job", "clip_index": 0} in response.json()["removed"]["clip_ids"]
    asyncio.run(run())
    assert not (root / "talk_clip_1.mp4").exists()
    assert all(not (library.root / relative).exists() for relative in copies)


def test_remove_uses_original_index_when_earlier_failed_render_compacted_memory(project):
    app, runtime, _, metadata_path, *_ = project
    surviving = copy.deepcopy(runtime["jobs"]["job"]["result"]["clips"][1])
    surviving["index"] = 1
    runtime["jobs"]["job"]["result"]["clips"] = [surviving]
    async def run():
        async with client(app) as http:
            response = await http.delete("/api/clip/job/1")
            assert response.status_code == 200, response.text
    asyncio.run(run())
    clips = runtime["jobs"]["job"]["result"]["clips"]
    assert clips == [{"deleted": True, "index": 1, "video_url": None}]
    assert json.loads(metadata_path.read_text())["shorts"][1]["deleted"] is True


def test_standalone_file_and_all_saved_aliases_are_tracked(project):
    app, runtime, root, _, library, store, *_ = project
    original = write(library.root, "History/Generated/original.mp4")
    fid = file_id(original.relative_to(library.root).as_posix())
    copies = [library.save_copy(original, name, source_key="file/" + fid) for name in ("one.mp4", "two.mp4")]
    async def run():
        async with client(app) as http:
            response = await http.delete("/api/clip/file:" + fid + "/0")
            assert response.status_code == 200, response.text
            assert set(response.json()["removed"]["file_ids"]) == {fid, *(file_id(r) for r in copies)}
            assert (await http.delete("/api/clip/file:" + fid + "/0")).status_code == 200
    asyncio.run(run())
    assert not original.exists()
    assert all(not (library.root / relative).exists() for relative in copies)
    assert (root / "talk_clip_1.mp4").exists()


def test_wrong_owner_or_busy_processing_never_deletes_files(project):
    app, runtime, root, _, *_ = project
    async def run():
        async with client(app) as http:
            assert (await http.delete("/api/clip/job/0", headers={"X-Wrong-Owner": "1"})).status_code == 404
            runtime["jobs"]["job"]["status"] = "processing"
            assert (await http.delete("/api/clip/job/0")).status_code == 409
    asyncio.run(run())
    assert (root / "talk_clip_1.mp4").exists()
    assert not (root / MANIFEST).exists()


def test_active_legacy_render_blocks_removal_without_racing_its_inputs(project):
    app, _, root, _, _, _, started, finish = project
    async def run():
        async with client(app) as http:
            rendering = asyncio.create_task(http.post("/api/clip/rerender", json={"job_id": "job", "clip_index": 0}))
            await asyncio.wait_for(started.wait(), 2)
            try:
                response = await http.delete("/api/clip/job/0")
                assert response.status_code == 409, response.text
                assert (root / "talk_clip_1.mp4").exists()
            finally:
                finish.set()
                assert (await rendering).status_code == 200
            assert (await http.delete("/api/clip/job/0")).status_code == 200
    asyncio.run(run())


def test_symlink_asset_stops_entire_removal_before_any_unlink(project, tmp_path):
    app, _, root, _, *_ = project
    outside = write(tmp_path, "outside.mp4")
    (root / "talk_clip_1.mp4").unlink()
    (root / "talk_clip_1.mp4").symlink_to(outside)
    async def run():
        async with client(app) as http:
            response = await http.delete("/api/clip/job/0")
            assert response.status_code == 409, response.text
    asyncio.run(run())
    assert outside.exists()
    assert (root / "wm_subtitled_12_talk_clip_1.mp4").exists()
    assert not (root / MANIFEST).exists()


def test_partial_unlink_failure_is_retryable_and_does_not_claim_success(project, monkeypatch):
    app, _, root, _, *_ = project
    original_unlink = Path.unlink
    failed = {"yes": True}
    def unlink(path, *args, **kwargs):
        if path == root / "talk_clip_1.mp4" and failed["yes"]:
            raise PermissionError("test failure")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    async def run():
        async with client(app) as http:
            response = await http.delete("/api/clip/job/0")
            assert response.status_code == 503, response.text
            assert response.json()["detail"]["retryable"] is True
            assert (root / "talk_clip_1.mp4").exists()
            assert media_deleted(root, "talk_clip_1.mp4")
            failed["yes"] = False
            assert (await http.delete("/api/clip/job/0")).status_code == 200
    asyncio.run(run())
    assert not (root / "talk_clip_1.mp4").exists()


def test_shared_hook_master_and_sibling_recipe_are_preserved(project):
    _, _, root, metadata_path, *_ = project
    metadata = json.loads(metadata_path.read_text())
    master = "ai_" + "a" * 32 + ".mp4"
    write(root, master)
    for index in (0, 1):
        name = f"hooked_12_talk_clip_{index + 1}.mp4"
        metadata["shorts"][index]["video_url"] = "/videos/job/" + name
        write(root, name)
        write(root, ".hookrecipe_" + name + ".json", json.dumps({"source": master}).encode())
        write(root, ".hookrecipe_" + name + ".png")
    files = set(plan_clip_files(root, "job", 0, metadata))
    assert "hooked_12_talk_clip_1.mp4" in files
    assert ".hookrecipe_hooked_12_talk_clip_1.mp4.png" in files
    assert master not in files
    assert not any("clip_2" in name for name in files)
    # Legacy metadata has no URLs; inferred sibling canonical protection remains.
    metadata["shorts"][1].pop("video_url")
    (root / ".hookrecipe_hooked_12_talk_clip_1.mp4.json").write_text(json.dumps({"source": "talk_clip_2.mp4"}))
    assert "talk_clip_2.mp4" not in plan_clip_files(root, "job", 0, metadata)


def test_recovery_merges_tombstone_without_reindexing_survivors(project):
    _, _, root, metadata_path, *_ = project
    (root / MANIFEST).write_text(json.dumps({"clips": {"0": {"files": ["talk_clip_1.mp4"]}}}))
    data = apply_tombstones(root, json.loads(metadata_path.read_text()))
    assert data["shorts"][0]["deleted"] is True
    assert data["shorts"][1]["index"] == 1


def test_last_clip_removes_owned_source_and_upload_but_not_unrelated_files(project):
    app, runtime, root, _, *_ = project
    upload = write(Path(runtime["UPLOAD_DIR"]), "job_original.mp4")
    unrelated = write(Path(runtime["UPLOAD_DIR"]), "other_original.mp4")
    async def run():
        async with client(app) as http:
            assert (await http.delete("/api/clip/job/0")).status_code == 200
            assert upload.exists() and (root / "source.mp4").exists()
            assert (await http.delete("/api/clip/job/1")).status_code == 200
    asyncio.run(run())
    assert not upload.exists()
    assert not (root / "source.mp4").exists()
    assert unrelated.exists() and (root / "unrelated.mp4").exists()


def test_cloud_delete_catches_archive_completed_after_initial_plan(project, monkeypatch):
    _, _, root, metadata_path, *_ = project
    # The first plan saw canonical; the row lock must also see the final edit
    # committed by an archive that ran immediately before removal acquired it.
    final = "wm_subtitled_77_ai_" + "a" * 32 + ".mp4"
    clean = final.removeprefix("wm_").removeprefix("subtitled_77_")
    state = {"clips": [{"index": 0, "server_file": final, "original_file": "talk_clip_1.mp4"},
                        {"index": 1, "server_file": "talk_clip_2.mp4", "original_file": "talk_clip_2.mp4"}]}
    project_row = SimpleNamespace(state=state)
    project_model = SimpleNamespace(job_id=None, user_id=None)
    video_model = SimpleNamespace(job_id=None, user_id=None, clip_index=None)
    prefix = "users/owner/job/"
    keys = {prefix + name for name in [final, final.removeprefix("wm_"), clean, "talk_clip_1.mp4", "talk_clip_2.mp4"]}
    calls = {"locked": False, "deleted": [], "uploaded": [], "rows_deleted": False}
    class Query:
        def __init__(self, kind, table):
            self.kind, self.table = kind, table
        def where(self, *args):
            return self
        def with_for_update(self):
            calls["locked"] = True
            return self
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def begin(self):
            return self
        async def execute(self, query):
            if query.kind == "delete":
                calls["rows_deleted"] = True
                return None
            if query.table is project_model:
                return SimpleNamespace(scalar_one_or_none=lambda: project_row)
            assert calls["locked"]
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [SimpleNamespace(r2_key=prefix + final)]))
    cloud = ModuleType("cloud")
    cloud.database = SimpleNamespace(session=Session)
    cloud.storage = SimpleNamespace(job_key=lambda user, job, name: f"users/{user}/{job}/{name}",
        list_keys=lambda prefix: list(keys), delete_key=lambda key: calls["deleted"].append(key),
        upload_file=lambda path, key, kind: calls["uploaded"].append(key))
    models = ModuleType("cloud.models")
    models.Project, models.UserVideo = project_model, video_model
    sqlalchemy = ModuleType("sqlalchemy")
    sqlalchemy.select = lambda table: Query("select", table)
    sqlalchemy.delete = lambda table: Query("delete", table)
    for key, module in {"cloud": cloud, "cloud.models": models, "sqlalchemy": sqlalchemy}.items():
        monkeypatch.setitem(sys.modules, key, module)
    data = json.loads(metadata_path.read_text())
    data["shorts"][0] = {"deleted": True, "index": 0, "video_url": None}
    metadata_path.write_text(json.dumps(data))
    (root / MANIFEST).write_text(json.dumps({"clips": {"0": {"files": ["talk_clip_1.mp4"]}}, "blocked_files": []}))
    files = ["talk_clip_1.mp4"]
    errors = asyncio.run(_cloud_remove("owner", "job", 0, files, metadata_path, root))
    assert not errors and calls["rows_deleted"] and calls["locked"]
    assert {prefix + final, prefix + clean, prefix + "talk_clip_1.mp4"}.issubset(calls["deleted"])
    assert prefix + "talk_clip_2.mp4" not in calls["deleted"]
    assert project_row.state["deleted_indices"] == [0]
    assert [entry["index"] for entry in project_row.state["clips"]] == [1]
    assert final in project_row.state["deleted_files"]["0"]
    assert media_deleted(root, final)
