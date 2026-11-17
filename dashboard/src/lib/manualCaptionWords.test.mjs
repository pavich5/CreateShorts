import test from 'node:test';
import assert from 'node:assert/strict';
import { retimeCaptionText } from './manualCaptionWords.js';

test('manual captions fill the actual clip with positive integer word times', () => {
    assert.deepEqual(retimeCaptionText(' Hello   new\nworld ', 1.5), [
        { text: 'Hello', startMs: 0, endMs: 500 },
        { text: 'new', startMs: 500, endMs: 1000 },
        { text: 'world', startMs: 1000, endMs: 1500 },
    ]);
});

test('edited transcript text stays in the original spoken window', () => {
    const original = [{ text: 'old', startMs: 400, endMs: 600 }, { text: 'text', startMs: 700, endMs: 1000 }];
    assert.deepEqual(retimeCaptionText('new text', 2, original), [
        { text: 'new', startMs: 400, endMs: 600 },
        { text: 'text', startMs: 700, endMs: 1000 },
    ]);
});

test('correcting words preserves recognition timing and pauses across repeated saves', () => {
    let original = [
        { text: 'Make', startMs: 250, endMs: 410 },
        { text: 'many', startMs: 420, endMs: 900 },
        { text: 'tomorrow', startMs: 2200, endMs: 2600 },
    ];
    for (let iteration = 0; iteration < 10; iteration++) {
        original = retimeCaptionText(`Make money${iteration} tomorrow`, 3, original);
        assert.deepEqual(original.map(({ startMs, endMs }) => [startMs, endMs]),
            [[250, 410], [420, 900], [2200, 2600]]);
    }
});

test('corrections preserve grouped untimed ASR words and their speech gaps', () => {
    let original = [
        { text: 'a lot of', startMs: 250, endMs: 410, utteranceId: 0 },
        { text: 'work', startMs: 2200, endMs: 2600, utteranceId: 1 },
    ];
    for (let iteration = 0; iteration < 10; iteration++) {
        original = retimeCaptionText(`a LOT of work${iteration}`, 3, original);
        assert.deepEqual(original, [
            { text: 'a LOT of', startMs: 250, endMs: 410, utteranceId: 0 },
            { text: `work${iteration}`, startMs: 2200, endMs: 2600, utteranceId: 1 },
        ]);
    }
});

test('timestamps stay inside the clip after trimming', () => {
    const captions = retimeCaptionText('one two', 1, [{ startMs: -200, endMs: 3000 }]);
    assert.deepEqual(captions, [
        { text: 'one', startMs: 0, endMs: 500 },
        { text: 'two', startMs: 500, endMs: 1000 },
    ]);
});

test('empty, unknown duration, excessive, and impossible text cannot be submitted', () => {
    for (const duration of [0, NaN, Infinity, -1]) assert.deepEqual(retimeCaptionText('word', duration), []);
    assert.deepEqual(retimeCaptionText('   ', 2), []);
    assert.deepEqual(retimeCaptionText(Array(2001).fill('word').join(' '), 10), []);
    assert.deepEqual(retimeCaptionText('one two', .001), []);
});
