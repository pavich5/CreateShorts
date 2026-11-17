"""Codex editorial choices are grounded, optional, bounded, and reusable."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import clip_editorial as editorial


def transcript(start=100):
    tokens = ["I", "stopped", "buying", "plastic", "bottles.", "A", "steel",
              "bottle", "keeps", "water", "cold.", "It", "lasted", "three", "years."]
    times = [0, .3, .6, .9, 1.2, 4, 4.25, 4.5, 4.75, 5, 5.25, 14, 14.25, 14.5, 14.75]
    return {"segments": [{"start": start, "end": start + 30,
                          "words": [{"word": token, "start": start + time,
                                     "end": start + time + .2}
                                    for token, time in zip(tokens, times)]}]}


def response():
    opening = {"opening_word_ids": [0, 1, 2, 3, 4],
               "opening_quote": "I stopped buying plastic bottles."}
    return {
        "hook_candidates": [
            {"hook": "Why I Stopped Buying Plastic", "specificity": 5,
             "curiosity": 4, "payoff": 5, "opening_match": 5,
             "reason": "Names the specific change the speaker immediately discusses.",
             "evidence_word_ids": [0, 1, 2, 3, 4],
             "evidence_quote": "I stopped buying plastic bottles.", **opening},
            {"hook": "One Bottle Lasted Three Years", "specificity": 5,
             "curiosity": 4, "payoff": 5, "opening_match": 3,
             "reason": "A concrete payoff appears later, with a weaker opening match.",
             "evidence_word_ids": [11, 12, 13, 14],
             "evidence_quote": "It lasted three years.", **opening},
            {"hook": "A Better Way To Drink Water", "specificity": 2,
             "curiosity": 3, "payoff": 4, "opening_match": 3,
             "reason": "The benefit is supported but less specific than the chosen hook.",
             "evidence_word_ids": [5, 6, 7, 8, 9, 10],
             "evidence_quote": "A steel bottle keeps water cold.", **opening},
        ],
        "chosen_hook": "Why I Stopped Buying Plastic",
        "chosen_title": "Why I Stopped Buying Plastic Bottles",
        "title_evidence_word_ids": [0, 1, 2, 3, 4],
        "title_evidence_quote": "I stopped buying plastic bottles.",
        "editor_note": "Lead with the specific decision and preserve the spoken explanation.",
    }


def validate(raw=None, allow_broll=False):
    return editorial.validate_plan(raw or response(), editorial.clip_words(transcript(), 100, 130),
                                   30, allow_broll)


def test_candidates_are_ranked_and_evidence_matches_actual_words():
    result = validate()
    assert result["provider"] == "codex" and result["status"] == "planned"
    assert result["chosen_hook"] == result["hook_candidates"][0]["hook"]
    assert [candidate["rank"] for candidate in result["hook_candidates"]] == [1, 2, 3]
    assert result["evidence_word_ids"] == [0, 1, 2, 3, 4]
    assert result["evidence_quotes"][0] == "I stopped buying plastic bottles."


@pytest.mark.parametrize("mutate", [
    lambda raw: raw.update(chosen_hook="A new unsupported headline"),
    lambda raw: raw.update(chosen_hook=raw["hook_candidates"][2]["hook"]),
    lambda raw: raw["hook_candidates"].pop(),
    lambda raw: raw["hook_candidates"][1].update(hook=raw["hook_candidates"][0]["hook"]),
    lambda raw: raw["hook_candidates"][0].update(hook="Stop"),
    lambda raw: raw["hook_candidates"][0].update(hook="word " * 14),
    lambda raw: raw["hook_candidates"][0].update(evidence_word_ids=[0, 999]),
    lambda raw: raw["hook_candidates"][0].update(evidence_word_ids=[0, 2]),
    lambda raw: raw["hook_candidates"][0].update(evidence_word_ids=[True]),
    lambda raw: raw["hook_candidates"][0].update(evidence_quote="The speaker never said this."),
    lambda raw: raw["hook_candidates"][0].update(payoff=True),
    lambda raw: raw["hook_candidates"][0].update(specificity=6),
    lambda raw: raw["hook_candidates"][0].update(opening_word_ids=[11, 12, 13, 14],
                                                opening_quote="It lasted three years."),
    lambda raw: raw.update(chosen_title="This Saved 1000 Dollars"),
    lambda raw: raw.update(title_evidence_quote="A made-up quotation"),
])
def test_invalid_or_unsupported_editorial_response_is_rejected(mutate):
    raw = response()
    mutate(raw)
    with pytest.raises(ValueError):
        validate(raw)


def test_invented_number_in_hook_is_rejected_even_with_real_quote():
    raw = response()
    raw["hook_candidates"][0]["hook"] = "Why I Saved 500 Dollars"
    raw["chosen_hook"] = raw["hook_candidates"][0]["hook"]
    with pytest.raises(ValueError, match="number absent"):
        validate(raw)


@pytest.fixture
def planner(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_EDITOR_ENABLED", "1")
    calls = []

    def generate(prompt, schema, timeout):
        calls.append((prompt, schema, timeout))
        return response()

    bridge = SimpleNamespace(available=lambda: True, generate_json=generate)
    monkeypatch.setitem(sys.modules, "codex_editor", bridge)
    clip = {"video_title_for_youtube_short": "Original title", "viral_hook_text": "Original hook",
            "layout_ranges": [{"start": 0, "end": 30, "layout": "track"}]}
    return tmp_path / "source_clip_1.mp4", clip, calls, bridge


def plan(path, clip, allow=True, data=None):
    return editorial.plan_clip(str(path), clip, data or transcript(), 100, 130, allow_broll=allow)


def test_disabled_does_not_import_or_call_codex(planner, monkeypatch):
    path, clip, calls, _ = planner
    monkeypatch.setenv("CODEX_EDITOR_ENABLED", "0")
    monkeypatch.setitem(sys.modules, "codex_editor", None)
    assert plan(path, clip) is None and calls == []
    assert list(path.parent.iterdir()) == []


def test_planning_sends_reference_data_and_schema_without_file_paths(planner):
    path, clip, calls, _ = planner
    original = copy.deepcopy(clip)
    result = plan(path, clip)
    assert result["status"] == "planned" and result["cached"] is False
    prompt, schema, timeout = calls[0]
    assert schema is editorial.PLAN_SCHEMA and timeout == 180
    assert str(path) not in prompt and str(path.parent) not in prompt
    assert '"word_id"' not in prompt.split("REFERENCE_DATA:\n", 1)[1]
    assert '"id": 6' in prompt
    assert "Do not change, reorder or replace spoken audio" in prompt
    assert "allow_broll" not in prompt and "search_query" not in prompt
    assert "cues" not in schema["properties"] and "broll_reason" not in schema["properties"]
    assert clip == original


def test_render_retry_reuses_plan_after_our_hook_and_title_are_applied(planner):
    path, clip, calls, bridge = planner
    first = plan(path, clip)
    clip.update(editorial=first, viral_hook_text=first["chosen_hook"],
                video_title_for_youtube_short=first["chosen_title"])
    bridge.available = lambda: False  # a cached decision needs no running host
    second = plan(path, clip)
    assert second["status"] == "planned" and second["cached"] is True
    assert second["original_hook"] == "Original hook"
    assert second["original_title"] == "Original title"
    assert len(calls) == 1


def test_repeated_screen_grounding_wording_does_not_duplicate_same_content_plan(planner):
    path, clip, calls, _ = planner
    first = plan(path, clip)
    clip.update(editorial=first, viral_hook_text="A differently grounded hook",
                video_title_for_youtube_short="A differently grounded title")
    second = plan(path, clip)
    assert second["cached"] is True and len(calls) == 1
    assert second["original_hook"] == "Original hook"


def test_legacy_visual_flag_does_not_change_text_only_plan(planner):
    path, clip, calls, _ = planner
    first = plan(path, clip, allow=False)
    second = plan(path, clip, allow=True)
    assert "cues" not in first and "cues" not in second
    assert len(calls) == 1 and second["cached"] is True


def test_previous_editorial_policy_cache_is_replanned(planner):
    path, clip, calls, _ = planner
    words = editorial.clip_words(transcript(), 100, 130)
    payload = editorial._input(clip, words, 30)
    payload["version"] = 4
    content = {key: value for key, value in payload.items() if key not in {"title", "current_hook"}}
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    cache = path.parent / ("editorial_" + hashlib.sha256(path.name.encode()).hexdigest()[:12] + ".json")
    stale = response()
    stale["cues"] = []
    cache.write_text(json.dumps({"fingerprint": fingerprint, "plan": stale}))
    result = plan(path, clip)
    assert result["status"] == "planned" and result["cached"] is False
    assert "cues" not in result and len(calls) == 1


def test_transcript_change_requires_a_new_decision(planner):
    path, clip, calls, _ = planner
    assert plan(path, clip)["status"] == "planned"
    changed = transcript()
    changed["segments"][0]["words"][4]["word"] = "cups."
    result = plan(path, clip, data=changed)
    assert len(calls) == 2
    assert result["status"] == "unavailable"  # the old quote no longer proves anything


def test_unavailable_is_explicit_and_does_not_claim_codex_generated_output(planner):
    path, clip, calls, bridge = planner
    original = copy.deepcopy(clip)
    bridge.available = lambda: False
    result = plan(path, clip)
    assert result["status"] == "unavailable" and result["provider"] is None
    assert result["requested_provider"] == "codex" and "cues" not in result
    assert calls == [] and clip == original


def test_failure_never_exposes_provider_details_or_calls_gemini(planner, monkeypatch, capsys):
    path, clip, _, bridge = planner

    def fail(*args, **kwargs):
        raise RuntimeError("https://host.invalid/?api_key=private-secret")

    bridge.generate_json = fail
    result = plan(path, clip)
    assert result["status"] == "unavailable" and "cues" not in result
    assert "private-secret" not in json.dumps(result) + capsys.readouterr().out
    assert list(path.parent.iterdir()) == []


def test_no_speech_or_no_opening_speech_never_calls_provider(planner):
    path, clip, calls, _ = planner
    assert plan(path, clip, data={"segments": []})["status"] == "unavailable"
    later = {"segments": [{"words": [{"word": "late", "start": 110, "end": 111}]}]}
    assert plan(path, clip, data=later)["status"] == "unavailable"
    assert calls == []


def test_validation_failure_identifies_the_rejected_constraint(planner):
    path, clip, _, bridge = planner
    raw = response()
    raw["hook_candidates"][0]["opening_quote"] = "An invented opening."
    bridge.generate_json = lambda *args, **kwargs: raw
    result = plan(path, clip)
    assert result["status"] == "unavailable" and result["provider"] is None
    assert result["validation_issue"] == "Evidence quote does not match its transcript words"
    assert clip["viral_hook_text"] == "Original hook"
    assert "broll_status" not in result and "cues" not in result


def test_actual_codex_coffee_plan_validates_and_is_cached(planner):
    """Recorded from the signed-in local Codex bridge, with no schema relaxation."""
    path, clip, _, bridge = planner
    text = ("I thought expensive coffee beans would fix bitter coffee. "
            "The real problem was boiling water. I lowered the temperature and "
            "the bitterness disappeared. Before buying fancy beans, change your water first.")
    data = {"segments": [{"words": [
        {"word": word, "start": index * .65, "end": index * .65 + .6}
        for index, word in enumerate(text.split())]}]}
    raw = {
        "hook_candidates": [
            {"hook": "Boiling Water Made My Coffee Bitter",
             "reason": "Directly reveals the unexpected cause and matches the opening problem.",
             "specificity": 5, "curiosity": 5, "payoff": 5, "opening_match": 5,
             "evidence_word_ids": list(range(9, 23)),
             "evidence_quote": "The real problem was boiling water. I lowered the temperature and the bitterness disappeared.",
             "opening_word_ids": list(range(8)),
             "opening_quote": "I thought expensive coffee beans would fix bitter"},
            {"hook": "Lower the Temperature, Lose the Bitterness",
             "reason": "States the practical adjustment and the result demonstrated in the clip.",
             "specificity": 5, "curiosity": 4, "payoff": 5, "opening_match": 4,
             "evidence_word_ids": list(range(15, 23)),
             "evidence_quote": "I lowered the temperature and the bitterness disappeared.",
             "opening_word_ids": list(range(8)),
             "opening_quote": "I thought expensive coffee beans would fix bitter"},
            {"hook": "Before Fancy Beans, Change Your Water",
             "reason": "Contrasts the assumed solution with the clip’s concrete recommendation.",
             "specificity": 5, "curiosity": 4, "payoff": 5, "opening_match": 5,
             "evidence_word_ids": list(range(23, 31)),
             "evidence_quote": "Before buying fancy beans, change your water first.",
             "opening_word_ids": list(range(8)),
             "opening_quote": "I thought expensive coffee beans would fix bitter"},
        ],
        "chosen_hook": "Boiling Water Made My Coffee Bitter",
        "chosen_title": "Bitter Coffee? Boiling Water Was the Real Problem",
        "title_evidence_word_ids": list(range(9, 23)),
        "title_evidence_quote": "The real problem was boiling water. I lowered the temperature and the bitterness disappeared.",
        "editor_note": "Lead with the specific cause-and-effect reveal, then use one focused visual at the boiling-water mention so the spoken correction remains central.",
    }
    requests = []

    def generate(*args, **kwargs):
        requests.append(True)
        return raw

    bridge.generate_json = generate
    clip.update(video_title_for_youtube_short="Why Coffee Tastes Bitter",
                viral_hook_text="Stop Blaming Your Coffee Beans")
    first = editorial.plan_clip(str(path), clip, data, 0, 30, allow_broll=True)
    assert first["status"] == "planned"
    assert first["chosen_hook"] == "Boiling Water Made My Coffee Bitter"
    assert "cues" not in first
    clip.update(editorial=first, viral_hook_text=first["chosen_hook"],
                video_title_for_youtube_short=first["chosen_title"])
    second = editorial.plan_clip(str(path), clip, data, 0, 30, allow_broll=True)
    assert second["status"] == "planned" and second["cached"] is True
    assert len(requests) == 1


def test_legacy_visual_fields_cannot_authorize_automatic_images():
    raw = response()
    raw.update(cues=[{"word_id": 6, "search_query": "unrequested image"}],
               broll_reason="Old cached automatic visual plan")
    result = validate(raw, allow_broll=True)
    assert "cues" not in result and "broll_reason" not in result
    assert result["chosen_hook"] == raw["chosen_hook"]


def test_evidence_words_keep_real_boundaries_and_ignore_invalid_times():
    data = {"segments": [{"words": [
        {"word": "edge", "start": 99.8, "end": 100.2},
        {"word": "bottle", "start": 104.25, "end": 104.6},
        {"word": "invalid", "start": float("nan"), "end": 105},
        {"word": "backwards", "start": 106, "end": 105},
        {"word": "after", "start": 131, "end": 132},
    ]}]}
    words = editorial.clip_words(data, 100, 130)
    assert [word["text"] for word in words] == ["edge", "bottle"]
    assert words[0]["start"] == 0 and words[0]["end"] == pytest.approx(.2)
    assert words[1]["start"] == 4.25 and words[1]["timing"] == "word"


def test_segment_only_evidence_remains_labeled_as_segment_timing():
    data = {"segments": [{"text": "A steel bottle.", "start": 104, "end": 108}]}
    assert editorial.clip_words(data, 100, 130) == [
        {"id": 0, "text": "A steel bottle.", "start": 4, "end": 8, "timing": "segment"}]
    assert editorial.clip_words(None, 100, 130) == []
