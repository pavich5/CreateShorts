"""MCP clip actions and displayed durations agree with actual rendered results."""
import pytest

import mcp_server as mcp


def test_filtered_mcp_results_keep_original_slots_and_actual_duration():
    summaries = mcp._clip_summaries("job", {"clips": [
        {"index": 2, "start": 100, "end": 150, "duration_seconds": 18.2,
         "video_url": "/videos/job/third.mp4"},
        {"index": 0, "deleted": True},
        {"index": 4, "start": 200, "end": 230, "duration_seconds": 9.5,
         "video_url": "/videos/job/fifth.mp4"},
    ]})
    assert [item["index"] for item in summaries] == [2, 4]
    assert [item["duration_seconds"] for item in summaries] == [18.2, 9.5]


@pytest.mark.parametrize("actual", [None, 0, -1, float("nan"), float("inf"), True])
def test_mcp_falls_back_to_ordered_recipe_before_flat_source_bounds(actual):
    summaries = mcp._clip_summaries("job", {"clips": [{"duration_seconds": actual,
        "start": 100, "end": 204, "recipe": {"segments": [
            {"start": 200, "end": 204}, {"start": 100, "end": 103}]}}]})
    assert summaries[0]["duration_seconds"] == 7


def test_legacy_mcp_results_keep_positional_identity_and_source_duration():
    summaries = mcp._clip_summaries("job", {"clips": [
        {"start": 3, "end": 8}, {"index": None, "start": 20, "end": 28.25}]})
    assert [item["index"] for item in summaries] == [0, 1]
    assert [item["duration_seconds"] for item in summaries] == [5, 8.2]
