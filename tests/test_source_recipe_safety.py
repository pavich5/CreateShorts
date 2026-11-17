"""Exact required-source padding recipe must not replay its next word twice."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from clip_timing import write_render_timing
from source_recipe_safety import repeats_proven_padding
from test_ai_trim import trim_endpoint


def padded_clip():
    return {'start': 310.88, 'end': 342.78, 'duration_seconds': 32.125,
        'recipe': {'segments': [{'start': 310.88, 'end': 333.005}, {'start': 332.78, 'end': 342.78}],
                   'canonical_range': {'start': 310.88, 'end': 332.78}},
        'source_edits': [{'mode': 'append', 'source_moment': {'start': 332.78, 'end': 342.78,
            'evidence': {'kind': 'transcript_continuation', 'word_count': 31}}}]}


def test_exact_padded_recipe_is_guarded_but_explicit_clean_source_ranges_are_allowed(tmp_path):
    canonical = tmp_path / 'canonical.mp4'
    canonical.write_bytes(b'unit-test-render-identity')
    write_render_timing(canonical, 310.88, 332.78)
    clip = padded_clip()
    assert repeats_proven_padding(clip, clip['recipe']['segments'], canonical)
    clean = [{'start': 310.88, 'end': 332.78}, {'start': 332.78, 'end': 342.78}]
    assert not repeats_proven_padding(clip, clean, canonical)
    assert not repeats_proven_padding(clip, [{'start': 310.88, 'end': 342.78}], canonical)
    intentional = deepcopy(clip)
    intentional['source_edits'] = []
    assert not repeats_proven_padding(intentional, clip['recipe']['segments'], canonical)
    canonical.write_bytes(b'a different render with stale provenance')
    assert not repeats_proven_padding(clip, clip['recipe']['segments'], canonical)


@pytest.mark.parametrize('operation', ['rerender', 'reframe'])
def test_source_rebuild_rejects_before_render_metering_or_mutation(trim_endpoint, monkeypatch, operation):
    endpoint = trim_endpoint
    app = endpoint.app
    clip = padded_clip()
    endpoint.clip.update(clip)
    data = json.loads(endpoint.meta.read_text())
    data['shorts'][0].update(clip)
    endpoint.meta.write_text(json.dumps(data))
    canonical = endpoint.directory / (endpoint.meta.name.removesuffix('_metadata.json') + '_clip_1.mp4')
    canonical.write_bytes(b'unit-test-render-identity')
    write_render_timing(canonical, 310.88, 332.78)
    source = endpoint.directory / 'source.mp4'
    source.write_bytes(b'unit-test-source-identity')
    monkeypatch.setattr(app, '_locate_source', lambda _: str(source))
    monkeypatch.setattr(app, '_source_duration_seconds', lambda _: 1500)
    reservation = AsyncMock()
    monkeypatch.setattr(app, 'reserve_managed_action', reservation)
    monkeypatch.setattr(app.recut, 'perform_recut', lambda **kwargs: pytest.fail('Unsafe source rebuild must not run'))
    before = endpoint.meta.read_bytes(), deepcopy(endpoint.clip)
    with pytest.raises(app.HTTPException) as caught:
        if operation == 'rerender':
            request = app.RerenderRequest(job_id='trim-job', clip_index=0, segments=clip['recipe']['segments'])
            asyncio.run(app.rerender_clip(request, None))
        else:
            request = app.ReframeRequest(job_id='trim-job', clip_index=0, crop_overrides={'0': .5})
            asyncio.run(app.reframe_clip(request, None))
    assert caught.value.status_code == 409
    assert 'repeat speech' in caught.value.detail
    reservation.assert_not_awaited()
    assert (endpoint.meta.read_bytes(), endpoint.clip) == before
