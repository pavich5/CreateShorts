"""Source edits retain separable picture, hook and layout files after restore."""
import ast
import asyncio
import glob
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import watermarked


ROOT = Path(__file__).resolve().parent.parent
MASTER = "ai_" + "a" * 32 + ".mp4"
HOOKED = "hooked_23_" + MASTER
SERVED = "subtitled_34_" + HOOKED


def helpers():
    tree = ast.parse((ROOT / "cloud" / "videos.py").read_text())
    names = {"_HOOK_CHAIN_RE", "_EDIT_LAYER_RE", "_BROLL_ASSET_RE"}
    functions = {"_canonical_name", "_twins_to_archive", "_safe_archive_file",
                 "_edit_dependencies_to_archive", "_broll_assets_to_archive",
                 "_manual_broll_dependencies_to_archive",
                 "_superseded_names", "archive_job", "archive_clip_edit", "_clip_title"}
    nodes = [node for node in tree.body
             if (isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id in names for t in node.targets))
             or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in functions)]
    namespace = {"re": re, "os": os, "watermarked": watermarked,
                 "asyncio": asyncio, "glob": glob, "json": json}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "cloud/videos.py", "exec"), namespace)
    return namespace


def write(root, name, content=b"video"):
    path = root / name
    path.write_bytes(content)
    return path


def recipe(root, video=HOOKED, source=MASTER):
    write(root, ".hookrecipe_" + video + ".json",
          json.dumps({"source": source, "x": 10, "y": 20, "duration": 3}).encode())
    write(root, ".hookrecipe_" + video + ".png", b"overlay")


@pytest.mark.parametrize("master", [MASTER, "source_visual_0123456789abcdef.mp4"])
def test_text_and_watermark_layers_keep_source_edit_master_in_legacy_order(master):
    twins = helpers()["_twins_to_archive"]
    hook = "hooked_23_" + master
    captioned = "subtitled_34_" + hook
    assert twins(captioned, "video", 0) == [hook, master]
    assert twins("wm_" + captioned, "video", 0) == [captioned, hook, master]
    assert twins(hook, "video", 0) == [master]
    assert twins(master, "video", 0) == []


def test_dependencies_keep_layout_and_hook_recipe_for_every_picture_layer(tmp_path):
    for name in (SERVED, HOOKED, MASTER):
        write(tmp_path, name)
        write(tmp_path, name + ".layout.json", b'{"ranges":[]}')
    recipe(tmp_path)
    files = helpers()["_edit_dependencies_to_archive"](str(tmp_path), SERVED, "video", 0)
    names = [name for name, _ in files]
    assert names == [SERVED + ".layout.json", HOOKED + ".layout.json",
                     ".hookrecipe_" + HOOKED + ".json", ".hookrecipe_" + HOOKED + ".png",
                     MASTER + ".layout.json"]
    assert all(path == str(tmp_path / name) for name, path in files)


def test_recipe_can_retain_a_master_whose_name_is_not_encoded_in_hook_filename(tmp_path):
    hooked = "hooked_23_video_clip_1.mp4"
    write(tmp_path, hooked)
    write(tmp_path, MASTER)
    recipe(tmp_path, video=hooked)
    names = [name for name, _ in helpers()["_edit_dependencies_to_archive"](
        str(tmp_path), hooked, "video", 0)]
    assert names == [".hookrecipe_" + hooked + ".json", ".hookrecipe_" + hooked + ".png", MASTER]


@pytest.mark.parametrize("source", ["../" + MASTER, "/tmp/" + MASTER,
                                    "ai_folder/secret.mp4", "ai_folder\\secret.mp4",
                                    "source.mp4", "secret.txt", 42, None])
def test_recipe_never_archives_unrelated_or_escaping_source_paths(tmp_path, source):
    write(tmp_path, HOOKED)
    recipe(tmp_path, source=source)
    assert helpers()["_edit_dependencies_to_archive"](str(tmp_path), HOOKED, "video", 0) == []


@pytest.mark.parametrize("linked", [MASTER, HOOKED + ".layout.json",
                                    ".hookrecipe_" + HOOKED + ".json",
                                    ".hookrecipe_" + HOOKED + ".png"])
def test_derivative_symlinks_are_not_followed(tmp_path, linked):
    write(tmp_path, HOOKED)
    write(tmp_path, MASTER)
    recipe(tmp_path)
    layout = write(tmp_path, HOOKED + ".layout.json", b'{"ranges":[]}')
    target = tmp_path / "unrelated-secret"
    original = tmp_path / linked
    target.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(target)
    names = [name for name, _ in helpers()["_edit_dependencies_to_archive"](
        str(tmp_path), HOOKED, "video", 0)]
    assert linked not in names
    if linked != layout.name:
        assert names == [layout.name]


@pytest.mark.parametrize("content", [b"bad json", b"[1,2]", b" " * (16 * 1024 + 1)])
def test_malformed_or_large_recipes_do_not_expand_archives(tmp_path, content):
    write(tmp_path, HOOKED)
    write(tmp_path, MASTER)
    recipe(tmp_path)
    write(tmp_path, ".hookrecipe_" + HOOKED + ".json", content)
    assert helpers()["_edit_dependencies_to_archive"](str(tmp_path), HOOKED, "video", 0) == []


def test_recipe_cycles_and_long_chains_are_bounded(tmp_path):
    names = [f"ai_{index}.mp4" for index in range(40)]
    for index, name in enumerate(names):
        write(tmp_path, name)
        recipe(tmp_path, name, names[(index + 1) % len(names)])
    files = helpers()["_edit_dependencies_to_archive"](str(tmp_path), names[0], None, 0)
    assert len([name for name, _ in files if name.endswith(".json")]) == 16
    assert len(files) == 48


class DatabaseBoundary(Exception):
    pass


def configure_uploads(namespace, uploaded, database):
    def upload(path, key, content_type="video/mp4"):
        uploaded.append((Path(path).name, key, content_type))
    namespace.update(settings=SimpleNamespace(r2_configured=True), database=database,
                     storage=SimpleNamespace(upload_file=upload,
                         job_key=lambda user, job, name: f"users/{user}/{job}/{name}"))


@pytest.mark.parametrize("function", ["archive_job", "archive_clip_edit"])
def test_both_archive_paths_upload_editable_source_and_hook_dependencies(tmp_path, function):
    for name in (SERVED, HOOKED, MASTER, "video_clip_1.mp4"):
        write(tmp_path, name)
    write(tmp_path, "video_metadata.json", b'{"shorts":[]}')
    write(tmp_path, MASTER + ".layout.json", b'{"ranges":[]}')
    write(tmp_path, HOOKED + ".layout.json", b'{"ranges":[]}')
    recipe(tmp_path)
    uploaded = []
    def database_session():
        raise DatabaseBoundary()
    namespace = helpers()
    configure_uploads(namespace, uploaded, SimpleNamespace(session=database_session))
    if function == "archive_clip_edit":
        # Edits now lock/check the project before uploading; the second query
        # is still our boundary after the upload dependency work under test.
        class Session:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            def begin(self):
                return self
            async def execute(self, query):
                if query == "project":
                    return SimpleNamespace(scalar_one_or_none=lambda: None)
                raise DatabaseBoundary()
        class Query:
            def __init__(self, table):
                self.table = table
            def where(self, *args):
                return self
            def with_for_update(self):
                return "project"
        namespace.update(database=SimpleNamespace(session=Session),
                         Project=SimpleNamespace(job_id=None, user_id=None),
                         UserVideo=SimpleNamespace(job_id=None, user_id=None, clip_index=None),
                         select=Query)
    args = ("user", "job", [{"video_url": "/videos/job/" + SERVED}], str(tmp_path)) if function == "archive_job" else (
        "user", "job", 0, str(tmp_path), SERVED)
    with pytest.raises(DatabaseBoundary):
        asyncio.run(namespace[function](*args))
    names = [name for name, _, _ in uploaded]
    assert names.index(SERVED) < names.index(HOOKED) < names.index(MASTER)
    assert {MASTER + ".layout.json", HOOKED + ".layout.json",
            ".hookrecipe_" + HOOKED + ".json", ".hookrecipe_" + HOOKED + ".png"} <= set(names)
    assert all(key == "users/user/job/" + name for name, key, _ in uploaded)
    assert next(kind for name, _, kind in uploaded if name == ".hookrecipe_" + HOOKED + ".json") == "application/json"
    assert next(kind for name, _, kind in uploaded if name == ".hookrecipe_" + HOOKED + ".png") == "image/png"


def test_captioning_a_previously_served_master_does_not_delete_it_from_archive(tmp_path):
    served = "subtitled_23_" + MASTER
    write(tmp_path, served)
    write(tmp_path, MASTER)
    project = SimpleNamespace(state={"v": 1, "clips": [{"index": 0,
        "server_file": MASTER, "original_file": "video_clip_1.mp4"}]})
    video = SimpleNamespace(r2_key="old", size_bytes=1)
    project_type = SimpleNamespace(job_id=None, user_id=None)
    video_type = SimpleNamespace(user_id=None, job_id=None, clip_index=None)

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def begin(self):
            return self
        async def execute(self, query):
            return SimpleNamespace(scalar_one_or_none=lambda: project,
                                   scalars=lambda: SimpleNamespace(first=lambda: video))
    uploaded, deleted = [], []
    namespace = helpers()
    configure_uploads(namespace, uploaded, SimpleNamespace(session=Session))
    query = SimpleNamespace()
    query.where = lambda *args: query
    query.with_for_update = lambda: query
    namespace.update(Project=project_type, UserVideo=video_type, select=lambda table: query)
    namespace["storage"].delete_key = deleted.append
    asyncio.run(namespace["archive_clip_edit"]("user", "job", 0, str(tmp_path), served))
    assert MASTER in [name for name, _, _ in uploaded]
    assert deleted == []
    assert project.state["clips"][0]["server_file"] == served


@pytest.mark.parametrize("local_tombstone", ["manifest", "metadata", "cloud"])
def test_removed_clip_is_never_archived_again(tmp_path, local_tombstone):
    write(tmp_path, SERVED)
    if local_tombstone == "manifest":
        write(tmp_path, ".deleted_clips.json", b'{"clips":{"0":{"status":"pending"}}}')
    if local_tombstone == "metadata":
        write(tmp_path, "video_metadata.json", b'{"shorts":[{"deleted":true}]}')
    project = SimpleNamespace(state={"clips": [], "deleted_indices": [0]})
    class Session:
        async def __aenter__(self):
            assert local_tombstone == "cloud", "Local removal must stop before cloud access"
            return self
        async def __aexit__(self, *args):
            return False
        def begin(self):
            return self
        async def execute(self, query):
            return SimpleNamespace(scalar_one_or_none=lambda: project)
    query = SimpleNamespace()
    query.where = lambda *args: query
    query.with_for_update = lambda: query
    uploaded, namespace = [], helpers()
    configure_uploads(namespace, uploaded, SimpleNamespace(session=Session))
    namespace.update(Project=SimpleNamespace(job_id=None, user_id=None), select=lambda table: query)
    asyncio.run(namespace["archive_clip_edit"]("user", "job", 0, str(tmp_path), SERVED))
    assert uploaded == []
    assert project.state == {"clips": [], "deleted_indices": [0]}


def test_a_stale_browser_save_cannot_recreate_a_removed_clip():
    tree = ast.parse((ROOT / "cloud" / "videos.py").read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "save_project_state")
    function.decorator_list = []
    project = SimpleNamespace(user_id="owner", state={"v": 1, "deleted_indices": [0],
        "clips": [{"index": 1, "active_layers": [], "server_file": "clip2.mp4"}]})
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def begin(self):
            return self
        async def execute(self, query):
            return SimpleNamespace(scalar_one_or_none=lambda: project)
    class Request:
        headers = {}
        async def json(self):
            return {"clips": [{"index": 0, "server_file": "removed.mp4", "active_layers": ["old"]},
                              {"index": 1, "active_layers": ["new"]}]}
    async def current_user(request):
        return SimpleNamespace(id="owner")
    query = SimpleNamespace()
    query.where = lambda *args: query
    query.with_for_update = lambda: query
    namespace = {"Request": Request, "get_current_user_required": current_user,
                 "database": SimpleNamespace(session=Session), "select": lambda table: query,
                 "Project": SimpleNamespace(job_id=None, user_id=None), "os": os}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "cloud/videos.py", "exec"), namespace)
    assert asyncio.run(namespace["save_project_state"]("job", Request())) == {"success": True}
    assert project.state["deleted_indices"] == [0]
    assert project.state["clips"] == [{"index": 1, "active_layers": ["new"], "server_file": "clip2.mp4"}]


def test_manual_image_edit_archives_its_uploads_and_clean_edit_base(tmp_path):
    asset = "broll_assets_123456abcdef/" + "a" * 20 + ".png"
    (tmp_path / asset).parent.mkdir()
    (tmp_path / asset).write_bytes(b"uploaded image")
    for name in (SERVED, MASTER, "ai_456_video_clip_1.mp4"):
        write(tmp_path, name)
    record = {"mode": "manual", "base_file": "ai_456_video_clip_1.mp4",
              "source_cues": [{"asset": asset}], "cues": []}
    write(tmp_path, "video_metadata.json", json.dumps({"shorts": [{"broll": record}]}).encode())
    project = SimpleNamespace(state={"clips": [{"index": 0, "server_file": MASTER,
        "original_file": "video_clip_1.mp4"}]})
    video = SimpleNamespace(r2_key="old", size_bytes=1)
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def begin(self):
            return self
        async def execute(self, query):
            return SimpleNamespace(scalar_one_or_none=lambda: project,
                                   scalars=lambda: SimpleNamespace(first=lambda: video))
    uploaded, namespace = [], helpers()
    configure_uploads(namespace, uploaded, SimpleNamespace(session=Session))
    query = SimpleNamespace()
    query.where = lambda *args: query
    query.with_for_update = lambda: query
    namespace.update(Project=SimpleNamespace(job_id=None, user_id=None),
        UserVideo=SimpleNamespace(user_id=None, job_id=None, clip_index=None), select=lambda table: query)
    namespace["storage"].delete_key = lambda key: None
    asyncio.run(namespace["archive_clip_edit"]("user", "job", 0, str(tmp_path), SERVED))
    by_key = {key: kind for _, key, kind in uploaded}
    assert by_key["users/user/job/" + asset] == "image/png"
    assert by_key["users/user/job/ai_456_video_clip_1.mp4"] == "video/mp4"
    assert len([key for _, key, _ in uploaded if key.endswith(asset)]) == 1
