import tempfile
import unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from campaigns import Campaigns
from campaign_routes import make_campaign_router

class LocalCampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.video = self.root / 'video.mp4'
        self.video.write_bytes(b'original media preserved')
        self.store = Campaigns(self.root / 'campaigns.json')
        self.jobs = {'job1': {'status': 'completed', 'result': {'clips': [
            {'video_url': '/videos/old.mp4'}, {'video_url': '/videos/second.mp4'}]}}}
        self.a = self.store.create('Podcast')
        self.b = self.store.create('Interviews')
        app = FastAPI()
        app.include_router(make_campaign_router(self.store, self.jobs, False))
        self.client = TestClient(app)

    def assign(self, campaign, index=None):
        return self.client.post('/api/local/campaigns/assign', json={
            'job_id': 'job1', 'clip_index': index, 'campaign_id': campaign})

    def test_create_normalizes_and_deduplicates(self):
        response = self.client.post('/api/local/campaigns', json={'name':'  PODCAST  '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['campaign']['id'], self.a['id'])
        self.assertEqual(self.client.post('/api/local/campaigns', json={'name':'   '}).status_code,400)

    def test_rename_persists_and_preserves_membership(self):
        self.assign(self.a['id'])
        before = self.store.snapshot()['clips']
        response = self.client.patch('/api/local/campaigns/' + self.a['id'], json={'name': '  Podcast   highlights  '})
        self.assertEqual(response.status_code, 200)
        restored = Campaigns(self.store.path).snapshot()
        self.assertEqual(restored['campaigns'][0], {'id': self.a['id'], 'name': 'Podcast highlights'})
        self.assertEqual(restored['clips'], before)
        self.assertEqual(response.json(), restored)
        self.assertEqual(self.video.read_bytes(), b'original media preserved')

    def test_rename_rejects_invalid_and_duplicate_names_without_writing(self):
        before = self.store.snapshot()
        for name in ['   ', 'x' * 81, ' INTERVIEWS ']:
            with self.subTest(name=name):
                response = self.client.patch('/api/local/campaigns/' + self.a['id'], json={'name': name})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.client.patch('/api/local/campaigns/missing', json={'name': 'New'}).status_code, 404)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.client.patch('/api/local/campaigns/' + self.a['id'], json={'name': 'PODCAST'}).status_code, 200)

    def test_remove_short_preserves_source_other_shorts_and_media(self):
        self.assertEqual(self.assign(self.a['id']).status_code,200)
        self.assertEqual(self.assign(None,0).status_code,200)
        mapping = Campaigns(self.store.path).snapshot()['clips']
        self.assertNotIn('job1/clip/0',mapping)
        self.assertEqual(mapping['job1/clip/1'],self.a['id'])
        self.assertEqual(mapping['job1/project'],self.a['id'])
        self.assertEqual(self.video.read_bytes(),b'original media preserved')

    def test_recuts_keep_membership_and_remove_video_keeps_other_campaign(self):
        self.assign(self.a['id'])
        self.assign(self.b['id'],1)
        self.jobs['job1']['result']['clips'][1]['video_url']='/videos/new-recut.mp4'
        self.assertEqual(Campaigns(self.store.path).snapshot()['clips']['job1/clip/1'],self.b['id'])
        self.assign(None)
        self.assertEqual(self.store.snapshot()['clips'],{'job1/clip/1':self.b['id']})
        self.assertTrue(self.video.exists())

    def test_reject_invalid_membership_without_writing(self):
        before=self.store.snapshot()
        self.assertEqual(self.assign('missing').status_code,400)
        self.assertEqual(self.assign(self.a['id'],10).status_code,404)
        self.assertEqual(self.assign(self.a['id'],-1).status_code,422)
        self.assertEqual(self.client.post('/api/local/campaigns/assign',json={'job_id':'../invalid','campaign_id':self.a['id']}).status_code,404)
        self.assertEqual(self.store.snapshot(),before)

    def test_delete_label_preserves_media(self):
        self.assign(self.a['id'])
        self.assertEqual(self.client.delete('/api/local/campaigns/'+self.a['id']).status_code,200)
        self.assertEqual(self.store.snapshot()['clips'],{})
        self.assertEqual(self.video.read_bytes(),b'original media preserved')

    def test_generation_category_inherits_once_and_survives_restart(self):
        self.store.assign(['new-job/project'],self.a['id'])
        self.store.initialize_shorts('new-job',3)
        restored=Campaigns(self.store.path)
        self.assertEqual(restored.snapshot()['clips']['new-job/clip/2'],self.a['id'])
        restored.assign(['new-job/clip/0'],None)
        restored.assign(['new-job/clip/1'],self.b['id'])
        restored.initialize_shorts('new-job',3)
        self.assertNotIn('new-job/clip/0',restored.snapshot()['clips'])
        self.assertEqual(restored.snapshot()['clips']['new-job/clip/1'],self.b['id'])

    def test_generation_inherits_for_partial_results_without_losing_manual_changes(self):
        self.store.assign(['stream-job/project'],self.a['id'])
        self.store.initialize_shorts('stream-job',0)
        self.store.initialize_shorts('stream-job',1)
        self.assertEqual(self.store.snapshot()['clips']['stream-job/clip/0'],self.a['id'])
        self.store.assign(['stream-job/clip/0'],None)
        self.store.initialize_shorts('stream-job',4)
        mapping=Campaigns(self.store.path).snapshot()['clips']
        self.assertNotIn('stream-job/clip/0',mapping)
        self.assertEqual([mapping['stream-job/clip/'+str(i)] for i in range(1,4)],[self.a['id']]*3)

    def test_cloud_routes_do_not_read_or_modify_local_store(self):
        app=FastAPI();app.include_router(make_campaign_router(self.store,self.jobs,True))
        client=TestClient(app);before=self.store.snapshot()
        self.assertEqual(client.get('/api/local/campaigns').status_code,404)
        self.assertEqual(client.post('/api/local/campaigns',json={'name':'Hidden'}).status_code,404)
        self.assertEqual(client.post('/api/local/campaigns/assign',json={'job_id':'job1','campaign_id':self.a['id']}).status_code,404)
        self.assertEqual(client.delete('/api/local/campaigns/'+self.a['id']).status_code,404)
        self.assertEqual(client.patch('/api/local/campaigns/'+self.a['id'],json={'name':'Hidden'}).status_code,404)
        self.assertEqual(self.store.snapshot(),before)

if __name__ == '__main__': unittest.main()
