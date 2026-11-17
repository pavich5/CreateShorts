"""Real local encodes keep appended footage after editable media is resaved.

ASGI calls exercise actual upload/save endpoints; the AI planner is bypassed
with an explicit validated operation, so this is not an external AI test.
"""
import asyncio
from array import array
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

import ai_clip_edit as ai
import ai_source_edit as ase
import source_edit
from test_manual_broll_api import make_image, project
from test_ai_source_endpoint import endpoint, apply


def test_append_with_manual_media_reaches_the_preserving_source_renderer(endpoint):
    # Route/dispatch regression only: this endpoint fixture stubs rendering.
    # Actual layer renders are exercised separately below.
    endpoint.clip["broll"] = {"mode": "manual", "base_file": "clip.mp4",
                              "file": "clip.mp4", "cues": [{"id": "image"}]}
    result = apply(endpoint)
    assert result["success"]
    assert endpoint.rendered[0]["current_clip"]["broll"]["cues"] == [{"id": "image"}]


def test_ai_route_captions_the_clean_extended_base_before_lower_caption_layers(endpoint, monkeypatch):
    # Dispatch and persistence test; actual caption/media/hook pixels are
    # covered by test_real_caption_below_media_keeps_hook_and_editable_layer_twins.
    media = {"mode": "manual", "base_file": "clip.mp4", "file": "clip.mp4",
             "captions_above_media": False, "cues": [{"id": "image"}]}
    endpoint.clip["broll"] = media
    module = sys.modules["ai_source_edit"]
    original_render = module.render_source_operation
    composed = []
    def render(*args, **kwargs):
        result = original_render(*args, **kwargs)
        result["clip_updates"]["broll"] = media
        return result
    def compose(captioned, current, record, hook):
        composed.append((captioned, current, record, hook))
        output = endpoint.directory / ("subtitled_88_" + Path(current).name)
        output.write_bytes(b"captions-behind-media")
        return str(output)
    monkeypatch.setattr(module, "render_source_operation", render)
    monkeypatch.setattr(module, "compose_captions_below_media", compose, raising=False)
    result = apply(endpoint, input_filename=endpoint.captioned.name)
    assert result["success"]
    assert endpoint.captions[-1]["path"] == str(endpoint.original)
    assert len(composed) == 1 and composed[0][2]["captions_above_media"] is False
    assert result["broll"]["captions_above_media"] is False


def frame(path, at):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
        "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])


def amplitude(path, at):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
        "-t", "0.2", "-vn", "-ac", "1", "-f", "f32le", "-"])
    samples = array("f")
    samples.frombytes(raw)
    return max(abs(value) for value in samples)


@pytest.mark.parametrize("with_hook", [False, True])
def test_real_caption_below_media_keeps_hook_and_editable_layer_twins(tmp_path, with_hook, monkeypatch):
    import app
    import broll
    import hooks
    import manual_broll
    from PIL import Image

    monkeypatch.setenv("FFMPEG_ENCODER", "x264")
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=c=green:s=90x160:r=10:d=2", "-c:v", "libx264", str(base)], check=True)
    transcript = {"segments": [{"words": [{"word": "VISIBLE", "start": .2, "end": 1.8}]}]}
    captioned = app._restyle_captions(str(base), transcript, 0, 2,
        {"style": "classic", "font_name": "Arial", "font_size": 24, "position": "middle"})
    assert captioned and Path(captioned).is_file()
    assert frame(captioned, .5) != frame(base, .5)
    saved = manual_broll.save_media(tmp_path, 0, make_image("blue"), "blue.png", "image/png")
    raw = [{"id": "cover", "asset": saved["asset"], "start": 0, "end": 2,
            "fit": "stretch", "background": "none"}]
    placements = manual_broll.validate_placements(raw, 2, tmp_path)
    uncaptioned = tmp_path / "ai_extended.mp4"
    broll.render_cutaways(str(base), str(uncaptioned), placements)
    current, hook = uncaptioned, None
    if with_hook:
        image = tmp_path / "hook.png"
        Image.new("RGBA", (20, 20), "red").save(image)
        current = tmp_path / ("hooked_1_" + uncaptioned.name)
        result = hooks._run_encode(hooks._overlay_cmd(str(uncaptioned), str(image),
            hooks._overlay_graph(0, 0, 1), str(current)), str(uncaptioned), [str(current)])
        assert result.returncode == 0
        hooks._write_recipe(str(current), str(uncaptioned), str(image), 0, 0, 1)
        hook = {"image": str(image), "x": 0, "y": 0, "duration": 1}
    output = ase.compose_captions_below_media(captioned, current, {"cues": raw}, hook)
    pixel = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", output,
        "-frames:v", "1", "-vf", "crop=2:2:40:80,scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    assert pixel[2] > 180 and pixel[0] < 40
    if with_hook:
        top = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", output,
            "-frames:v", "1", "-vf", "crop=2:2:5:5,scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        assert top[0] > 180 and top[2] < 40
    stripped = app._strip_burned_captions(str(tmp_path), Path(output).name, materialize=False)
    assert stripped == current.name
    assert app._strip_burned_hook(str(tmp_path), stripped) == uncaptioned.name


def test_extension_with_image_sound_and_effects_survives_resave_and_removal(project):
    app, root, base, metadata, runtime = project
    original_source = root / "original.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=c=green:s=90x160:r=10:d=16", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=16", "-vf", "drawbox=color=red:t=fill:enable='gte(t,4)'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(original_source)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(original_source), "-t", "4",
        "-c:v", "libx264", "-c:a", "aac", str(base)], check=True)
    sound = root / "sound.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=880:duration=2", str(sound)], check=True)
    saved = json.loads(metadata.read_text())
    saved["shorts"][0].update(start=0, end=4, recipe={"segments": [{"start": 0, "end": 4}]})
    metadata.write_text(json.dumps(saved))
    runtime["jobs"]["job"]["result"]["clips"][0].update(saved["shorts"][0])
    runtime["_clip_recipe_parts"] = lambda clip: ((clip.get("recipe") or {}).get("segments"), {})
    transcript = {"segments": [{"words": [
        {"word": " more", "start": index + .2, "end": index + .6}
        for index in range(4, 16)]}]}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            endpoint = "/api/clip/job/0/broll"
            image = (await client.post(endpoint + "/assets", files={
                "file": ("image.png", make_image("blue"), "image/png")})).json()
            audio = (await client.post(endpoint + "/assets", files={
                "file": ("sound.wav", sound.read_bytes(), "audio/wav")})).json()
            response = await client.put(endpoint, json={"placements": [
                {"id": "image", "asset": image["asset"], "start": .5, "end": 3.5},
                {"id": "sound", "asset": audio["asset"], "start": 1, "end": 2.5, "volume": .5}]})
            assert response.status_code == 200, response.text
            saved = json.loads(metadata.read_text())
            clip = saved["shorts"][0]
            current = root / Path(clip["video_url"]).name
            effected = root / "effected.mp4"
            effects = [{"type": "mute", "start": 1, "end": 2},
                       {"type": "bw_moment", "start": 2, "end": 3}]
            ai.render_edit(current, effected, ai.validate_plan({"operations": effects}, 4, False), ai.probe(current))
            clip["broll"].update(file=effected.name, post_effects=[effects])
            clip["video_url"] = "/videos/job/" + effected.name
            extended = root / "extended.mp4"
            result = ase.render_source_operation(effected, extended, source_path=original_source,
                transcript=transcript, current_segments=clip["recipe"]["segments"], current_clip=clip,
                operation={"type": "source_continue", "start": 4, "end": 5, "query": "Continue story"})
            clip.update(result["clip_updates"], video_url="/videos/job/" + extended.name)
            metadata.write_text(json.dumps(saved))
            runtime["jobs"]["job"]["result"]["clips"][0].update(clip)
            state = (await client.get(endpoint)).json()
            assert state["duration"] == pytest.approx(5, abs=.15)
            assert len(state["placements"]) == 2
            assert state["post_effects"] == [effects]
            response = await client.put(endpoint, json={"placements": state["placements"], "revision": state["revision"]})
            assert response.status_code == 200, response.text
            resaved = root / Path(response.json()["video_url"]).name
            assert source_edit.probe(resaved)["duration"] == pytest.approx(5, abs=.15)
            assert source_edit.probe(resaved)["width"] * 16 == source_edit.probe(resaved)["height"] * 9
            assert amplitude(resaved, 1.3) < .002
            assert max(frame(resaved, 2.5)) - min(frame(resaved, 2.5)) < 6
            assert frame(resaved, 4.5)[0] > 180
            response = await client.put(endpoint, json={"placements": []})
            assert response.status_code == 200, response.text
            cleared = root / Path(response.json()["video_url"]).name
            assert source_edit.probe(cleared)["duration"] == pytest.approx(5, abs=.15)
            assert frame(cleared, 4.5)[0] > 180
            assert amplitude(cleared, 1.3) < .002
            assert amplitude(cleared, 4.5) > .02
            # Continue repeatedly after media removal and reopening. The
            # empty record still owns its clean base and saved effects.
            for index in range(1, 10):
                saved = json.loads(metadata.read_text())
                clip = saved["shorts"][0]
                current = root / Path(clip["video_url"]).name
                extended = root / f"continued_{index}.mp4"
                result = ase.render_source_operation(current, extended, source_path=original_source,
                    transcript=transcript, current_segments=clip["recipe"]["segments"], current_clip=clip,
                    operation={"type": "source_continue", "start": 0, "end": 1, "query": "Continue story"})
                clip.update(result["clip_updates"], video_url="/videos/job/" + extended.name)
                metadata.write_text(json.dumps(saved))
                runtime["jobs"]["job"]["result"]["clips"][0].update(clip)
                state = (await client.get(endpoint)).json()
                expected = 5 + index
                assert state["duration"] == pytest.approx(expected, abs=.15)
                assert len(clip["source_edits"]) == index + 1
                response = await client.put(endpoint, json={"placements": [], "revision": state["revision"]})
                assert response.status_code == 200, response.text
                resaved = root / Path(response.json()["video_url"]).name
                assert source_edit.probe(resaved)["duration"] == pytest.approx(expected, abs=.15)
                assert amplitude(resaved, 1.3) < .002
                assert amplitude(resaved, expected - .5) > .02
    asyncio.run(run())
