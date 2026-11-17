"""Campaigns follow original clip identities after filtering/out-of-order arrival."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from campaign_routes import CampaignAssignment, make_campaign_router
from campaigns import Campaigns


def _assign(store, clips):
    router = make_campaign_router(store, {'job': {'status': 'completed', 'result': {'clips': clips}}}, False)
    return next(route.endpoint for route in router.routes if route.path.endswith('/assign'))


def test_existing_second_clip_can_be_assigned_but_missing_first_cannot(tmp_path):
    store = Campaigns(tmp_path / 'campaigns.json')
    category = store.create('Review')['id']
    assign = _assign(store, [{'index': 1}])
    result = assign(CampaignAssignment(job_id='job', clip_index=1, campaign_id=category))
    assert result['clips'] == {'job/clip/1': category}
    with pytest.raises(HTTPException) as error:
        assign(CampaignAssignment(job_id='job', clip_index=0, campaign_id=category))
    assert error.value.status_code == 404


def test_whole_project_assignment_uses_actual_indices_and_preserves_tombstones(tmp_path):
    store = Campaigns(tmp_path / 'campaigns.json')
    category = store.create('Review')['id']
    assign = _assign(store, [{'index': 3}, {'index': 0, 'deleted': True}, {'index': 1}])
    result = assign(CampaignAssignment(job_id='job', campaign_id=category))
    assert result['clips'] == {'job/project': category, 'job/clip/1': category, 'job/clip/3': category}


def test_out_of_order_arrivals_inherit_once_and_manual_unassignment_persists(tmp_path):
    store = Campaigns(tmp_path / 'campaigns.json')
    category = store.create('Review')['id']
    store.assign(['job/project'], category)
    store.initialize_shorts('job', 1, clip_indices=[2])
    assert 'job/clip/0' not in store.snapshot()['clips']
    assert store.snapshot()['clips']['job/clip/2'] == category
    store.initialize_shorts('job', 2, clip_indices=[0, 2])
    assert store.snapshot()['clips']['job/clip/0'] == category
    store.assign(['job/clip/0'], None)
    store.initialize_shorts('job', 3, clip_indices=[0, 2, 3])
    assert 'job/clip/0' not in store.snapshot()['clips']
    assert 'job/clip/1' not in store.snapshot()['clips']
    assert store.snapshot()['clips']['job/clip/3'] == category


def test_app_initialization_passes_stable_indices(monkeypatch):
    import app

    calls = []
    monkeypatch.setattr(app, 'LOCAL_CAMPAIGNS', SimpleNamespace(
        initialize_shorts=lambda *args, **kwargs: calls.append((args, kwargs))))
    app._initialize_clip_campaigns('job', [{'index': 2}, {'index': 0, 'deleted': True}])
    assert calls == [(('job', 2), {'clip_indices': [2]})]
