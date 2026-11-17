"""Source-footage edits commit one new version only after captions/delivery."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import ai_clip_edit as ai


def source_plan(kind="source_append", start=6, end=10, query="the car reveal and his reaction"):
    return {"operations": [{"type": kind, "start": start, "end": end,
                             "query": query, "preferred_region": "end"}]}


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    app = pytest.importorskip("app")
    directory = tmp_path / "source-job"
    directory.mkdir()
    original = directory / "clip.mp4"
    original.write_bytes(b"original-clean-picture")
    captioned = directory / "subtitled_1_clip.mp4"
    captioned.write_bytes(b"original-captioned-picture")
    source = directory / "source.mp4"
    source.write_bytes(b"full-original-source")
    transcript = {"language": "en", "segments": [
        {"start": 10, "end": 16, "text": "The setup", "words": [
            {"word": "setup", "start": 12, "end": 13}]},
        {"start": 60, "end": 64, "text": "The car reveal", "words": [
            {"word": "car", "start": 61, "end": 62}]},
    ]}
    clip = {"video_url": "/videos/source-job/clip.mp4", "start": 10, "end": 16,
            "recipe": {"segments": [{"start": 10, "end": 16}]},
            "caption_style": {"font": "Arial", "font_size": 60}}
    metadata = directory / "source_metadata.json"
    metadata.write_text(json.dumps({"shorts": [clip], "transcript": transcript}))
    job = {"status": "completed", "result": {"clips": [clip]}, "ready_files": {0: "clip.mp4"}}
    monkeypatch.setattr(app, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app, "jobs", {"source-job": job})
    monkeypatch.setattr(app, "_rerender_locks", {})
    monkeypatch.setattr(app, "BILLING_ENABLED", False)
    monkeypatch.setattr(app.llm_backend, "active", lambda: True)
    monkeypatch.setattr(app, "_ensure_job_files", AsyncMock())
    monkeypatch.setattr(app, "_assert_job_owner", AsyncMock())
    monkeypatch.setattr(app, "require_managed_entitlement", AsyncMock())
    monkeypatch.setattr(app, "_locate_source", lambda _: str(source))
    monkeypatch.setattr(app, "_archive_clip_edit_bg", lambda *args: None)
    monkeypatch.setattr(app, "_deliver", AsyncMock(side_effect=lambda request, job_id, filename: filename))
    monkeypatch.setattr(ai, "probe", lambda path: {"width": 160, "height": 288, "fps": 12,
                                                  "duration": 10 if 'ai_' in Path(path).name else 6})
    planned, rendered, captions = [], [], []

    def planning(instructions, info, **kwargs):
        planned.append({"instructions": instructions, "info": dict(info), **kwargs})
        return ai.validate_plan(source_plan(), info["duration"], kwargs["source_available"], kwargs["has_burns"])

    composed = {"language": "en", "segments": [
        {"start": 0, "end": 6, "text": "The setup", "words": [
            {"word": "setup", "start": 2, "end": 3}]},
        {"start": 6, "end": 10, "text": "The car reveal", "words": [
            {"word": "car", "start": 7, "end": 8}]},
    ]}
    record = {"mode": "source_append", "at": 6, "duration": 4,
              "source_start": 60, "source_end": 64, "new_duration": 10,
              "summary": "Actual car and reaction", "layout_ranges": [
                  {"start": 0, "end": 6, "layout": "track"},
                  {"start": 6, "end": 10, "layout": "wide"}]}
    updates = {"duration_seconds": 10, "source_edits": [record],
               "recipe": {"v": 1, "segments": [{"start": 10, "end": 16}, {"start": 60, "end": 64}],
                          "canonical_range": {"start": 10, "end": 16}},
               "layout_ranges": record["layout_ranges"]}

    def render(input_path, output_path, **kwargs):
        rendered.append({"input_path": str(input_path), "output_path": str(output_path), **kwargs})
        Path(output_path).write_bytes(b"setup-and-actual-car-reveal")
        return {"record": deepcopy(record), "clip_updates": deepcopy(updates),
                "transcript": deepcopy(composed)}

    def recap(path, words, start, end, style, layouts=None):
        captions.append({"path": str(path), "transcript": deepcopy(words), "start": start,
                         "end": end, "style": style, "layout": layouts})
        output = directory / ("subtitled_2_" + Path(path).name)
        output.write_bytes(b"setup-reveal-and-composed-captions")
        return str(output)

    monkeypatch.setattr(ai, "plan_edit", planning)
    monkeypatch.setitem(sys.modules, "ai_source_edit", SimpleNamespace(render_source_operation=render))
    monkeypatch.setattr(app, "_restyle_captions", recap)
    monkeypatch.setattr(app, "_reapply_captions", lambda *args: (_ for _ in ()).throw(
        AssertionError("Old timeline cannot caption an inserted scene")))
    return SimpleNamespace(app=app, directory=directory, clip=clip, job=job, metadata=metadata,
                           original=original, captioned=captioned, source=source,
                           transcript=transcript, composed=composed, planned=planned,
                           rendered=rendered, captions=captions, render=render)


def apply(endpoint, **fields):
    request = endpoint.app.AIEditRequest(job_id="source-job", instructions="Find the car reveal at the end and add his reaction.", **fields)
    return asyncio.run(endpoint.app.edit_clip_with_ai(request, None))


def snapshot(endpoint):
    return endpoint.metadata.read_bytes(), deepcopy(endpoint.clip), deepcopy(endpoint.job["ready_files"])


def test_source_append_recovers_the_selected_render_source_clock(endpoint):
    from clip_timing import write_render_timing

    # A restarted generation published a new plan while this old completed
    # render is still selected. Its bound provenance is the source of truth.
    endpoint.clip.pop('recipe')
    endpoint.clip.update(start=10, end=14)
    write_render_timing(endpoint.original, 100, 106)
    result = apply(endpoint)
    assert result['success']
    assert endpoint.rendered[0]['current_segments'] == [{'start': 100, 'end': 106}]
    assert endpoint.rendered[0]['current_clip']['start'] == 100
    assert endpoint.rendered[0]['current_clip']['end'] == 106


def test_playhead_and_source_context_reach_planner_and_renderer(endpoint):
    result = apply(endpoint, playhead_time=5.7)
    assert result["success"]
    assert endpoint.planned[0]["info"]["playhead_time"] == 5.7
    assert endpoint.planned[0]["source_available"] is True
    call = endpoint.rendered[0]
    assert call["playhead_time"] == 5.7
    assert call["source_path"] == str(endpoint.source)
    assert call["current_segments"] == [{"start": 10, "end": 16}]
    assert call["transcript"] == endpoint.transcript
    assert call["operation"].query == "the car reveal and his reaction"


def test_playhead_past_clip_end_is_clamped_for_planner_and_renderer(endpoint):
    result = apply(endpoint, playhead_time=99)
    assert result["success"]
    assert endpoint.planned[0]["info"]["playhead_time"] == 6
    assert endpoint.rendered[0]["playhead_time"] == 6


def test_append_returns_new_duration_recipe_preview_and_commits_metadata(endpoint):
    result = apply(endpoint)
    assert result["duration_seconds"] == 10
    assert (result["preview_start"], result["preview_end"]) == (6, 10)
    assert result["recipe"]["segments"][-1] == {"start": 60, "end": 64}
    assert result["source_edits"][0]["summary"] == "Actual car and reaction"
    saved = json.loads(endpoint.metadata.read_text())["shorts"][0]
    assert saved["video_url"] == endpoint.clip["video_url"] == result["new_video_url"]
    assert saved["duration_seconds"] == endpoint.clip["duration_seconds"] == 10
    assert saved["recipe"] == endpoint.clip["recipe"] == result["recipe"]
    assert saved["ai_edit"]["previous_video_url"] == "/videos/source-job/clip.mp4"
    assert endpoint.job["ready_files"][0] == result["new_video_url"].split("/")[-1]
    assert endpoint.original.read_bytes() == b"original-clean-picture"


def test_old_input_after_append_is_rejected_before_planning(endpoint):
    apply(endpoint)
    before = snapshot(endpoint)
    calls_before = len(endpoint.planned)
    request = endpoint.app.AIEditRequest(job_id="source-job", input_filename="clip.mp4",
                                       instructions="Brighten this clip.")
    with pytest.raises(endpoint.app.HTTPException) as caught:
        asyncio.run(endpoint.app.edit_clip_with_ai(request, None))
    assert caught.value.status_code == 409
    assert "latest version" in caught.value.detail
    assert len(endpoint.planned) == calls_before
    assert len(endpoint.rendered) == 1
    assert snapshot(endpoint) == before


def test_source_append_captions_follow_new_composed_timeline(endpoint):
    result = apply(endpoint, input_filename=endpoint.captioned.name)
    assert result["success"]
    assert len(endpoint.captions) == 1
    caption = endpoint.captions[0]
    assert caption["transcript"] == endpoint.composed
    assert (caption["start"], caption["end"]) == (0, 10)
    assert caption["transcript"]["segments"][-1]["words"][0]["start"] == 7
    assert caption["layout"][-1]["layout"] == "wide"
    assert Path(caption["path"]).read_bytes() == b"setup-and-actual-car-reveal"
    assert endpoint.captioned.read_bytes() == b"original-captioned-picture"


def test_source_append_uses_default_captioner_with_composed_words(endpoint, monkeypatch):
    endpoint.clip.pop("caption_style")
    calls = []
    def caption(path, words, start, end):
        calls.append((deepcopy(words), start, end))
        output = endpoint.directory / ("subtitled_2_" + Path(path).name)
        output.write_bytes(b"default-captioned-new-timeline")
        return str(output)
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(auto_caption_clip=caption))
    result = apply(endpoint, input_filename=endpoint.captioned.name)
    assert result["success"] and calls == [(endpoint.composed, 0, 10)]


def test_default_captioner_receives_layout_on_restored_hook_path(endpoint, monkeypatch):
    import hooks
    import layout_ranges

    endpoint.clip.pop("caption_style")
    recipe, image = hooks.recipe_paths(str(endpoint.directory), endpoint.original.name)
    Path(recipe).write_text(json.dumps({"source": endpoint.original.name,
                                       "x": 0, "y": 0, "duration": 3}))
    Path(image).write_bytes(b"existing-hook-image")
    def encode(command, source, outputs):
        Path(outputs[0]).write_bytes(b"new-source-with-restored-hook")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(hooks, "_run_encode", encode)
    calls = []
    def caption(path, words, start, end):
        calls.append({"path": str(path), "transcript": deepcopy(words),
                      "start": start, "end": end, "layout": layout_ranges.read(str(path))})
        output = endpoint.directory / ("subtitled_2_" + Path(path).name)
        output.write_bytes(b"default-captioned-hook-and-new-timeline")
        return str(output)
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(auto_caption_clip=caption))

    result = apply(endpoint, input_filename=endpoint.captioned.name)

    assert result["success"] and len(calls) == 1
    assert Path(calls[0]["path"]).name.startswith("hooked_")
    assert calls[0]["transcript"] == endpoint.composed
    assert (calls[0]["start"], calls[0]["end"]) == (0, 10)
    assert calls[0]["layout"] == [
        {"start": 0, "end": 6, "layout": "track"},
        {"start": 6, "end": 10, "layout": "wide"}]


def test_search_rejection_keeps_original_version_and_metadata(endpoint, monkeypatch):
    before = snapshot(endpoint)
    def reject(*args, **kwargs):
        raise ValueError("No matching source moment could be visually verified.")
    monkeypatch.setitem(sys.modules, "ai_source_edit", SimpleNamespace(render_source_operation=reject))
    with pytest.raises(endpoint.app.HTTPException) as caught:
        apply(endpoint)
    assert caught.value.status_code == 400
    assert "visually verified" in caught.value.detail
    assert snapshot(endpoint) == before


def test_delivery_failure_does_not_commit_new_source_recipe(endpoint, monkeypatch):
    before = snapshot(endpoint)
    monkeypatch.setattr(endpoint.app, "_deliver", AsyncMock(side_effect=RuntimeError("delivery failed")))
    with pytest.raises(endpoint.app.HTTPException) as caught:
        apply(endpoint)
    assert caught.value.status_code == 500
    assert snapshot(endpoint) == before


def test_caption_failure_does_not_commit_new_source_recipe(endpoint, monkeypatch):
    before = snapshot(endpoint)
    monkeypatch.setattr(endpoint.app, "_restyle_captions", lambda *args: None)
    with pytest.raises(endpoint.app.HTTPException) as caught:
        apply(endpoint, input_filename=endpoint.captioned.name)
    assert caught.value.status_code == 500
    assert snapshot(endpoint) == before


def test_selected_range_rejects_append_before_source_search(endpoint):
    before = snapshot(endpoint)
    with pytest.raises(endpoint.app.HTTPException) as caught:
        apply(endpoint, range_start=3, range_end=5)
    assert caught.value.status_code == 400
    assert "Whole clip" in caught.value.detail
    assert endpoint.rendered == [] and snapshot(endpoint) == before


@pytest.mark.parametrize("kind", ["source_cutaway", "source_insert", "source_append"])
def test_source_operation_requires_source_and_a_meaningful_query(kind):
    raw = source_plan(kind, 3, 5)
    with pytest.raises(ValueError, match="original full video"):
        ai.validate_plan(raw, 6, False)
    with pytest.raises(ValueError, match="source moment"):
        ai.validate_plan(source_plan(kind, 3, 5, query="  "), 6, True)
    mixed = {"operations": raw["operations"] + [{"type": "brighten", "start": 0, "end": 2}]}
    with pytest.raises(ValueError, match="separately"):
        ai.validate_plan(mixed, 6, True)


def test_append_timing_normalizes_to_the_actual_short_end():
    result = ai.validate_plan(source_plan("source_append", 57.9, 65.9), 59.3, True)
    operation = result.operations[0]
    assert (operation.start, operation.end) == pytest.approx((59.3, 67.3))


def test_source_cutaway_is_constrained_to_explicit_selected_range():
    plan = ai.validate_plan(source_plan("source_cutaway", 0, 6), 6, True)
    constrained = ai.constrain_plan(plan, 3, 5, 6)
    assert (constrained.operations[0].start, constrained.operations[0].end) == (3, 5)


def test_planner_can_request_full_source_search_instead_of_rejecting_the_reframe(monkeypatch):
    seen = []
    monkeypatch.setattr(ai.llm_backend, "active", lambda: True)
    monkeypatch.setenv('LLM_MODEL', 'test-generic-local-model')
    def generate(prompt, schema):
        seen.append(prompt)
        assert schema is ai.EditPlan
        return source_plan("source_append", 59.3, 67.3), None
    monkeypatch.setattr(ai.llm_backend, "generate_json", generate)
    result = ai.plan_edit("Show the car reveal and his reaction at the end of the full video.",
                          {"duration": 59.3, "playhead_time": 57.9}, source_available=True)
    assert result.operations[0].type == "source_append"
    assert "FULL original" in seen[0] and "57.9" in seen[0]

