import test from 'node:test';
import assert from 'node:assert/strict';
import { clipDurationSeconds, formatClipDuration } from './clipDuration.js';

test('cards use the rendered duration instead of a planned source range', () => {
    const clip = { start: 0, end: 48.4, duration_seconds: 29.5295 };
    assert.equal(clipDurationSeconds(clip), 29.5295);
    assert.equal(formatClipDuration(clip), '00:29');
    assert.equal(formatClipDuration({ start: 0, end: 48.4 }, 29.5295), '00:29');
});

test('loaded video timing wins over stale metadata and recipes', () => {
    const clip = { start: 0, end: 48.4, duration_seconds: 48.4, recipe: { segments: [{ start: 0, end: 48.4 }] } };
    assert.equal(clipDurationSeconds(clip, 29.5295), 29.5295);
    assert.equal(clipDurationSeconds({ ...clip, duration_seconds: 29.5295 }), 29.5295);
});

test('recipes and original ranges remain fallbacks before a file is available', () => {
    assert.equal(clipDurationSeconds({ start: 10, end: 100, recipe: { segments: [{ start: 10, end: 13 }, { start: 97, end: 100 }] } }), 6);
    assert.equal(clipDurationSeconds({ start: 10, end: 20 }), 10);
    assert.equal(formatClipDuration({}), null);
});
