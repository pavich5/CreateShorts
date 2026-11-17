"""Uploaded display names survive validation, saves and subsequent reopening."""
import asyncio
import json
import unicodedata

import httpx
import pytest

import manual_broll as manual
from test_manual_broll_api import project, make_image


@pytest.mark.parametrize("name,expected", [
    ("Alex extra scene.mp4", "Alex extra scene.mp4"),
    ("  sound\n\x00effect\x7f.wav  ", "soundeffect.wav"),
    ("a" * 300, "a" * 255),
    (None, None),
])
def test_placement_display_names_are_bounded_and_control_free(tmp_path, name, expected):
    uploaded = manual.save_media(tmp_path, 0, make_image("red"), "image.png", "image/png")
    cue = manual.validate_placements([{"asset": uploaded["asset"], "name": name,
        "start": 0, "end": 1}], 2, tmp_path)[0]
    assert cue.get("name") == expected
    reopened = manual.displayed_placements({"mode": "manual", "cues": [cue]})
    assert reopened[0].get("name") == expected
    if expected:
        assert len(expected) <= 255 and not any(unicodedata.category(char) == "Cc" for char in expected)


def test_media_name_survives_real_save_reopen_and_second_save(project):
    app, _, _, metadata, _ = project
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            endpoint = "/api/clip/job/0/broll"
            uploaded = await client.post(endpoint + "/assets", files={
                "file": ("Alex frame.png", make_image("blue"), "image/png")})
            uploaded.raise_for_status()
            response = await client.put(endpoint, json={"placements": [{"id": "frame",
                "asset": uploaded.json()["asset"], "name": "Alex frame.png", "start": 0, "end": 2}]})
            assert response.status_code == 200, response.text
            state = (await client.get(endpoint)).json()
            assert state["placements"][0]["name"] == "Alex frame.png"
            assert json.loads(metadata.read_text())["shorts"][0]["broll"]["cues"][0]["name"] == "Alex frame.png"
            response = await client.put(endpoint, json={"placements": state["placements"], "revision": state["revision"]})
            assert response.status_code == 200, response.text
            assert (await client.get(endpoint)).json()["placements"][0]["name"] == "Alex frame.png"
    asyncio.run(run())
