import test from 'node:test';
import assert from 'node:assert/strict';
import { entranceStyle, mediaKind, mediaOffset, mediaSourceRange, mediaTimelineChange, mediaVolume, placementError, replayEffectsPreview, videoSourceRangeChange } from './mediaEditControls.js';

test('multiple images, videos and sounds can share the same time', () => {
    const image = { start: 0, end: 2 };
    const sound = { kind: 'audio', start: .5, end: 1.5, media_duration: 1 };
    assert.equal(placementError([image, sound, { kind: 'video', start: 2, end: 4, media_duration: 2 }], 4), '');
    assert.equal(placementError([image, sound, { kind: 'video', start: 1.5, end: 3 }], 4), '');
});

test('source offset must leave enough footage or audio to cover a placement', () => {
    const video = { kind: 'video', start: 2, end: 4, source_start: 1, media_duration: 3 };
    assert.equal(placementError([video], 4), '');
    assert.match(placementError([{ ...video, source_start: 1.1 }], 4), /too short/);
    assert.match(placementError([{ ...video, source_start: '' }], 4), /too short/);
    assert.match(placementError([{ ...video, start: '' }], 4), /Choose a start/);
});

test('entrance animations follow the playhead when scrubbing or replaying', () => {
    const item = { start: 1, end: 3, animation: 'fade', animation_duration: .4, opacity: .8 };
    assert.equal(entranceStyle(item, 1).opacity, 0);
    assert.ok(Math.abs(entranceStyle(item, 1.2).opacity - .4) < .00001);
    assert.equal(entranceStyle(item, 2).opacity, .8);
    assert.equal(entranceStyle({ ...item, animation: 'slide_up' }, 1).transform, 'translateY(25%)');
    assert.equal(entranceStyle({ ...item, animation: 'zoom_in' }, 2).transform, 'scale(1)');
    assert.equal(entranceStyle({ ...item, animation: 'zoom_in' }, 1).transform, 'scale(0.75)');
    assert.ok(Math.abs(entranceStyle({ ...item, animation_duration: 5 }, 3).opacity - .32) < .00001);
    assert.equal(entranceStyle({ ...item, animation_elapsed: .4 }, 1).opacity, .8);
});

test('legacy images and sound/video defaults remain compatible', () => {
    assert.equal(mediaKind({ image_url: '/old.png' }), 'image');
    assert.equal(mediaVolume({ kind: 'video' }), 0);
    assert.equal(mediaVolume({ kind: 'audio' }), 1);
    assert.equal(mediaOffset({ media_start: 1.5 }), 1.5);
});

test('a 40-second upload can use only source 10–13 at an independent reel time', () => {
    const original = { kind: 'video', media_duration: 40, source_start: 0, start: 2, end: 8 };
    const withIn = { ...original, ...videoSourceRangeChange(original, 'start', '10') };
    const selected = { ...withIn, ...videoSourceRangeChange(withIn, 'end', '13') };
    assert.deepEqual(mediaSourceRange(selected), { start: 10, end: 13 });
    assert.equal(selected.start, 2);
    assert.equal(selected.end, 5);
    assert.equal(placementError([selected], 8), '');
    const moved = { ...selected, ...mediaTimelineChange(selected, 'start', '4') };
    assert.equal(moved.end, 7);
    assert.deepEqual(mediaSourceRange(moved), { start: 10, end: 13 });
    const reopened = { kind: 'video', media_duration: 40, source_start: 10, start: 4, end: 7 };
    assert.deepEqual(mediaSourceRange(reopened), { start: 10, end: 13 });
});

test('invalid or oversized source ranges cannot silently save another section', () => {
    const video = { kind: 'video', media_duration: 40, source_start: 10, source_out: 13, start: 2, end: 5 };
    const empty = { ...video, ...videoSourceRangeChange(video, 'end', '') };
    assert.match(placementError([empty], 8), /source in and out/);
    assert.match(placementError([{ ...video, source_out: 41 }], 8), /source in and out/);
    const tooLong = { ...video, ...videoSourceRangeChange(video, 'end', 30) };
    assert.match(placementError([tooLong], 8), /within the clip/);
    const timelineTrimmed = { ...video, ...mediaTimelineChange(video, 'end', '4') };
    assert.deepEqual(mediaSourceRange(timelineTrimmed), { start: 10, end: 12 });
    assert.equal(placementError([timelineTrimmed], 8), '');
});

test('saved effects preview the complete composition and mute all sound only in range', () => {
    const batches = [[{ type: 'punch_in', start: 1, end: 3, strength: .15 }, { type: 'mute', start: 1, end: 2 }], [{ type: 'bw_moment', start: 0, end: 4 }]];
    const active = replayEffectsPreview(batches, 1.5);
    assert.equal(active.muted, true);
    assert.match(active.style.transform, /scale\(1.15\)/);
    assert.equal(active.style.filter, 'grayscale(1)');
    assert.equal(replayEffectsPreview(batches, 2).muted, false);
    assert.equal(replayEffectsPreview(batches, 4).style.filter, undefined);
    assert.equal(replayEffectsPreview(batches, 4).style.transform, undefined);
    assert.match(replayEffectsPreview([[{ type: 'zoom_pulse', start: 0, end: 2, strength: .1 }]], 1).style.transform, /scale\(1.1\)/);
    assert.match(replayEffectsPreview([[{ type: 'zoom_pulse', start: 0, end: 2, strength: .1, timeline_offset: 1 }]], 0).style.transform, /scale\(1.1\)/);
    assert.equal(replayEffectsPreview([[{ type: 'mute', start: 0, end: 1, timeline_offset: 1 }]], 0).muted, false);
});

test('added videos stay muted even when an older draft saved a volume', () => {
    assert.equal(mediaVolume({ kind: 'video', volume: 1 }), 0);
    assert.equal(mediaVolume({ kind: 'audio', volume: .7 }), .7);
});
