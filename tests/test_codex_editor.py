"""Codex editorial adapter contract without network or live inference."""
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import httpx
import pytest

import codex_editor
import llm_backend

SCHEMA = {"type": "object", "properties": {"hook": {"type": "string"}}, "required": ["hook"]}


@pytest.fixture()
def local(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://host.docker.internal:8766/v1")
    monkeypatch.setenv("LLM_MODEL", "codex-account")
    monkeypatch.setenv("LLM_API_KEY", "private-bridge-token")
    monkeypatch.delenv("CODEX_EDITOR_ENABLED", raising=False)


def serve(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(llm_backend, "_client", lambda **kw: httpx.Client(transport=transport, **kw))


def ready():
    return httpx.Response(200, json={"status": "ready", "backend": "codex-cli", "model": "codex-account"})


def completion(value):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(value)}}]})


def test_health_requires_identified_codex_adapter(local, monkeypatch):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return ready()
    serve(monkeypatch, handler)
    assert codex_editor.available()
    assert seen == ["http://host.docker.internal:8766/health"]


@pytest.mark.parametrize("model,url", [
    ("qwen", "http://localhost:8766/v1"),
    ("codex-account", "https://remote.example/v1"),
    ("codex-account", "http://user:password@localhost:8766/v1"),
    ("codex-account", "http://localhost:8766/v1?secret=hidden"),
    ("codex-account", "file:///tmp/adapter"),
])
def test_unknown_or_nonlocal_adapters_are_rejected_without_http(local, monkeypatch, model, url):
    monkeypatch.setenv("LLM_MODEL", model)
    monkeypatch.setenv("LLM_BASE_URL", url)
    def unexpected(request):
        raise AssertionError("Untrusted endpoint should never be called")
    serve(monkeypatch, unexpected)
    assert not codex_editor.available()
    with pytest.raises(codex_editor.CodexEditorError):
        codex_editor.generate_json("context", SCHEMA)


def test_explicit_optin_still_requires_actual_codex_health(local, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "friendly-alias")
    monkeypatch.setenv("CODEX_EDITOR_ENABLED", "1")
    serve(monkeypatch, lambda request: ready())
    assert codex_editor.available()
    serve(monkeypatch, lambda request: httpx.Response(200, json={"status": "ready", "backend": "ollama"}))
    assert not codex_editor.available()


def test_request_keeps_exact_schema_and_auth_and_has_no_tool_fields(local, monkeypatch):
    captured = []
    def handler(request):
        if request.url.path == "/health":
            return ready()
        captured.append(request)
        return completion({"hook": "The opening matters"})
    serve(monkeypatch, handler)
    result = codex_editor.generate_json("A supplied transcript", SCHEMA)
    assert result == {"hook": "The opening matters"}
    request = captured[0]
    payload = json.loads(request.content)
    assert str(request.url) == "http://host.docker.internal:8766/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer private-bridge-token"
    assert payload["model"] == "codex-account" and payload["stream"] is False
    assert payload["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "tools" not in payload and "functions" not in payload
    assert payload["messages"][-1]["content"] == "A supplied transcript"


def test_busy_request_retries_with_deadline(local, monkeypatch):
    posts, sleeps = [], []
    def handler(request):
        if request.url.path == "/health":
            return ready()
        posts.append(request)
        if len(posts) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return completion({"hook": "Done"})
    serve(monkeypatch, handler)
    monkeypatch.setattr(codex_editor.time, "sleep", sleeps.append)
    assert codex_editor.generate_json("context", SCHEMA, timeout=10) == {"hook": "Done"}
    assert len(posts) == 2 and sleeps == [2.0]


def test_busy_retries_are_bounded(local, monkeypatch):
    posts = []
    def handler(request):
        if request.url.path == "/health":
            return ready()
        posts.append(request)
        return httpx.Response(429, text="private-bridge-token", headers={"Retry-After": "1"})
    serve(monkeypatch, handler)
    monkeypatch.setattr(codex_editor.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(codex_editor, "MAX_BUSY_RETRIES", 2)
    with pytest.raises(codex_editor.CodexEditorError, match="busy"):
        codex_editor.generate_json("context", SCHEMA)
    assert len(posts) == 3


def test_busy_deadline_does_not_sleep_beyond_timeout(local, monkeypatch):
    serve(monkeypatch, lambda request: ready() if request.url.path == "/health" else httpx.Response(429))
    sleeps = []
    monkeypatch.setattr(codex_editor.time, "sleep", sleeps.append)
    with pytest.raises(codex_editor.CodexEditorError, match="timed out"):
        codex_editor.generate_json("context", SCHEMA, timeout=0.01)
    assert sleeps == []


def test_thread_lock_wait_uses_same_request_deadline(local, monkeypatch):
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(codex_editor, "_editor_lock", lock)
    with pytest.raises(codex_editor.CodexEditorError, match="waiting"):
        codex_editor.generate_json("context", SCHEMA, timeout=0.001)
    assert lock.locked()
    lock.release()


def test_parallel_clip_workers_do_not_stampede_single_codex_slot(local, monkeypatch):
    state = {"active": 0, "peak": 0}
    tracking = threading.Lock()
    def handler(request):
        if request.url.path == "/health":
            return ready()
        with tracking:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.01)
        with tracking:
            state["active"] -= 1
        return completion({"hook": "Done"})
    serve(monkeypatch, handler)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: codex_editor.generate_json("context", SCHEMA), range(3)))
    assert results == [{"hook": "Done"}] * 3
    assert state["peak"] == 1


@pytest.mark.parametrize("failure", ["http", "timeout", "status"])
def test_failures_are_sanitized_and_never_fall_back(local, monkeypatch, failure):
    def handler(request):
        if request.url.path == "/health":
            return ready()
        if failure == "http":
            raise httpx.ConnectError("private-bridge-token: supplied private transcript", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-bridge-token", request=request)
        return httpx.Response(502, text="private-bridge-token supplied private transcript")
    serve(monkeypatch, handler)
    with pytest.raises(codex_editor.CodexEditorError) as caught:
        codex_editor.generate_json("supplied private transcript", SCHEMA)
    assert "private-bridge-token" not in str(caught.value)
    assert "private transcript" not in str(caught.value)
    assert "host.docker.internal" not in str(caught.value)


@pytest.mark.parametrize("content", ["[]", "not json", '{} {}', '```json\n{}\n```'])
def test_invalid_or_nonobject_completion_is_rejected(local, monkeypatch, content):
    def handler(request):
        return ready() if request.url.path == "/health" else httpx.Response(200, json={
            "choices": [{"message": {"content": content}}]})
    serve(monkeypatch, handler)
    with pytest.raises(codex_editor.CodexEditorError, match="valid editorial plan"):
        codex_editor.generate_json("context", SCHEMA)


def test_tool_use_response_is_rejected(local, monkeypatch):
    serve(monkeypatch, lambda request: ready() if request.url.path == "/health" else httpx.Response(200, json={
        "choices": [{"message": {"content": "{}", "tool_calls": [{"name": "shell"}]}}]}))
    with pytest.raises(codex_editor.CodexEditorError):
        codex_editor.generate_json("context", SCHEMA)


def test_no_health_connection_is_reported_unavailable(local, monkeypatch):
    def fail(request):
        raise httpx.ConnectError("offline", request=request)
    serve(monkeypatch, fail)
    assert not codex_editor.available()


@pytest.fixture
def frame_path(tmp_path):
    image = pytest.importorskip("PIL.Image")
    path = tmp_path / "explicit-frame.jpg"
    image.new("RGB", (320, 180), "blue").save(path)
    return path


def test_vision_request_sends_inline_bytes_and_keeps_paths_private(local, monkeypatch, frame_path):
    import base64
    captured = []
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ready", "backend": "codex-cli", "visionSupported": True})
        captured.append(json.loads(request.content))
        return completion({"hook": "The car is visible"})
    serve(monkeypatch, handler)
    assert codex_editor.vision_available()
    result = codex_editor.generate_vision_json("Judge this explicit frame", SCHEMA, [frame_path])
    assert result == {"hook": "The car is visible"}
    payload = captured[0]
    content = payload["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "Judge this explicit frame"}
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == frame_path.read_bytes()
    assert str(frame_path) not in json.dumps(payload)
    assert "tools" not in payload


def test_vision_refuses_old_bridge_instead_of_ignoring_images(local, monkeypatch, frame_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return ready()
    serve(monkeypatch, handler)
    assert codex_editor.available() and not codex_editor.vision_available()
    with pytest.raises(codex_editor.CodexEditorError, match="frame-analysis update"):
        codex_editor.generate_vision_json("Find car", SCHEMA, [frame_path])
    assert all(path == "/health" for path in calls)


@pytest.mark.parametrize("images", [[], ["missing.jpg"] * 7, ["https://example.test/frame.jpg"]])
def test_bad_vision_input_fails_before_any_http(local, monkeypatch, images):
    def unexpected(request):
        raise AssertionError("Invalid input cannot reach the bridge")
    serve(monkeypatch, unexpected)
    with pytest.raises(codex_editor.CodexEditorError):
        codex_editor.generate_vision_json("Find car", SCHEMA, images)


def test_corrupt_symlink_and_oversized_images_are_rejected(local, monkeypatch, frame_path, tmp_path):
    image = pytest.importorskip("PIL.Image")
    invalid = tmp_path / "bad.jpg"
    invalid.write_bytes(b"not an image")
    symlink = tmp_path / "linked.jpg"
    symlink.symlink_to(frame_path)
    oversized = tmp_path / "too-wide.png"
    image.new("RGB", (4097, 1)).save(oversized)
    serve(monkeypatch, lambda request: (_ for _ in ()).throw(AssertionError("No HTTP expected")))
    for path in (invalid, symlink, oversized):
        with pytest.raises(codex_editor.CodexEditorError, match="valid JPEG or PNG"):
            codex_editor.generate_vision_json("Find car", SCHEMA, [path])


def test_combined_image_bytes_are_bounded(local, monkeypatch, frame_path):
    monkeypatch.setattr(codex_editor, "MAX_IMAGE_BYTES", frame_path.stat().st_size + 4)
    serve(monkeypatch, lambda request: (_ for _ in ()).throw(AssertionError("No HTTP expected")))
    with pytest.raises(codex_editor.CodexEditorError):
        codex_editor.generate_vision_json("Find car", SCHEMA, [frame_path, frame_path])
