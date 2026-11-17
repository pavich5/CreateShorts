"""A running generator cannot silently overwrite a clip edit it accepted."""
from copy import deepcopy
import asyncio

import httpx
import pytest

from test_ai_trim import trim_endpoint


@pytest.mark.parametrize('status', ['queued', 'processing'])
@pytest.mark.parametrize('method,path,payload', [
    ('POST', '/api/edit/trim', {'job_id': 'trim-job', 'start': 1, 'end': 5}),
    ('POST', '/api/edit/ai', {'job_id': 'trim-job', 'instructions': 'Brighten it'}),
    ('POST', '/api/hook', {'job_id': 'trim-job', 'text': 'Updated hook'}),
    ('POST', '/api/subtitle', {'job_id': 'trim-job'}),
    ('POST', '/api/subtitle/remove', {'job_id': 'trim-job'}),
    ('PUT', '/api/clip/trim-job/0/broll', {'placements': []}),
])
def test_mutations_wait_for_generation_but_transcript_preview_remains_available(
        trim_endpoint, status, method, path, payload):
    endpoint = trim_endpoint
    endpoint.job['status'] = status
    before = endpoint.meta.read_bytes(), deepcopy(endpoint.clip)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=endpoint.app.app), base_url='http://test') as client:
            response = await client.request(method, path, json=payload)
            assert response.status_code == 409, response.text
            assert 'generation finishes' in response.json()['detail']
            preview = await client.get('/api/clip/trim-job/0/transcript')
            assert preview.status_code == 200, preview.text
    asyncio.run(run())
    assert (endpoint.meta.read_bytes(), endpoint.clip) == before


def test_media_body_cannot_override_processing_path_identity(trim_endpoint):
    endpoint = trim_endpoint
    endpoint.job['status'] = 'processing'
    endpoint.app.jobs['completed-job'] = {'status': 'completed', 'result': {'clips': []}}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=endpoint.app.app), base_url='http://test') as client:
            response = await client.put('/api/clip/trim-job/0/broll', json={
                'job_id': 'completed-job', 'clip_index': 4, 'placements': []})
            assert response.status_code == 409, response.text
            assert 'generation finishes' in response.json()['detail']
    asyncio.run(run())
