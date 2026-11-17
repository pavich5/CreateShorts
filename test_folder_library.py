import tempfile
import unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from campaigns import Campaigns
from folder_library import FolderLibrary, make_folder_router

class FolderLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.clips=self.root/'Clips'
        self.source=self.clips/'Podcast'/'Generated'/'batch'/'one.mp4'
        self.source.parent.mkdir(parents=True);self.source.write_bytes(b'original-video')
        self.saved=self.clips/'Podcast'/'Saved'/'Named clip.mp4'
        self.saved.parent.mkdir(parents=True);self.saved.write_bytes(b'previous-saved-version')
        self.store=Campaigns(self.root/'campaigns.json');self.library=FolderLibrary(self.clips,self.store)
        app=FastAPI();app.include_router(make_folder_router(self.library,False));self.client=TestClient(app)

    def test_lists_generated_and_saved_with_existing_campaign(self):
        rows=self.library.scan();self.assertEqual(len(rows),2)
        self.assertEqual({r['kind'] for r in rows},{'generated','saved'})
        self.assertEqual(self.store.snapshot()['campaigns'][0]['name'],'Podcast')
        self.assertEqual(self.source.read_bytes(),b'original-video')

    def test_unassigned_membership_survives_rescan_and_restart(self):
        row=next(r for r in self.library.scan() if not r['saved'])
        self.store.assign(['file/'+row['local_file_id']],None)
        FolderLibrary(self.clips,Campaigns(self.store.path)).scan()
        self.assertNotIn('file/'+row['local_file_id'],self.store.snapshot()['clips'])
        self.assertTrue(self.source.exists())

    def test_save_is_verified_idempotent_and_preserves_originals(self):
        self.library.scan();cid=self.store.snapshot()['campaigns'][0]['id']
        rel=self.library.save_copy(self.source,'My version.mp4',cid,'source-one')
        self.assertEqual(rel,'Podcast/Saved/My version.mp4')
        self.assertEqual((self.clips/rel).read_bytes(),self.source.read_bytes())
        self.assertEqual(self.library.save_copy(self.source,'My version.mp4',cid),rel)
        self.assertEqual(self.store.snapshot()['saved_sources']['source-one'],rel)
        self.assertEqual(self.saved.read_bytes(),b'previous-saved-version')
        self.assertFalse(self.source.is_symlink())
        with self.assertRaises(ValueError):self.library.save_copy(self.source,'Named clip.mp4',cid)
        self.assertEqual(self.saved.read_bytes(),b'previous-saved-version')

    def test_saved_versions_keep_the_editable_clip_reference(self):
        self.library.scan()
        first=self.library.save_copy(self.source,'First.mp4',source_key='job-one/clip/0')
        row=next(r for r in self.library.scan() if r['relative_path']==first)
        self.assertEqual(row['source_key'],'job-one/clip/0')
        second=self.library.save_copy(self.clips/first,'Second.mp4',source_key='file/'+row['local_file_id'])
        rows=FolderLibrary(self.clips,Campaigns(self.store.path)).scan()
        self.assertEqual({r['source_key'] for r in rows if r['relative_path'] in (first,second)},{'job-one/clip/0'})

    def test_saving_uses_generation_campaign_when_browser_mapping_is_stale(self):
        self.library.scan()
        category=self.store.create('New interview')
        self.store.assign(['pending-job/project'],category['id'])
        rel=self.library.save_copy(self.source,'New moment',source_key='pending-job/clip/0')
        self.assertEqual(rel,'New interview/Saved/New moment.mp4')
        row=next(r for r in self.library.scan() if r['relative_path']==rel)
        self.assertEqual(self.store.snapshot()['clips']['file/'+row['local_file_id']],category['id'])

    def test_path_escape_and_external_symlinks_are_rejected(self):
        outside=self.root/'private.mp4';outside.write_bytes(b'private')
        (self.clips/'escape.mp4').symlink_to(outside)
        self.assertEqual(len(self.library.scan()),2)
        for name in ('../outside.mp4','bad/name.mp4','bad:name.mp4'):
            with self.assertRaises(ValueError):self.library.save_copy(self.source,name)
        with self.assertRaises(ValueError):self.library.locate('../../private.mp4')

    def test_media_ranges_and_upload_save(self):
        row=self.client.get('/api/local/library').json()['videos'][0]
        response=self.client.get(row['video_url'],headers={'Range':'bytes=0-3'})
        self.assertEqual(response.status_code,206);self.assertEqual(len(response.content),4)
        response=self.client.post('/api/local/library/save',params={'name':'From browser.mp4'},content=b'rendered-output')
        self.assertEqual(response.status_code,200)
        self.assertEqual((self.clips/'Unassigned/Saved/From browser.mp4').read_bytes(),b'rendered-output')

    def test_cloud_guard(self):
        app=FastAPI();app.include_router(make_folder_router(self.library,True));client=TestClient(app)
        self.assertEqual(client.get('/api/local/library').status_code,404)
        self.assertEqual(client.get('/api/local/library/media/any').status_code,404)
        self.assertEqual(client.post('/api/local/library/save?name=x.mp4',content=b'x').status_code,404)

if __name__=='__main__':unittest.main()
