import asyncio
import io
import json
from pathlib import Path
import shutil
import subprocess

from fastapi import FastAPI, HTTPException
import httpx
import pytest

import broll
from manual_broll_routes import install_manual_broll_routes


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")


def make_image(color):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), color).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    output = tmp_path / "output"
    root = output / "job"
    root.mkdir(parents=True)
    source = root / "talk_clip_1.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=green:s=120x180:r=10:d=4", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(source)], check=True)
    clip = {"start": 100, "end": 104, "video_url": "/videos/job/talk_clip_1.mp4"}
    metadata = root / "talk_metadata.json"
    metadata.write_text(json.dumps({"shorts": [clip], "transcript": {"segments": []}}))
    async def check_owner(request, record):
        if request.headers.get("X-Wrong-Owner"):
            raise HTTPException(404, "Job not found")
    async def no_op(*args, **kwargs):
        pass
    async def deliver(request, job_id, name):
        return name
    runtime = {"OUTPUT_DIR": str(output), "jobs": {"job": {"result": {"clips": [dict(clip)]}}},
               "_rerender_locks": {}, "require_managed_entitlement": no_op,
               "_ensure_job_files": no_op, "_assert_job_owner": check_owner,
               "_strip_watermark_copy": lambda root, name: name,
               "_strip_burned_captions": lambda root, name, materialize=False: name,
               "_strip_burned_hook": lambda root, name: name,
               "_clip_recipe_parts": lambda clip: ([{"start": 100, "end": 104}], {}),
               "_signed_media_url": lambda url: url, "_deliver": deliver,
               "write_json_atomic": lambda path, data, **kwargs: Path(path).write_text(json.dumps(data)),
               "_archive_clip_edit_bg": lambda *args: None}
    app = FastAPI()
    install_manual_broll_routes(app, runtime)
    return app, root, source, metadata, runtime


def test_media_save_updates_compacted_runtime_by_original_clip_index(project):
    app, root, source, metadata, runtime = project
    data = json.loads(metadata.read_text())
    clip = data['shorts'][0]
    clip['index'] = 1
    tombstone = {'index': 0, 'deleted': True, 'video_url': None}
    data['shorts'] = [tombstone, clip]
    metadata.write_text(json.dumps(data))
    memory_clip = dict(clip)
    runtime['jobs']['job']['result']['clips'] = [memory_clip]

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            saved = await client.put('/api/clip/job/1/broll', json={'placements': []})
            assert saved.status_code == 200, saved.text
            assert memory_clip['video_url'] == saved.json()['new_video_url']
            assert memory_clip['broll']['mode'] == 'manual'
            assert json.loads(metadata.read_text())['shorts'][0] == tombstone
    asyncio.run(run())


def test_upload_apply_replace_reopen_and_remove_without_losing_audio(project):
    app, root, source, metadata, runtime = project
    original = source.read_bytes()
    def audio_hash(path):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                               "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"],
                              capture_output=True, check=True).stdout
    def frame(path, at):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
                              "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        return tuple(raw[:3])
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            state = (await client.get("/api/clip/job/0/broll")).json()
            assert state["placements"] == [] and state["duration"] == pytest.approx(4, abs=.15)
            assert state["base_video_url"] == "/videos/job/talk_clip_1.mp4"
            assets = []
            for color in ("red", "blue"):
                uploaded = await client.post("/api/clip/job/0/broll/assets", files={"file": ("../test.png", make_image(color), "image/png")})
                assert uploaded.status_code == 200, uploaded.text
                assets.append(uploaded.json()["asset"])
            placed = await client.put("/api/clip/job/0/broll", json={"placements": [{"asset": assets[0], "start": 0, "end": 1}]})
            assert placed.status_code == 200, placed.text
            first = placed.json()
            first_path = root / first["video_url"].rsplit("/", 1)[1]
            assert frame(first_path, .5)[0] > 180
            assert audio_hash(source) == audio_hash(first_path)
            reopened = (await client.get("/api/clip/job/0/broll")).json()
            assert len(reopened["placements"]) == 1
            assert reopened["base_video_url"] == "/videos/job/talk_clip_1.mp4"
            replaced = await client.put("/api/clip/job/0/broll", json={"placements": [{"asset": assets[1], "start": 2, "end": 4}]})
            assert replaced.status_code == 200, replaced.text
            second_path = root / replaced.json()["video_url"].rsplit("/", 1)[1]
            red, green, blue = frame(second_path, .5)
            assert green > red + 60 and green > blue + 60
            assert frame(second_path, 3.5)[2] > 180
            assert audio_hash(source) == audio_hash(second_path)
            cleared = await client.put("/api/clip/job/0/broll", json={"placements": []})
            assert cleared.status_code == 200, cleared.text
            clean_path = root / cleared.json()["video_url"].rsplit("/", 1)[1]
            assert frame(clean_path, 3.5)[1] > 100
            assert audio_hash(source) == audio_hash(clean_path)
            assert (await client.get("/api/clip/job/0/broll")).json()["placements"] == []
            assert json.loads(metadata.read_text())["shorts"][0]["broll"]["mode"] == "manual"
            assert runtime["jobs"]["job"]["result"]["clips"][0]["video_url"] == cleared.json()["video_url"]
    asyncio.run(run())
    assert source.read_bytes() == original


def test_owner_bounds_and_asset_isolation_are_enforced(project):
    app, root, source, _, _ = project
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/clip/job/0/broll", headers={"X-Wrong-Owner": "1"})).status_code == 404
            assert (await client.post("/api/clip/job/0/broll/assets", headers={"X-Wrong-Owner": "1"}, files={"file": ("x.png", make_image("red"))})).status_code == 404
            assert (await client.get("/api/clip/job/-1/broll")).status_code == 404
            assert (await client.get("/api/clip/job/4/broll")).status_code == 404
            bad = await client.put("/api/clip/job/0/broll", json={"placements": [{"asset": "../outside.png", "start": 0, "end": 1}]})
            assert bad.status_code == 400
            assert (await client.post("/api/clip/job/0/broll/assets", files={"file": ("x.svg", b"<svg></svg>", "image/svg+xml")})).status_code == 400
    asyncio.run(run())
    assert source.is_file() and not list(root.glob("broll_*.mp4"))


def test_stale_editor_cannot_overwrite_a_later_clip_edit(project):
    app, _, _, metadata, _ = project
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            state = (await client.get("/api/clip/job/0/broll")).json()
            assert state["width"] == 120 and state["height"] == 180
            data = json.loads(metadata.read_text())
            data["shorts"][0]["auto_hook"] = {"text": "Later edit"}
            metadata.write_text(json.dumps(data))
            response = await client.put("/api/clip/job/0/broll", json={"placements": [], "revision": state["revision"]})
            assert response.status_code == 409
            assert json.loads(metadata.read_text())["shorts"][0]["auto_hook"]["text"] == "Later edit"
    asyncio.run(run())


def test_failed_render_keeps_previous_clip_and_metadata(project, monkeypatch):
    app, root, source, metadata, runtime = project
    original_metadata = metadata.read_bytes()
    def fail(*args, **kwargs):
        Path(args[1]).write_bytes(b"unfinished-video")
        raise RuntimeError("render failed")
    monkeypatch.setattr(broll, "render_cutaways", fail)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            upload = await client.post("/api/clip/job/0/broll/assets", files={"file": ("image.png", make_image("red"))})
            response = await client.put("/api/clip/job/0/broll", json={"placements": [{"asset": upload.json()["asset"], "start": 0, "end": 1}]})
            assert response.status_code == 500
    asyncio.run(run())
    assert metadata.read_bytes() == original_metadata
    assert runtime["jobs"]["job"]["result"]["clips"][0]["video_url"] == "/videos/job/talk_clip_1.mp4"
    assert not list(root.glob("broll_*.mp4")) and source.is_file()


def test_uploaded_video_and_sound_can_overlap_reopen_and_be_removed(project):
    app, root, source, _, _ = project
    video, sound = root / "incoming.mov", root / "pop.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=120x180:r=20:d=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=880:duration=3", str(sound)], check=True)

    def audio_hash(path):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                               "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"],
                              capture_output=True, check=True).stdout

    def pixel(path, at):
        return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
                               "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24",
                               "-f", "rawvideo", "-"], capture_output=True, check=True).stdout[:3]

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            endpoint = "/api/clip/job/0/broll"
            state = (await client.get(endpoint)).json()
            video_response = await client.post(endpoint + "/assets", files={
                "file": ("insert.mov", video.read_bytes(), "video/quicktime")})
            sound_response = await client.post(endpoint + "/assets", files={
                "file": ("pop.wav", sound.read_bytes(), "audio/wav")})
            assert video_response.status_code == 200, video_response.text
            assert sound_response.status_code == 200, sound_response.text
            video_data, sound_data = video_response.json(), sound_response.json()
            assert video_data["kind"] == "video" and video_data["media_url"].endswith(".mp4")
            assert sound_data["kind"] == "audio" and sound_data["media_url"].endswith(".m4a")
            placements = [
                {"id": "video", "asset": video_data["asset"], "kind": "video", "start": 1,
                 "end": 2.5, "source_start": .25, "animation": "fade", "animation_duration": .4,
                 "fit": "stretch", "background": "none", "volume": 0},
                {"id": "sound", "asset": sound_data["asset"], "kind": "audio", "start": 1,
                 "end": 2.5, "source_start": .25, "volume": .7},
            ]
            applied = await client.put(endpoint, json={"revision": state["revision"], "placements": placements})
            assert applied.status_code == 200, applied.text
            output = root / applied.json()["video_url"].rsplit("/", 1)[1]
            assert pixel(output, .2)[1] > 100
            assert pixel(output, 1.8)[0] > 180
            reopened = (await client.get(endpoint)).json()
            by_id = {item["id"]: item for item in reopened["placements"]}
            assert set(by_id) == {"video", "sound"}
            assert by_id["video"]["animation"] == "fade"
            assert by_id["video"]["source_start"] == .25
            assert by_id["sound"]["source_start"] == .25
            assert by_id["sound"]["volume"] == .7
            assert by_id["sound"]["media_url"].endswith(".m4a")
            base = root / reopened["base_video_url"].rsplit("/", 1)[1]
            assert audio_hash(base) == audio_hash(source)
            cleared = await client.put(endpoint, json={"revision": reopened["revision"], "placements": []})
            assert cleared.status_code == 200, cleared.text
            restored = root / cleared.json()["video_url"].rsplit("/", 1)[1]
            assert pixel(restored, 1.8)[1] > 100
            assert audio_hash(restored) == audio_hash(source)
            assert (await client.get(endpoint)).json()["placements"] == []

    asyncio.run(run())


def test_live_media_preview_does_not_encode_captions_or_hooks(project):
    app, root, source, metadata, runtime = project
    hooked = root / ('hooked_1_' + source.name)
    captioned = root / ('subtitled_1_' + hooked.name)
    shutil.copyfile(source, hooked)
    shutil.copyfile(source, captioned)
    data = json.loads(metadata.read_text())
    hook = {'text': 'Opening', 'duration_seconds': 2, 'style': 'pill'}
    data['shorts'][0].update(video_url='/videos/job/' + captioned.name, auto_hook=hook,
        caption_style={'font_name': 'Arial'}, broll={'mode': 'manual', 'base_file': source.name,
        'file': source.name, 'cues': [], 'post_effects': [[{'type': 'mute', 'start': 1, 'end': 2}]]})
    data['transcript'] = {'segments': [{'words': [{'word': 'hello', 'start': 100, 'end': 101}]}]}
    metadata.write_text(json.dumps(data))
    runtime['_strip_burned_captions'] = lambda root, name, **kwargs: name.removeprefix('subtitled_1_')
    runtime['_strip_burned_hook'] = lambda root, name: name.removeprefix('hooked_1_')
    runtime['_restyle_captions'] = lambda *args: pytest.fail('Opening Media encoded captions')
    before = set(root.iterdir())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for _ in range(2):
                response = await client.get('/api/clip/job/0/broll?live_preview=true')
                assert response.status_code == 200, response.text
                state = response.json()
                assert state['base_video_url'] == '/videos/job/' + source.name
                assert state['caption_free_video_url'] == state['base_video_url']
                assert state['preview_hook'] == hook
                assert state['captions'] == [{'text': 'hello', 'startMs': 0, 'endMs': 1000, 'utteranceId': 0}]
                assert state['post_effects']
    asyncio.run(run())
    assert set(root.iterdir()) == before
