"""The local account planner shares the editor's bounded busy-request retry."""
import json
import asyncio

import httpx
import pytest

import ai_clip_edit as ai
import codex_editor
from test_ai_trim import trim_endpoint


@pytest.fixture
def local_account(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8766/v1")
    monkeypatch.setenv("LLM_MODEL", "codex-account")
    monkeypatch.setenv("LLM_API_KEY", "test-only-adapter-key")
    monkeypatch.setenv("CODEX_EDITOR_ENABLED", "1")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(codex_editor, "_healthy", lambda *args, **kwargs: True)
    sleeps = []
    monkeypatch.setattr(codex_editor.time, "sleep", sleeps.append)
    return sleeps


def test_planner_retries_busy_account_worker_instead_of_failing(local_account, monkeypatch):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": ".25"}, json={"error": "worker busy"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "operations": [{"type": "brighten", "start": 0, "end": 2}], "unsupported": []})}}]})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(ai.llm_backend, "_client", lambda **kwargs: httpx.Client(transport=transport, **kwargs))
    result = ai.plan_edit("Brighten this clip", {"duration": 2}, source_available=False)
    assert result.operations[0].type == "brighten"
    assert len(requests) == 2
    assert local_account == [.25]


def test_planner_reports_a_bounded_busy_account_error(local_account, monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": ".25"}))
    monkeypatch.setattr(ai.llm_backend, "_client", lambda **kwargs: httpx.Client(transport=transport, **kwargs))
    with pytest.raises(codex_editor.CodexEditorError, match="busy with another request"):
        ai.plan_edit("Brighten this clip", {"duration": 2}, source_available=False)
    assert len(local_account) == codex_editor.MAX_BUSY_RETRIES


def test_busy_account_error_is_actionable_at_the_ai_endpoint(trim_endpoint, monkeypatch):
    def busy(*args, **kwargs):
        raise codex_editor.CodexEditorError("Codex editor is busy with another request. Try again shortly.")
    monkeypatch.setattr(ai, "plan_edit", busy)
    with pytest.raises(trim_endpoint.app.HTTPException) as caught:
        asyncio.run(trim_endpoint.app.edit_clip_with_ai(trim_endpoint.app.AIEditRequest(
            job_id="trim-job", instructions="Brighten the video"), None))
    assert caught.value.status_code == 503
    assert "busy with another request" in caught.value.detail
