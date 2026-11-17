import test from 'node:test';
import assert from 'node:assert/strict';
import { savedCaptionPreview, savedHookPreview } from './editorPreviewLayers.js';

test('saved hooks retain their text, appearance, and clipped display duration', () => {
    assert.deepEqual(savedHookPreview({ text: 'Opening line', style: 'dark', font: 'anton', position: 'center', size: 'L', duration_seconds: 8 }, 4), {
        text: 'Opening line', style: 'dark', font: 'anton', position: 'center', size: 'L',
        entranceAnimation: 'none', exitFade: false, displayDurationSec: 4,
    });
    assert.equal(savedHookPreview({ text: 'Full clip', duration_seconds: null }, 12).displayDurationSec, 12);
    assert.equal(savedHookPreview(null, 12), null);
});

test('saved automatic hooks retain resolved export geometry and have no synthetic exit fade', () => {
    const hook = savedHookPreview({ text: 'Resolved', y: .56, scale: .8, height: .12 }, 10);
    assert.equal(hook.y, .56);
    assert.equal(hook.scale, .8);
    assert.equal(hook.height, .12);
    assert.equal(hook.exitFade, false);
});

test('saved captions preserve current word timing and user-selected styling', () => {
    const words = [{ text: 'Edited', startMs: 200, endMs: 600 }];
    const config = savedCaptionPreview(words, {
        style: 'karaoke', effect: 'glow', font_name: 'Georgia', font_size: 34,
        position: 'middle', highlight_color: '#00BBFF', font_color: '#FF0000',
        border_width: 2, base_opacity: .6, uppercase: false, max_chars: 9, max_duration: 2,
    });
    assert.equal(config.captions, words);
    assert.equal(config.position, 'middle');
    assert.equal(config.maxChars, 9);
    assert.equal(config.maxDurationMs, 2000);
    assert.equal(config.style.fontFamily, 'Georgia');
    assert.equal(config.style.animation, 'word-highlight');
    assert.equal(config.style.baseOpacity, .6);
    assert.equal(config.style.uppercase, false);
    assert.equal(config.style.borderWidth, 3);
});

test('empty captions create no overlay and ClipCutter keeps its native font units', () => {
    assert.equal(savedCaptionPreview([], {}), null);
    assert.equal(savedCaptionPreview([{ text: 'hidden', startMs: 0, endMs: 300 }], {
        effect: 'clipcutter:none',
    }), null);
    const config = savedCaptionPreview([{ text: 'word', startMs: 0, endMs: 300 }], {
        effect: 'clipcutter:mozi', font_size: 72, border_width: 0, bg_opacity: 0,
    });
    assert.equal(config.style.fontSize, 72);
    assert.equal(config.style.borderWidth, 0);
    assert.equal(config.style.bgOpacity, 0);
    assert.equal(config.captionEffect, 'clipcutter:mozi');
    assert.equal(config.wordsPerCaption, 4);
});

test('saved ClipCutter captions retain raw outline units, one-word grouping, and split placement', () => {
    const layouts = [{ start: 0, end: 2, layout: 'split' }];
    const config = savedCaptionPreview([{ text: 'word', startMs: 0, endMs: 300, utteranceId: 1 }], {
        effect: 'clipcutter:marker_sweep', font_size: 72, border_width: 2, max_chars: 1,
    }, layouts);
    assert.equal(config.style.borderWidth, 2);
    assert.equal(config.wordsPerCaption, 1);
    assert.equal(config.layoutRanges, layouts);
    assert.equal(config.captions[0].utteranceId, 1);
});
