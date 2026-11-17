"""Opt-in real Codex/LLM interpretation tests, never mocked.

Use OPENSHORTS_AI_INTEGRATION_CLIP for a generated 9:16 clip and
OPENSHORTS_REAL_TEST_VIDEO for the required retained Alex Hormozi source.
The default suite skips these account-backed requests. Rendering/source
preservation are tested separately; these cases verify the actual planner.
"""
import json
import os
from pathlib import Path

import pytest

import ai_clip_edit as ai


CLIP = os.environ.get("OPENSHORTS_AI_INTEGRATION_CLIP")
SOURCE = os.environ.get("OPENSHORTS_REAL_TEST_VIDEO")
pytestmark = pytest.mark.skipif(not (CLIP and SOURCE), reason="Set real clip/source paths to run external AI planner integration")

REQUESTS = [
    ("Extend this video by adding more story.", "extend"),
    ("Add another 10 seconds of relevant content.", "extend"),
    ("Continue the existing narrative.", "extend"),
    ("Add another scene after the current ending.", "extend"),
    ("Make the video longer without removing existing content.", "extend"),
    ("Improve the hook.", "hook"),
    ("Replace the current hook with a stronger one.", "hook"),
    ("Shorten the introduction.", "trim_or_explain"),
    ("Remove the unnecessary section.", "explain"),
    ("Add more explanation to the ending.", "extend"),
    ("Change the captions.", "caption"),
    ("Make the story flow better.", "explain"),
]


@pytest.mark.parametrize("instructions,expectation", REQUESTS)
def test_real_planner_interprets_requested_edit(instructions, expectation):
    assert Path(SOURCE).is_file(), "The mandatory real source is unavailable"
    assert Path(CLIP).is_file(), "The real generated clip is unavailable"
    info = ai.probe(CLIP)
    assert info["width"] * 16 == info["height"] * 9
    result = {"instructions": instructions, "expectation": expectation}
    try:
        plan = ai.plan_edit(instructions, info, source_available=True)
    except ValueError as exc:
        result.update(status="explicit_limitation", explanation=str(exc))
        assert expectation != "extend", str(exc)
        if expectation in {"hook", "caption"}:
            assert expectation in str(exc).lower()
    else:
        result.update(status="planned", plan=plan.model_dump())
        if expectation == "extend":
            assert len(plan.operations) == 1
            assert plan.operations[0].type == "source_continue"
            assert plan.operations[0].start == info["duration"]
            assert plan.operations[0].end > info["duration"]
        elif expectation == "trim_or_explain":
            assert len(plan.operations) == 1
            assert plan.operations[0].type == "trim"
            assert plan.operations[0].start > 0
        else:
            pytest.fail("The planner must explain unsupported or ambiguous text edits instead of applying unrelated video effects")
    finally:
        output = os.environ.get("OPENSHORTS_AI_INTEGRATION_REPORT")
        if output:
            with Path(output).open("a") as handle:
                handle.write(json.dumps(result) + "\n")
    print(json.dumps(result))
