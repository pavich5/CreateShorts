"""Deletion attribution must remove every owned version and protect siblings."""
import hashlib
import json

from fastapi import HTTPException
import pytest

from video_removal import _safe_path, plan_clip_files


def _file(root, name, content=b"fixture media"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _recipe(root, output, source):
    recipe = ".hookrecipe_" + output
    _file(root, recipe + ".json", json.dumps({"source": source}).encode())
    _file(root, recipe + ".png")
    return {recipe + ".json", recipe + ".png"}


def test_clip_one_does_not_match_clip_ten_or_unlinked_files(tmp_path):
    owned = {"video_clip_1.mp4", "broll_123_video_clip_1.mp4",
             "subtitled_456_broll_123_video_clip_1.mp4",
             "video_clip_1.mp4.layout.json", "video_clip_1.mp4.srt", "video_clip_1.mp4.ass"}
    unrelated = {"video_clip_10.mp4", "subtitled_123_video_clip_10.mp4",
                 "ai_unlinked.mp4", "notes.txt", "source.mp4", "video_metadata.json"}
    for name in owned | unrelated:
        _file(tmp_path, name)
    metadata = {"shorts": [{} for _ in range(10)], "source_video": "source.mp4"}
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert set(plan_clip_files(tmp_path, "job", 0, metadata)) == owned
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_unsaved_uploads_and_legacy_cache_belong_only_to_their_clip(tmp_path):
    manual_hash = hashlib.sha256(b"manual:0").hexdigest()[:12]
    sibling_hash = hashlib.sha256(b"manual:1").hexdigest()[:12]
    legacy_hash = hashlib.sha256(b"video_clip_1.mp4").hexdigest()[:12]
    ours = {f"broll_assets_{manual_hash}/unsaved.png", f"broll_assets_{manual_hash}/upload_pending.png",
            f"broll_assets_{legacy_hash}/old.jpg"}
    sibling = f"broll_assets_{sibling_hash}/saved.png"
    _file(tmp_path, "video_metadata.json", b'{"shorts":[{},{}]}')
    for name in ours | {sibling, "unlinked.png"}:
        _file(tmp_path, name)
    assert set(plan_clip_files(tmp_path, "job", 0, {"shorts": [{}, {}]})) == ours


def test_shared_images_and_transitive_hook_sources_are_preserved_for_siblings(tmp_path):
    selected = "hooked_11_video_clip_1.mp4"
    sibling = "hooked_22_video_clip_2.mp4"
    shared = "ai_shared.mp4"
    unique_image = "broll_assets_abcdef123456/only_selected.png"
    shared_image = "broll_assets_abcdef123456/shared.png"
    selected_recipe = _recipe(tmp_path, selected, shared)
    sibling_recipe = _recipe(tmp_path, sibling, shared)
    shared_files = {shared, shared + ".layout.json", shared_image}
    for name in {selected, sibling, "video_clip_1.mp4", "video_clip_2.mp4", unique_image} | shared_files:
        _file(tmp_path, name)
    metadata = {"shorts": [
        {"video_url": "/videos/job/" + selected,
         "broll": {"source_cues": [{"asset": unique_image}, {"asset": shared_image}]}},
        {"video_url": "/videos/job/" + sibling,
         "broll": {"source_cues": [{"asset": shared_image}]}},
    ]}
    plan = set(plan_clip_files(tmp_path, "job", 0, metadata))
    assert {selected, "video_clip_1.mp4", unique_image, *selected_recipe} <= plan
    assert plan.isdisjoint({sibling, "video_clip_2.mp4", *sibling_recipe, *shared_files})


def test_a_sibling_without_a_saved_url_still_keeps_its_canonical_and_sidecars(tmp_path):
    selected = "hooked_11_video_clip_1.mp4"
    sibling = "video_clip_2.mp4"
    _file(tmp_path, selected)
    _file(tmp_path, sibling)
    _file(tmp_path, sibling + ".layout.json")
    _recipe(tmp_path, selected, sibling)
    metadata = {"shorts": [{"video_url": "/videos/job/" + selected}, {}]}
    plan = set(plan_clip_files(tmp_path, "job", 0, metadata))
    assert selected in plan
    assert sibling not in plan and sibling + ".layout.json" not in plan


def test_source_is_kept_until_the_final_live_clip_is_removed(tmp_path):
    source, selected = "long_source.mp4", "video_clip_2.mp4"
    _file(tmp_path, source)
    _file(tmp_path, selected)
    clip = {"video_url": "/videos/job/" + selected}
    metadata = {"source_video": source, "shorts": [{}, clip]}
    assert source not in plan_clip_files(tmp_path, "job", 1, metadata)
    metadata["shorts"][0] = {"deleted": True, "index": 0, "video_url": None}
    assert {source, selected} <= set(plan_clip_files(tmp_path, "job", 1, metadata))
    assert (tmp_path / source).is_file()  # planning never unlinks media


@pytest.mark.parametrize("relative", ["../outside.mp4", "nested/../../outside.mp4",
    "/tmp/outside.mp4", "%2e%2e/outside.mp4", "nested/%2e%2e/%2e%2e/outside.mp4",
    "https://example.com/video.mp4", "nested\\outside.mp4"])
def test_unsafe_paths_fail_validation_without_deleting_anything(tmp_path, relative):
    sentinel = _file(tmp_path, "sentinel.mp4", b"keep me")
    with pytest.raises(HTTPException) as error:
        _safe_path(tmp_path, relative)
    assert error.value.status_code == 400
    assert sentinel.read_bytes() == b"keep me"


def test_an_unsafe_extra_reference_aborts_the_entire_plan(tmp_path):
    owned = _file(tmp_path, "video_clip_1.mp4", b"keep before validation succeeds")
    with pytest.raises(HTTPException):
        plan_clip_files(tmp_path, "job", 0, {"shorts": [{}]}, extra_references=["../outside.mp4"])
    assert owned.read_bytes() == b"keep before validation succeeds"


@pytest.mark.parametrize("intermediate", [False, True])
def test_file_and_directory_symlinks_abort_before_removal(tmp_path, intermediate):
    root, outside = tmp_path / "job", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    protected = _file(outside, "protected.mp4", b"outside data")
    if intermediate:
        (root / "linked").symlink_to(outside, target_is_directory=True)
        reference = "linked/protected.mp4"
    else:
        (root / "video_clip_1.mp4").symlink_to(protected)
        reference = "video_clip_1.mp4"
    with pytest.raises(HTTPException) as error:
        plan_clip_files(root, "job", 0, {"shorts": [{"file": reference}]})
    assert error.value.status_code == 409
    assert protected.read_bytes() == b"outside data"


def test_a_symlinked_job_root_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = _file(outside, "video.mp4", b"outside data")
    root = tmp_path / "job"
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(HTTPException) as error:
        _safe_path(root, "video.mp4")
    assert error.value.status_code == 400
    assert protected.read_bytes() == b"outside data"
