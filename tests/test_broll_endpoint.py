"""Creation no longer supports automatic B-roll; saved renders remain editable."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

app = pytest.importorskip("app")


def request_process(*, payload=None, fields=None, key="test-key"):
    async def run():
        transport = httpx.ASGITransport(app=app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-Gemini-Key": key} if key else {}
            if payload is not None:
                return await client.post("/api/process", json=payload, headers=headers)
            return await client.post(
                "/api/process", data=fields,
                files={"file": ("source.mp4", b"fake-video", "video/mp4")},
                headers=headers)
    return asyncio.run(run())


@pytest.fixture
def creation(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_EDITOR_ENABLED", "0")
    output = tmp_path / "output"
    uploads = tmp_path / "uploads"
    output.mkdir()
    uploads.mkdir()
    source = uploads / "session_source.mp4"
    source.write_bytes(b"fake-video")
    monkeypatch.setattr(app, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(app, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(app, "jobs", {})
    monkeypatch.setattr(app, "thumbnail_sessions", {
        "broll-session": {"user_id": None, "video_path": str(source)},
    })
    monkeypatch.setattr(app, "MIN_SOURCE_SECONDS", 0)
    monkeypatch.setattr(app, "_enqueue_job", lambda *args: None)
    monkeypatch.setattr(app, "_media_duration_seconds", lambda *args: 120)
    monkeypatch.setattr(app.llm_backend, "active", lambda: True)
    monkeypatch.setattr(app, "LOCAL_CAMPAIGNS", SimpleNamespace(
        normalize_name=lambda value: value,
        create=lambda value: {"id": "test-category"},
        assign=lambda *args: None,
    ))

    async def key(request):
        return request.headers.get("X-Gemini-Key")

    async def reserve(*args, **kwargs):
        return None, 2, None, None, None

    monkeypatch.setattr(app, "resolve_gemini", key)
    monkeypatch.setattr(app, "reserve_process_minutes", reserve)
    return output


@pytest.mark.parametrize("legacy_choice", [True, False, "true"])
def test_legacy_creation_choice_does_not_enable_automatic_broll(creation, legacy_choice):
    response = request_process(payload={
        "thumbnail_session_id": "broll-session", "acknowledged": True,
        "category_name": "Tests", "auto_broll": legacy_choice,
    })
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert "auto_broll" not in app._read_manifest(job_id)
    assert "AUTO_BROLL" not in app.jobs[job_id]["env"]


def test_legacy_recovery_manifest_cannot_restore_automatic_broll(creation, monkeypatch):
    monkeypatch.setattr(app, "_job_cancelled", lambda *args: False)
    manifest = {"cmd": ["python", "main.py"], "priority": 2, "auto_broll": True}
    assert app._register_resumed([("recovered-broll", manifest)]) == 1
    assert "AUTO_BROLL" not in app.jobs["recovered-broll"]["env"]


def test_broll_delivery_survives_disk_resolution_and_caption_restyle(tmp_path):
    clean = "example_clip_1.mp4"
    broll = "broll_123_" + clean
    captioned = "subtitled_234_" + broll
    for name in [clean, broll]:
        (tmp_path / name).write_bytes(b"video")
    assert app._canonical_clip_file(str(tmp_path), "example", 0) == broll
    (tmp_path / captioned).write_bytes(b"video")
    assert app._strip_burned_captions(str(tmp_path), captioned) == broll
