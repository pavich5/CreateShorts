"""A trimmed media project keeps a clean, editable base without duplicate sound."""
import asyncio
import json
from pathlib import Path
import subprocess

import httpx
import pytest

import ai_clip_edit as ai
from test_manual_broll_api import make_image, project


def test_trimmed_media_can_reopen_resave_and_remove_all_layers(project):
    app, root, source, metadata, runtime = project
    sound = root / "effect.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=880:duration=3", str(sound)], check=True)

    def audio_hash(path):
        return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path),
            "-map", "0:a:0", "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"])

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            image = (await client.post("/api/clip/job/0/broll/assets", files={
                "file": ("picture.png", make_image("red"), "image/png")})).json()
            audio = (await client.post("/api/clip/job/0/broll/assets", files={
                "file": ("pop.wav", sound.read_bytes(), "audio/wav")})).json()
            response = await client.put("/api/clip/job/0/broll", json={"placements": [
                {"id": "photo", "asset": image["asset"], "start": .5, "end": 3,
                 "animation": "fade", "animation_duration": 2},
                {"id": "sound", "asset": audio["asset"], "start": 1, "end": 2.5, "volume": .5},
            ]})
            assert response.status_code == 200, response.text
            saved = json.loads(metadata.read_text())
            clip = saved["shorts"][0]
            current = root / Path(clip["video_url"]).name
            output = root / "ai_trimmed.mp4"
            info = ai.probe(str(current))
            plan = ai.validate_plan({"operations": [{"type": "trim", "start": 1.5, "end": 3.5}]},
                                    info["duration"], False)
            ai.render_edit(str(current), str(output), plan, info)
            record = ai.prepare_manual_media_trim(clip, output, plan, info)
            assert record["base_file"] != source.name
            assert record["cues"][0]["animation_elapsed"] == 1
            clean_base = root / record["base_file"]
            assert ai.probe(clean_base)["duration"] == pytest.approx(2, abs=.1)
            clip.update(broll=record, video_url="/videos/job/ai_trimmed.mp4", duration_seconds=2)
            metadata.write_text(json.dumps(saved))
            runtime["jobs"]["job"]["result"]["clips"][0].update(clip)
            reopened = (await client.get("/api/clip/job/0/broll")).json()
            assert reopened["duration"] == pytest.approx(2, abs=.1)
            assert [(item["start"], item["end"]) for item in reopened["placements"]] == [(0, 1.5), (0, 1)]
            assert reopened["placements"][1]["source_start"] == .5
            assert reopened["placements"][0]["animation_elapsed"] == 1
            response = await client.put("/api/clip/job/0/broll", json={
                "placements": reopened["placements"], "revision": reopened["revision"]})
            assert response.status_code == 200, response.text
            response = await client.put("/api/clip/job/0/broll", json={"placements": []})
            assert response.status_code == 200, response.text
            cleared = root / Path(response.json()["video_url"]).name
            assert audio_hash(cleared) == audio_hash(clean_base)
            pixel = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(cleared),
                "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
            assert pixel[1] > pixel[0] + 60
    asyncio.run(run())


def test_trim_without_saved_manual_media_does_not_create_a_second_render():
    plan = ai.validate_plan({"operations": [{"type": "trim", "start": 1, "end": 3}]}, 4, False)
    assert ai.prepare_manual_media_trim({}, "/nonexistent/output.mp4", plan, {"duration": 4}) is None
    assert ai.prepare_manual_media_trim({'broll': {'mode': 'manual', 'cues': []}},
                                       '/nonexistent/output.mp4', plan, {'duration': 4}) is None


@pytest.mark.parametrize('previous_image', [False, True])
def test_empty_media_record_cannot_restore_trimmed_footage_on_reopen_or_resave(project, previous_image):
    app, root, source, metadata, runtime = project

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            endpoint = '/api/clip/job/0/broll'
            if previous_image:
                uploaded = await client.post(endpoint + '/assets', files={
                    'file': ('picture.png', make_image('red'), 'image/png')})
                uploaded.raise_for_status()
                placed = await client.put(endpoint, json={'placements': [{
                    'id': 'removed_image', 'asset': uploaded.json()['asset'], 'start': 0, 'end': 2}]})
                placed.raise_for_status()
            cleared = await client.put(endpoint, json={'placements': []})
            cleared.raise_for_status()

            # Each trim must use the saved derivative, including a second trim
            # after reopening/resaving media, rather than the original source.
            for index, (start, end) in enumerate(((1, 3.5), (.5, 1.75))):
                saved = json.loads(metadata.read_text())
                clip = saved['shorts'][0]
                current = root / Path(clip['video_url']).name
                info = ai.probe(current)
                plan = ai.validate_plan({'operations': [{'type': 'trim', 'start': start, 'end': end}]},
                                        info['duration'], False)
                output = root / f'ai_empty_trim_{index}.mp4'
                ai.render_edit(current, output, plan, info)
                record = ai.prepare_manual_media_trim(clip, output, plan, info)
                assert record is not None
                assert record['cues'] == []
                assert record['base_file'] != source.name
                clip.update(broll=record, video_url=f'/videos/job/{output.name}',
                            duration_seconds=end - start)
                metadata.write_text(json.dumps(saved))
                runtime['jobs']['job']['result']['clips'][0].update(clip)
                reopened = await client.get(endpoint)
                reopened.raise_for_status()
                state = reopened.json()
                assert state['placements'] == []
                assert state['duration'] == pytest.approx(end - start, abs=.11)
                resaved = await client.put(endpoint, json={
                    'revision': state['revision'], 'placements': []})
                resaved.raise_for_status()
                resaved_path = root / Path(resaved.json()['video_url']).name
                assert ai.probe(resaved_path)['duration'] == pytest.approx(end - start, abs=.11)

    asyncio.run(run())
