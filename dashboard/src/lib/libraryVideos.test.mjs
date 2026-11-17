import test from 'node:test';
import assert from 'node:assert/strict';
import { uniqueLibraryVideos } from './libraryVideos.js';

test('a saved copy and its project appear as one editable video in All', () => {
    const saved = { local_file_id: 'export', source_key: 'job/clip/8', video_url: '/saved.mp4', saved: true, kind: 'saved' };
    const generated = { job_id: 'job', clip_index: 8, clip: { title: 'Cereal' }, video_url: '/current.mp4', kind: 'generated' };
    for (const rows of [[saved, generated], [generated, saved]]) {
        const [video] = uniqueLibraryVideos(rows);
        assert.equal(uniqueLibraryVideos(rows).length, 1);
        assert.equal(video.video_url, '/current.mp4');
        assert.equal(video.local_file_id, undefined);
        assert.equal(video.saved, true);
    }
    // Grouping does not remove or alter the exported file's own row.
    assert.equal(saved.video_url, '/saved.mp4');
    assert.equal(saved.kind, 'saved');
});

test('unrelated projects with the same title remain separate', () => {
    const rows = ['first', 'second'].map(job_id => ({ job_id, clip_index: 0, clip: { title: 'Same title' } }));
    assert.equal(uniqueLibraryVideos(rows).length, 2);
});

test('multiple exports share one source card and standalone files stay visible', () => {
    const rows = [
        { local_file_id: 'first', source_key: 'job/clip/0', saved: true },
        { local_file_id: 'second', source_key: 'job/clip/0', saved: true },
        { local_file_id: 'unrelated' },
    ];
    assert.equal(uniqueLibraryVideos(rows).length, 2);
    assert.equal(rows.length, 3);
});
