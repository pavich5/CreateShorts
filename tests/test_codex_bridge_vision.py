"""Bounded inline frames reach Codex as explicit images without enabling tools."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import pytest


def bridge_module():
    path = Path(__file__).resolve().parent.parent / ".local" / "openshorts_codex_bridge.py"
    if not path.is_file():
        pytest.skip("This installation has no local Codex bridge mirror", allow_module_level=True)
    spec = importlib.util.spec_from_file_location("codex_bridge_vision_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = bridge_module()


def png(width=24, height=16):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # The malformed-size cases need only an oversized header; they should be
    # refused before Codex ever receives the image.
    pixels = (b"\x00" + b"\xff\x00\x00" * min(width, 24)) * min(height, 16)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def image_part(data=None, mime="image/png"):
    return {"type": "image_url", "image_url": {
        "url": "data:" + mime + ";base64," + base64.b64encode(data or png()).decode()}}


def body(parts=None):
    return {"messages": [{"role": "user", "content": parts or [
        {"type": "text", "text": "Find the car in these explicit preview frames."}, image_part(),
    ]}], "response_format": {"type": "json_schema", "json_schema": {"schema": {
        "type": "object", "properties": {"found": {"type": "boolean"}},
        "required": ["found"], "additionalProperties": False}}}}


def test_inline_frames_are_removed_from_text_context():
    messages, images = bridge.request_messages(body())
    assert images == [("image/png", png())]
    assert "Explicitly attached preview image 1" in messages[0]["content"]
    assert "base64" not in messages[0]["content"]
    assert bridge.image_dimensions(images[0][1], images[0][0]) == (24, 16)


@pytest.mark.parametrize("url", [
    "https://example.test/private.png", "file:///etc/passwd", "/tmp/frame.png",
    "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,%%%%",
    "data:image/png,not-base64", "data:image/jpeg;base64,AA==",
])
def test_remote_paths_nonraster_and_malformed_inputs_are_rejected(url):
    request = body([{"type": "image_url", "image_url": {"url": url}}])
    with pytest.raises(bridge.RequestError):
        bridge.request_messages(request)


@pytest.mark.parametrize("width,height", [(0, 24), (4097, 20), (4000, 4000)])
def test_oversized_or_invalid_dimensions_are_rejected(width, height):
    with pytest.raises(bridge.RequestError):
        bridge.request_messages(body([image_part(png(width, height))]))


def test_invalid_png_checksum_is_rejected():
    data = bytearray(png())
    data[-5] ^= 0xff
    with pytest.raises(bridge.RequestError):
        bridge.request_messages(body([image_part(bytes(data))]))


def test_frame_count_and_combined_bytes_are_bounded(monkeypatch):
    with pytest.raises(bridge.RequestError, match="combined"):
        bridge.request_messages(body([image_part()] * 7))
    monkeypatch.setattr(bridge, "MAX_IMAGE_BYTES", len(png()) + 4)
    with pytest.raises(bridge.RequestError, match="combined"):
        bridge.request_messages(body([image_part(), image_part()]))


def test_images_cannot_arrive_as_system_instructions_or_tools():
    request = body()
    request["messages"][0]["role"] = "system"
    with pytest.raises(bridge.RequestError):
        bridge.request_messages(request)
    with pytest.raises(bridge.RequestError):
        bridge.request_messages({**body(), "tools": [{"name": "shell"}]})


def test_text_payload_retains_its_two_megabyte_limit(monkeypatch):
    monkeypatch.setattr(bridge, "MAX_TEXT_BODY", 100)
    with pytest.raises(bridge.RequestError, match="Text context"):
        bridge.request_messages(body([{"type": "text", "text": "x" * 101}]))


def test_real_jpeg_headers_are_accepted_and_mime_mismatch_rejected():
    image = pytest.importorskip("PIL.Image")
    data = io.BytesIO()
    image.new("RGB", (160, 90), "blue").save(data, format="JPEG")
    assert bridge.image_dimensions(data.getvalue(), "image/jpeg") == (160, 90)
    with pytest.raises(bridge.RequestError):
        bridge.image_dimensions(data.getvalue(), "image/png")


def test_bridge_process_passes_private_readonly_images_to_cli(tmp_path, monkeypatch):
    worker = bridge.Bridge("test-private-token", Path("/fake/codex"), tmp_path / "worker")
    monkeypatch.setattr(bridge, "require_chatgpt_account", lambda _: None)
    calls = []

    def run(command, prompt, cwd, timeout):
        calls.append(command)
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in command and "--ignore-user-config" in command
        assert "--disable" in command and "shell_tool" in command
        image_path = Path(command[command.index("--image") + 1])
        assert image_path.parent == cwd and image_path.read_bytes() == png()
        assert image_path.stat().st_mode & 0o777 == 0o400
        assert cwd.stat().st_mode & 0o777 == 0o500
        assert "base64" not in prompt and "Explicitly attached preview image 1" in prompt
        Path(command[command.index("--output-last-message") + 1]).write_text('{"found": true}')
        return 0, '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":2}}', ""

    monkeypatch.setattr(bridge, "run_process", run)
    result = worker.process(body())
    assert json.loads(result["choices"][0]["message"]["content"]) == {"found": True}
    assert len(calls) == 1
    assert not Path(calls[0][calls[0].index("--image") + 1]).exists()
    assert not list((tmp_path / "worker").glob("call-*"))


def test_worker_cannot_use_tools_even_with_explicit_image_inputs(tmp_path, monkeypatch):
    worker = bridge.Bridge("test-private-token", Path("/fake/codex"), tmp_path / "worker")
    monkeypatch.setattr(bridge, "require_chatgpt_account", lambda _: None)
    def run(command, prompt, cwd, timeout):
        Path(command[command.index("--output-last-message") + 1]).write_text('{"found": true}')
        return 0, '{"type":"item.completed","item":{"type":"command_execution"}}', ""
    monkeypatch.setattr(bridge, "run_process", run)
    with pytest.raises(bridge.WorkerError):
        worker.process(body())


def test_existing_text_request_adds_no_image_arguments(tmp_path, monkeypatch):
    worker = bridge.Bridge("test-private-token", Path("/fake/codex"), tmp_path / "worker")
    command = worker.command(tmp_path, tmp_path / "schema.json", tmp_path / "result.json")
    assert "--image" not in command
    assert bridge.text_messages({"messages": [{"role": "user", "content": "Plain context"}]}) == [
        {"role": "user", "content": "Plain context"}]


def handler_for(payload, *, token="Bearer private-token"):
    encoded = json.dumps(payload).encode()
    handler = object.__new__(bridge.Handler)
    handler.path = "/v1/chat/completions"
    handler.headers = {"Authorization": token, "Content-Length": str(len(encoded)),
                       "Content-Type": "application/json"}
    handler.rfile = io.BytesIO(encoded)
    handler.connection = SimpleNamespace(settimeout=lambda _: None)
    replies, calls = [], []
    handler.reply = lambda status, data, extra_headers=None: replies.append((status, data))
    def process(request):
        calls.append(request)
        return {"ok": True}
    handler.server = SimpleNamespace(bridge=SimpleNamespace(
        token="private-token", slot=__import__("threading").BoundedSemaphore(1), process=process))
    return handler, replies, calls


def test_http_accepts_inline_frames_after_authentication():
    handler, replies, calls = handler_for(body())
    handler.do_POST()
    assert replies == [(200, {"ok": True})] and len(calls) == 1


def test_http_keeps_text_request_limit_despite_larger_vision_envelope(monkeypatch):
    monkeypatch.setattr(bridge, "MAX_TEXT_BODY", 300)
    request = {"messages": [{"role": "user", "content": "Small text"}], "padding": "x" * 400}
    handler, replies, calls = handler_for(request)
    handler.do_POST()
    assert replies[0][0] == 413 and calls == []


def test_unauthenticated_frames_never_reach_parsing_or_worker(monkeypatch):
    handler, replies, calls = handler_for(body(), token="Bearer wrong-token")
    monkeypatch.setattr(bridge, "request_messages", lambda _: (_ for _ in ()).throw(AssertionError("No parsing")))
    handler.do_POST()
    assert replies[0][0] == 401 and calls == []


def test_health_advertises_vision_without_claiming_account_validation():
    handler, replies, _ = handler_for(body())
    handler.path = "/health"
    handler.do_GET()
    assert replies[0][0] == 200
    assert replies[0][1]["visionSupported"] is True
    assert replies[0][1]["account_authentication"] == "not_checked"
