"""A restored B-roll clip retains the pictures needed to replace text layers.

The archive-name resolver is pure logic. Extract it so these regressions also
run without cloud database/billing dependencies or provider credentials.
"""
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


def _archive_helpers():
    tree = ast.parse((ROOT / "cloud" / "videos.py").read_text())
    names = {"_HOOK_CHAIN_RE", "_EDIT_LAYER_RE", "_BROLL_ASSET_RE"}
    functions = {"_canonical_name", "_twins_to_archive", "_broll_assets_to_archive",
                 "_manual_broll_dependencies_to_archive",
                 "_safe_archive_file", "_edit_dependencies_to_archive", "archive_job", "_clip_title"}
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in names for t in node.targets))
        or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions)
    ]
    namespace = {"re": re, "os": os, "watermarked": watermarked,
                 "asyncio": asyncio, "glob": glob, "json": json}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "cloud/videos.py", "exec"),
         namespace)
    return namespace


helpers = _archive_helpers()
twins = helpers["_twins_to_archive"]
assets_to_archive = helpers["_broll_assets_to_archive"]
manual_dependencies = helpers["_manual_broll_dependencies_to_archive"]
CLEAN = "video_clip_1.mp4"
BROLL = f"broll_123_{CLEAN}"
HOOKED = f"hooked_234_{BROLL}"
CAPTIONED = f"subtitled_345_{HOOKED}"


def test_captioned_broll_archives_its_picture():
    assert twins(f"subtitled_345_{BROLL}", "video", 0) == [BROLL]


def test_captioned_hook_broll_archives_both_editable_layers():
    assert twins(CAPTIONED, "video", 0) == [HOOKED, BROLL]


def test_marked_chain_keeps_the_clean_twin_before_editable_layers():
    assert twins(f"wm_{CAPTIONED}", "video", 0) == [CAPTIONED, HOOKED, BROLL]


def test_uncaptioned_hook_also_keeps_the_broll_picture():
    assert twins(HOOKED, "video", 0) == [BROLL]
    assert twins(f"hook_{BROLL}", "video", 0) == [BROLL]


def test_unlayered_broll_is_already_archived_as_the_served_file():
    assert twins(BROLL, "video", 0) == []
    assert twins(f"wm_{BROLL}", "video", 0) == [BROLL]


def test_broll_over_a_recut_keeps_the_reusable_picture():
    picture = f"broll_567_recut_456_abcd_{CLEAN}"
    assert twins(f"subtitled_678_{picture}", "video", 0) == [picture]


def test_legacy_archive_order_is_unchanged():
    hooked = f"hooked_234_{CLEAN}"
    captioned = f"subtitled_345_{hooked}"
    assert twins(f"wm_{captioned}", "video", 0) == [captioned, hooked]
    assert twins(captioned, "video", 0) == [hooked]
    assert twins(f"subtitled_345_{CLEAN}", "video", 0) == []
    assert twins(f"wm_{CLEAN}", "video", 0) == []
    assert twins(CLEAN, "video", 0) == []


def test_a_canonical_named_like_broll_is_still_the_callers_job():
    canonical = "broll_123_video_clip_1.mp4"
    assert twins(f"subtitled_345_{canonical}", "broll_123_video", 0) == []


def _image(root, suffix="jpg", digest="a" * 20):
    relative = f"broll_assets_123456abcdef/{digest}.{suffix}"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"cached-image")
    return relative, str(path.resolve())


def test_referenced_images_keep_nested_relative_archive_keys(tmp_path):
    jpg = _image(tmp_path)
    png = _image(tmp_path, "png", "b" * 20)
    webp = _image(tmp_path, "webp", "c" * 20)
    clip = {"broll": {"cues": [{"asset": item[0]} for item in (jpg, png, webp)]}}
    assert assets_to_archive(str(tmp_path), clip) == [jpg, png, webp]


def test_manual_image_edit_keeps_clean_base_and_captioned_preview(tmp_path):
    for name in ("ai_123_video_clip_1.mp4", "subtitled_234_video_clip_1.mp4"):
        (tmp_path / name).write_bytes(b"saved edit base")
    clip = {"broll": {"mode": "manual", "base_file": "ai_123_video_clip_1.mp4",
                      "base_preview_file": "subtitled_234_video_clip_1.mp4"}}
    assert [name for name, _ in manual_dependencies(str(tmp_path), clip, "video", 0)] == [
        "ai_123_video_clip_1.mp4", "subtitled_234_video_clip_1.mp4"]


def test_manual_image_edit_base_cannot_archive_outside_files(tmp_path):
    clip = {"broll": {"mode": "manual", "base_file": "../secret.mp4",
                      "base_preview_file": "/private/secret.mp4"}}
    assert manual_dependencies(str(tmp_path), clip, "video", 0) == []


def test_declared_stock_video_keeps_its_nested_archive_key(tmp_path):
    video = _image(tmp_path, "mp4")
    clip = {"broll": {"cues": [{"asset": video[0], "media_type": "video"}]}}
    assert assets_to_archive(str(tmp_path), clip) == [video]


@pytest.mark.parametrize("media_type", [None, "image", "Video", "other"])
def test_mp4_assets_require_an_explicit_video_media_type(tmp_path, media_type):
    video = _image(tmp_path, "mp4")
    clip = {"broll": {"cues": [{"asset": video[0], "media_type": media_type}]}}
    assert assets_to_archive(str(tmp_path), clip) == []


def test_video_media_type_cannot_disguise_an_image_asset(tmp_path):
    image = _image(tmp_path)
    clip = {"broll": {"cues": [{"asset": image[0], "media_type": "video"}]}}
    assert assets_to_archive(str(tmp_path), clip) == []


def test_sound_effect_assets_survive_a_trim_and_archive(tmp_path):
    audio = _image(tmp_path, "m4a")
    clip = {"broll": {"mode": "manual", "source_cues": [
        {"asset": audio[0], "media_type": "audio"}], "cues": []}}
    assert assets_to_archive(str(tmp_path), clip) == [audio]


@pytest.mark.parametrize("media_type", [None, "image", "video", "Audio", "other"])
def test_audio_assets_require_an_explicit_audio_media_type(tmp_path, media_type):
    audio = _image(tmp_path, "m4a")
    clip = {"broll": {"cues": [{"asset": audio[0], "media_type": media_type}]}}
    assert assets_to_archive(str(tmp_path), clip) == []


@pytest.mark.parametrize("suffix", ["jpg", "png", "mp4"])
def test_audio_media_type_cannot_disguise_other_assets(tmp_path, suffix):
    asset = _image(tmp_path, suffix)
    clip = {"broll": {"cues": [{"asset": asset[0], "media_type": "audio"}]}}
    assert assets_to_archive(str(tmp_path), clip) == []


def test_sound_effect_archive_rejects_symlink_and_escaping_assets(tmp_path):
    audio = _image(tmp_path, "m4a")
    for asset in ("../" + audio[0], "/" + audio[0], audio[1]):
        clip = {"broll": {"cues": [{"asset": asset, "media_type": "audio"}]}}
        assert assets_to_archive(str(tmp_path), clip) == []
    protected = tmp_path / "outside.m4a"
    protected.write_bytes(b"unrelated")
    Path(audio[1]).unlink()
    Path(audio[1]).symlink_to(protected)
    clip = {"broll": {"cues": [{"asset": audio[0], "media_type": "audio"}]}}
    assert assets_to_archive(str(tmp_path), clip) == []


def test_source_cues_retain_stock_video_removed_by_a_trim(tmp_path):
    image, video = _image(tmp_path), _image(tmp_path, "mp4", "b" * 20)
    clip = {"broll": {
        "source_cues": [{"asset": image[0]}, {"asset": video[0], "media_type": "video"}],
        "cues": [{"asset": image[0]}],
    }}
    assert assets_to_archive(str(tmp_path), clip) == [image, video]


def test_video_assets_cannot_escape_the_job_or_follow_symlinks(tmp_path):
    video = _image(tmp_path, "mp4")
    relative, path = video
    for asset in ("../" + relative, "/" + relative, path,
                  relative.replace("/", "/../"),
                  "broll_assets_123456abcdef/nested/" + "a" * 20 + ".mp4"):
        clip = {"broll": {"cues": [{"asset": asset, "media_type": "video"}]}}
        assert assets_to_archive(str(tmp_path), clip) == []
    protected = tmp_path / "outside.mp4"
    protected.write_bytes(b"unrelated")
    Path(path).unlink()
    Path(path).symlink_to(protected)
    clip = {"broll": {"cues": [{"asset": relative, "media_type": "video"}]}}
    assert assets_to_archive(str(tmp_path), clip) == []


def test_source_cues_retain_the_images_a_trim_temporarily_removes(tmp_path):
    retained, trimmed = _image(tmp_path), _image(tmp_path, digest="b" * 20)
    clip = {"broll": {
        "source_cues": [{"asset": retained[0]}, {"asset": trimmed[0]}],
        "cues": [{"asset": retained[0]}],
    }}
    assert assets_to_archive(str(tmp_path), clip) == [retained, trimmed]


def test_repeated_cues_archive_an_image_once(tmp_path):
    image = _image(tmp_path)
    clip = {"broll": {"cues": [{"asset": image[0]}, {"asset": image[0]}]}}
    assert assets_to_archive(str(tmp_path), clip) == [image]


def test_missing_and_empty_files_are_not_archived(tmp_path):
    image = _image(tmp_path)
    Path(image[1]).write_bytes(b"")
    missing = "broll_assets_123456abcdef/" + "b" * 20 + ".jpg"
    clip = {"broll": {"cues": [{"asset": image[0]}, {"asset": missing}]}}
    assert assets_to_archive(str(tmp_path), clip) == []


def test_traversal_absolute_paths_and_unrelated_files_are_not_archived(tmp_path):
    image = _image(tmp_path)
    relative = image[0]
    invalid = [
        "../" + relative, "/" + relative, image[1],
        relative.replace("/", "/../"),
        "other_cache/" + "a" * 20 + ".jpg",
        relative.replace(".jpg", ".mp4"),
        relative.replace(".jpg", ".json"),
        "broll_assets_123456abcdef/secret.jpg",
        "broll_assets_123456abcdef/nested/" + "a" * 20 + ".jpg",
    ]
    clip = {"broll": {"cues": [{"asset": item} for item in invalid]}}
    assert assets_to_archive(str(tmp_path), clip) == []


def test_symlinked_image_is_not_archived(tmp_path):
    image = _image(tmp_path)
    target = tmp_path / "outside.jpg"
    target.write_bytes(b"unrelated")
    Path(image[1]).unlink()
    Path(image[1]).symlink_to(target)
    assert assets_to_archive(str(tmp_path), {"broll": {"cues": [{"asset": image[0]}]}}) == []


def test_symlinked_cache_directory_is_not_archived(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ("a" * 20 + ".jpg")).write_bytes(b"unrelated")
    cache = tmp_path / "broll_assets_123456abcdef"
    cache.symlink_to(outside, target_is_directory=True)
    relative = cache.name + "/" + "a" * 20 + ".jpg"
    assert assets_to_archive(str(tmp_path), {"broll": {"cues": [{"asset": relative}]}}) == []


def test_malformed_metadata_has_no_assets(tmp_path):
    for clip in (None, [], {}, {"broll": "bad"}, {"broll": {"cues": "bad"}},
                 {"broll": {"cues": [None, 123, {}, {"asset": None}]}}):
        assert assets_to_archive(str(tmp_path), clip) == []


@pytest.mark.parametrize("suffix,media_type,content_type", [
    ("jpg", "image", "image/jpeg"),
    ("mp4", "video", "video/mp4"),
    ("m4a", "audio", "audio/mp4"),
])
def test_archive_job_uploads_shared_assets_once_under_nested_keys(
        tmp_path, suffix, media_type, content_type):
    image = _image(tmp_path, suffix)
    (tmp_path / BROLL).write_bytes(b"delivered-video")
    clip = {"video_url": "/videos/job/" + BROLL,
            "broll": {"cues": [{"asset": image[0], "media_type": media_type}]}}
    uploaded = []

    class ReachedDatabase(Exception):
        pass

    def database_session():
        # Exercise the complete upload half, stopping before database writes.
        raise ReachedDatabase()

    def upload(path, key, content_type="video/mp4"):
        uploaded.append((path, key, content_type))

    namespace = _archive_helpers()
    namespace.update(
        settings=SimpleNamespace(r2_configured=True),
        database=SimpleNamespace(session=database_session),
        storage=SimpleNamespace(
            upload_file=upload,
            job_key=lambda user, job, name: f"users/{user}/{job}/{name}"))
    try:
        asyncio.run(namespace["archive_job"]("user", "job", [clip, dict(clip)], str(tmp_path)))
    except ReachedDatabase:
        pass
    else:
        raise AssertionError("The upload stage did not reach the database boundary")
    asset_uploads = [entry for entry in uploaded if entry[0] == image[1]]
    assert asset_uploads == [(image[1], "users/user/job/" + image[0], content_type)]


def test_archive_uses_original_index_for_compacted_result_canonical(tmp_path):
    (tmp_path / 'talk_metadata.json').write_text('{}')
    (tmp_path / 'edited.mp4').write_bytes(b'derivative')
    (tmp_path / 'talk_clip_3.mp4').write_bytes(b'correct-clean-original')
    (tmp_path / 'talk_clip_1.mp4').write_bytes(b'wrong-first-clip')
    uploads = []

    class ReachedDatabase(Exception):
        pass

    def session():
        raise ReachedDatabase()

    namespace = _archive_helpers()
    namespace.update(settings=SimpleNamespace(r2_configured=True),
                     database=SimpleNamespace(session=session),
                     storage=SimpleNamespace(
                         upload_file=lambda path, key, content_type='video/mp4': uploads.append(Path(path).name),
                         job_key=lambda user, job, name: name))
    with pytest.raises(ReachedDatabase):
        asyncio.run(namespace['archive_job']('user', 'job', [
            {'index': 2, 'video_url': '/videos/job/edited.mp4'}], str(tmp_path)))
    assert 'talk_clip_3.mp4' in uploads
    assert 'talk_clip_1.mp4' not in uploads
