"""Bounded editorial decisions through the existing signed-in Codex adapter.

This client does not launch Codex, copy account credentials, or fall back to a
different model. The host's authenticated bridge owns its read-only, tool-free
Codex workers. Only supplied context, explicit preview frames, and JSON Schema
cross the API.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

import llm_backend

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_VISION_REQUEST_BYTES = 12 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 6
MAX_IMAGE_PIXELS = 12_000_000
MAX_IMAGE_SIDE = 4096
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TIMEOUT = 540.0
MAX_BUSY_RETRIES = 12
HEALTH_TIMEOUT = 2.0
_editor_lock = threading.Lock()


class CodexEditorError(RuntimeError):
    """A sanitized editor failure safe for job logs and API responses."""


def _configuration():
    url = llm_backend.base_url()
    explicit = os.environ.get("CODEX_EDITOR_ENABLED", "").strip().lower() in ("1", "true", "yes")
    if not url or (llm_backend.model_name() != "codex-account" and not explicit):
        return None
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        local = host in ("localhost", "host.docker.internal")
        if not local:
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                return None
        if (not local or parsed.scheme not in ("http", "https")
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return None
        # Accessing .port also rejects malformed/out-of-range configuration.
        _ = parsed.port
    except ValueError:
        return None
    health = urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))
    return url + "/chat/completions", health


def _healthy(client, health_url, timeout=HEALTH_TIMEOUT, *, vision=False):
    try:
        response = client.get(health_url, headers=llm_backend._headers(),
                              timeout=timeout, follow_redirects=False)
        if response.status_code != 200 or len(response.content) > 16 * 1024:
            return False
        data = response.json()
        return (isinstance(data, dict) and data.get("status") == "ready"
                and data.get("backend") == "codex-cli"
                and (not vision or data.get("visionSupported") is True))
    except (httpx.HTTPError, ValueError, TypeError):
        return False


def available():
    """True only for a healthy, explicitly identified local Codex adapter."""
    configured = _configuration()
    if configured is None:
        return False
    try:
        with llm_backend._client() as client:
            return _healthy(client, configured[1])
    except (httpx.HTTPError, ValueError, TypeError):
        return False


def vision_available():
    """True when the identified local bridge supports bounded frame inputs."""
    configured = _configuration()
    if configured is None:
        return False
    try:
        with llm_backend._client() as client:
            return _healthy(client, configured[1], vision=True)
    except (httpx.HTTPError, ValueError, TypeError):
        return False


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CodexEditorError("Codex editor timed out; try again after the current edit finishes.")
    return remaining


def _parse_completion(response):
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise CodexEditorError("Codex editor returned an oversized response.")
    try:
        body = response.json()
        message = body["choices"][0]["message"]
        if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
            raise ValueError("Text-only response required")
        content = message["content"]
        if isinstance(content, list):
            if any(not isinstance(part, dict) or part.get("type") != "text" for part in content):
                raise ValueError("Only text content is supported")
            content = "".join(part.get("text", "") for part in content)
        if not isinstance(content, str):
            raise ValueError("Missing text")
        # The bridge enforces JSON Schema. Do not scrape a plausible object
        # out of surrounding prose or accept a trailing second JSON value.
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Expected an object")
        return result
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise CodexEditorError("Codex editor did not return a valid editorial plan.") from None


def _generate_json(prompt, schema_dict, timeout=MAX_TIMEOUT, *, image_parts=None):
    """Return one schema-constrained decision from signed-in host Codex.

    Local calls serialize because the existing adapter has one worker slot.
    Only its explicit 429 busy response is retried; model failures are never
    silently rerun or sent to Gemini. ``timeout`` includes local queue wait.
    """
    configured = _configuration()
    if configured is None:
        raise CodexEditorError("Connect the local Codex editor before requesting an editorial plan.")
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(schema_dict, dict):
        raise CodexEditorError("Codex editor needs text context and a JSON object schema.")
    try:
        seconds = float(timeout)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Invalid timeout")
        deadline = time.monotonic() + min(seconds, MAX_TIMEOUT)
        content = ([{"type": "text", "text": prompt}, *image_parts]
                   if image_parts else prompt)
        payload = {
            "model": "codex-account", "stream": False,
            "messages": [{"role": "system", "content": "Return the editorial decision as one JSON object. Use only the supplied context; do not use tools."},
                         {"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "editorial_plan", "schema": schema_dict}},
        }
        limit = MAX_VISION_REQUEST_BYTES if image_parts else MAX_REQUEST_BYTES
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limit:
            raise CodexEditorError("The clip context is too large for the Codex editor.")
    except (TypeError, ValueError, OverflowError):
        raise CodexEditorError("Codex editor received an invalid schema or timeout.") from None

    if not _editor_lock.acquire(timeout=_remaining(deadline)):
        raise CodexEditorError("Codex editor timed out while waiting for another edit.")
    try:
        with llm_backend._client() as client:
            if not _healthy(client, configured[1], min(HEALTH_TIMEOUT, _remaining(deadline)), vision=bool(image_parts)):
                message = ("The local Codex editor needs its frame-analysis update. Restart CreateShorts and try again."
                           if image_parts else "The local Codex editor is unavailable. Start CreateShorts and try again.")
                raise CodexEditorError(message)
            for attempt in range(MAX_BUSY_RETRIES + 1):
                response = client.post(configured[0], json=payload, headers=llm_backend._headers(),
                                       timeout=_remaining(deadline), follow_redirects=False)
                if response.status_code == 429:
                    if attempt == MAX_BUSY_RETRIES:
                        raise CodexEditorError("Codex editor is busy with another request. Try again shortly.")
                    try:
                        retry_after = float(response.headers.get("Retry-After", "5"))
                        if not math.isfinite(retry_after):
                            retry_after = 5.0
                    except ValueError:
                        retry_after = 5.0
                    wait = min(5.0, max(0.25, retry_after))
                    if wait >= _remaining(deadline):
                        raise CodexEditorError("Codex editor timed out while waiting for another edit.")
                    time.sleep(wait)
                    continue
                if response.status_code >= 400:
                    raise CodexEditorError("Codex editor could not complete the editorial plan. Check its connection and try again.")
                if response.status_code != 200:
                    raise CodexEditorError("Codex editor returned an unexpected response.")
                _remaining(deadline)
                return _parse_completion(response)
    except httpx.TimeoutException:
        raise CodexEditorError("Codex editor timed out; try again after the current edit finishes.") from None
    except httpx.HTTPError:
        raise CodexEditorError("Could not reach the local Codex editor. Start CreateShorts and try again.") from None
    finally:
        _editor_lock.release()


def generate_json(prompt, schema_dict, timeout=MAX_TIMEOUT):
    """Return a text-context decision; existing callers keep their contract."""
    return _generate_json(prompt, schema_dict, timeout)


def generate_vision_json(prompt, schema_dict, images, timeout=MAX_TIMEOUT):
    """Judge explicit local JPEG/PNG preview frames, with all tools disabled.

    Paths are consumed here, never sent to or chosen by the model. The host
    bridge accepts only their bounded inline bytes and supplies them to Codex
    as image inputs in a private, read-only working directory.
    """
    if not isinstance(images, (list, tuple)) or not 1 <= len(images) <= MAX_IMAGES:
        raise CodexEditorError("Source analysis needs one to six preview frames.")
    parts, total = [], 0
    try:
        from PIL import Image
        for item in images:
            path = Path(item)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("Invalid preview frame")
            data = path.read_bytes()
            total += len(data)
            if not data or total > MAX_IMAGE_BYTES:
                raise ValueError("Preview frames exceed their byte limit")
            with Image.open(path) as image:
                if (image.format not in {"JPEG", "PNG"} or min(image.size) <= 0
                        or max(image.size) > MAX_IMAGE_SIDE
                        or image.width * image.height > MAX_IMAGE_PIXELS):
                    raise ValueError("Invalid preview image")
                mime = "image/jpeg" if image.format == "JPEG" else "image/png"
                image.verify()
            parts.append({"type": "image_url", "image_url": {
                "url": "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")}})
    except Exception:
        raise CodexEditorError("Use valid JPEG or PNG preview frames within the source-analysis limits.") from None
    return _generate_json(prompt, schema_dict, timeout, image_parts=parts)
