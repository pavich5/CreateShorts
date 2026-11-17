"""Source retrieval uses real frame IDs and keeps full-source provenance."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

import source_moments as sm


def samples(window=0, start=860, count=12):
    return [{"id": index, "time": start + index, "window_id": window}
            for index in range(count)]


def verified(**changes):
    return {"matched": True, "start_frame_id": 2, "end_frame_id": 7,
            "subject_frame_ids": [2, 3], "reaction_frame_ids": [5, 6],
            "subject_view": "recognizable",
            "summary": "The revealed car and a visible surprised gesture.", "confidence": .9,
            **changes}


def test_transcript_windows_cover_the_ending_of_long_sources():
    transcript = {"segments": [{"start": i, "end": i + 2, "text": "word " * 300}
                                for i in range(0, 7200, 15)]}
    result = sm.transcript_windows(transcript, 7200)
    assert len(result) <= sm.MAX_TRANSCRIPT_WINDOWS
    assert result[0]["start"] == 0
    assert result[-1]["end"] >= 7185
    assert len(json.dumps(result)) < sm.MAX_TRANSCRIPT_CHARS


def test_transcript_uses_word_text_when_segment_text_missing():
    result = sm.transcript_windows({"segments": [
        {"start": 100, "end": 102, "words": [{"word": "car"}, {"word": "reveal"}]},
        {"start": float("nan"), "end": 200, "text": "invalid"},
        {"start": 990, "end": 1001, "text": "outside"},
    ]}, 200)
    assert len(result) == 1 and result[0]["text"] == "car reveal"
    assert sm.transcript_windows({"segments": 1}, 200) == []


def test_current_time_maps_reordered_source_edits():
    segments = [{"start": 900, "end": 910}, {"start": 100, "end": 120}]
    assert sm.current_source_time(segments, 12) == 102
    assert sm.current_source_time(segments, 10) == 100
    assert sm.current_source_time(segments, 30) is None
    assert sm.current_source_time(segments, float("inf")) is None


def test_overview_still_covers_whole_source_when_ending_is_preferred():
    times = sm.overview_times(1102, [{"start": 840, "end": 890}], "end",
                             [{"start": 853, "end": 913}], 57.9)
    assert len(times) <= sm.MAX_OVERVIEW_FRAMES
    assert min(times) < 60 and max(times) > 1080
    assert any(850 < value < 910 for value in times)
    assert any(920 < value < 940 for value in times)  # payoff after short's end
    assert all(0 <= value < 1102 for value in times)
    assert times == sorted(set(times))


def test_refinement_has_bounded_dense_samples_and_window_provenance():
    result = sm.refinement_samples([{"time": 875}, {"time": 1000}], [], 1102, 6)
    assert len(result) <= sm.MAX_FINE_FRAMES
    assert {sample["window_id"] for sample in result} == {0, 1}
    assert all(0 <= sample["time"] < 1102 for sample in result)
    assert max(b["time"] - a["time"] for a, b in zip(result[:10], result[1:11])) < 1.1


def test_transcript_lead_is_only_a_refinement_fallback_not_visual_proof():
    result = sm.refinement_samples([], [{"start": 850, "end": 880}], 1102, 6)
    assert result and 850 < result[0]["time"] < 880
    with pytest.raises(sm.SourceMomentNotFound):
        sm.refinement_samples([], [], 1102, 6)


def test_verified_range_comes_only_from_trusted_frames():
    result = sm.validate_match(verified(), samples(), 1102, 6, requires_reaction=True)
    assert result["start"] == pytest.approx(861.85)
    assert result["end"] == 868
    assert result["evidence"]["subject_frames"] == [862, 863]
    assert result["evidence"]["reaction_frames"] == [865, 866]
    assert result["evidence"]["verified"] is True


@pytest.mark.parametrize("change", [
    {"matched": False}, {"start_frame_id": 999}, {"start_frame_id": True},
    {"end_frame_id": 0}, {"confidence": float("nan")}, {"confidence": .4},
    {"confidence": 3}, {"summary": ""}, {"subject_frame_ids": []},
    {"subject_frame_ids": [99]}, {"subject_frame_ids": [True]},
    {"subject_frame_ids": [0]}, {"reaction_frame_ids": []},
    {"subject_view": "fragment_only"}, {"subject_view": "not_visible"},
])
def test_unverified_or_fabricated_ranges_are_rejected(change):
    with pytest.raises(sm.SourceMomentNotFound):
        sm.validate_match(verified(**change), samples(), 1102, 6, requires_reaction=True)


def test_reaction_is_optional_when_user_only_requests_the_object():
    result = sm.validate_match(verified(reaction_frame_ids=[]), samples(), 1102, 6)
    assert result["evidence"]["reaction_frames"] == []


def test_model_cannot_bridge_two_unrelated_search_windows():
    frames = samples()
    frames[7]["window_id"] = 1
    with pytest.raises(sm.SourceMomentNotFound, match="continuous"):
        sm.validate_match(verified(), frames, 1102, 6)


def test_model_cannot_select_an_arbitrarily_long_source_range():
    frames = samples()
    frames[7]["time"] = 900
    with pytest.raises(sm.SourceMomentNotFound, match="too short or too long"):
        sm.validate_match(verified(), frames, 1102, 6)


def test_our_own_end_padding_cannot_reject_a_valid_frame_span():
    frames = samples()
    frames[7]["time"] = 873.4  # 11.4 seconds after the first selected frame
    result = sm.validate_match(verified(), frames, 1102, 8)
    assert result["end"] - result["start"] == pytest.approx(12)
    assert result["end"] > frames[7]["time"]


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    source = tmp_path / "original.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(sm, "probe_source", lambda path: {
        "duration": 1102, "width": 1920, "height": 1080})
    history, calls = {}, []

    def boards(path, frames, directory, phase):
        directory.mkdir(parents=True, exist_ok=True)
        sheet = directory / (phase + ".jpg")
        sheet.write_bytes(b"test-image")
        history[phase] = frames
        return [str(sheet)], frames

    def text(prompt, schema, timeout):
        calls.append("text")
        return {"windows": [{"window_id": 1, "reason": "Possible reveal discussion"}]}

    def vision(prompt, schema, *, images, timeout):
        assert images and all(Path(path).is_file() for path in images)
        if schema is sm.OVERVIEW_SCHEMA:
            calls.append("overview")
            frame = min(history["overview"], key=lambda f: abs(f["time"] - 875))
            return {"matches": [{"frame_id": frame["id"], "observation": "Car and person visible", "confidence": .9}]}
        calls.append("detail")
        frames = history["detail"]
        return verified(start_frame_id=frames[2]["id"], end_frame_id=frames[7]["id"],
                        subject_frame_ids=[frames[3]["id"]], reaction_frame_ids=[frames[6]["id"]])

    bridge = SimpleNamespace(available=lambda: True, vision_available=lambda: True,
                             generate_json=text, generate_vision_json=vision)
    monkeypatch.setitem(sys.modules, "codex_editor", bridge)
    monkeypatch.setattr(sm, "_storyboards", boards)
    transcript = {"segments": [
        {"start": 0, "end": 5, "text": "An introduction"},
        {"start": 850, "end": 860, "text": "Open your eyes, here is the gift"},
    ]}
    return source, transcript, bridge, history, calls


def test_full_search_requires_both_overview_and_actual_frame_verification(pipeline, tmp_path):
    source, transcript, _, history, calls = pipeline
    result = sm.find_source_moment(source, transcript, "car reveal and his reaction",
                                   preferred_region="end", cache_dir=tmp_path / "cache")
    assert calls == ["text", "overview", "detail"]
    assert result["evidence"]["verified"] and result["evidence"]["reaction_frames"]
    assert 850 < result["start"] < result["end"] < 900
    assert not any("path" in key for key in result)
    assert len(history["overview"]) <= 48 and len(history["detail"]) <= 36


def test_success_is_cached_without_new_provider_calls(pipeline, tmp_path):
    source, transcript, _, _, calls = pipeline
    first = sm.find_source_moment(source, transcript, "the car reveal", cache_dir=tmp_path / "cache")
    second = sm.find_source_moment(source, transcript, "the car reveal", cache_dir=tmp_path / "cache")
    assert first["cached"] is False and second["cached"] is True
    assert second["start"] == first["start"]
    assert calls == ["text", "overview", "detail"]


def test_changed_source_invalidates_evidence_cache(pipeline, tmp_path):
    source, transcript, _, _, calls = pipeline
    first = sm.find_source_moment(source, transcript, "the car reveal", cache_dir=tmp_path / "cache")
    source.write_bytes(b"a different original")
    second = sm.find_source_moment(source, transcript, "the car reveal", cache_dir=tmp_path / "cache")
    assert first["source_fingerprint"] != second["source_fingerprint"]
    assert len(calls) == 6


def test_text_failure_still_allows_actual_visual_search(pipeline, tmp_path):
    source, transcript, bridge, _, calls = pipeline
    bridge.generate_json = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable"))
    result = sm.find_source_moment(source, transcript, "the car reveal", cache_dir=tmp_path / "cache")
    assert result["evidence"]["verified"] is True
    assert calls == ["overview", "detail"]


def test_unavailable_visual_connection_never_claims_a_match(pipeline, tmp_path):
    source, transcript, bridge, _, calls = pipeline
    bridge.vision_available = lambda: False
    with pytest.raises(sm.SourceMomentNotFound, match="not connected"):
        sm.find_source_moment(source, transcript, "the car reveal", cache_dir=tmp_path / "cache")
    assert calls == []


def test_model_failure_is_sanitized_and_never_cached(pipeline, tmp_path):
    source, transcript, bridge, _, _ = pipeline
    bridge.generate_vision_json = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("token-secret-123"))
    with pytest.raises(sm.SourceMomentNotFound) as error:
        sm.find_source_moment(source, transcript, "the car reveal", cache_dir=tmp_path / "cache")
    assert "token-secret" not in str(error.value)
    assert list((tmp_path / "cache").glob("*/verified.json")) == []


def test_source_missing_is_an_actionable_failure(tmp_path):
    with pytest.raises(sm.SourceMomentNotFound, match="no longer available"):
        sm.find_source_moment(tmp_path / "gone.mp4", {}, "car reveal")


@pytest.mark.parametrize("duration", [True, float("nan"), float("inf"), 1, 30, None])
def test_requested_sequence_length_is_bounded(pipeline, duration):
    source, transcript, _, _, _ = pipeline
    with pytest.raises(sm.SourceMomentNotFound):
        sm.find_source_moment(source, transcript, "car reveal", duration=duration)
