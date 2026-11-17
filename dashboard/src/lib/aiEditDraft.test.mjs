import test from 'node:test';
import assert from 'node:assert/strict';
import { aiEditDraft } from './aiEditControls.js';

test('trim drafts compare actual ranges, not playback and preview state', () => {
    const saved = { trimStart: 0, trimEnd: 5, currentTime: 0, showOriginal: false };
    assert.deepEqual(aiEditDraft('trim', saved), aiEditDraft('trim', { ...saved, currentTime: 3, showOriginal: true }));
    assert.notDeepEqual(aiEditDraft('trim', saved), aiEditDraft('trim', { ...saved, trimStart: 1 }));
    const afterSave = { ...saved, trimStart: 0, trimEnd: 3 };
    assert.deepEqual(aiEditDraft('trim', afterSave), { start: 0, end: 3 });
});

test('unused range values and submitted prompts do not leave a dirty draft', () => {
    const saved = { instructions: '', rangeMode: false, rangeStart: 0, rangeEnd: 10 };
    assert.deepEqual(aiEditDraft('ai', saved), aiEditDraft('ai', { ...saved, rangeEnd: 3 }));
    assert.deepEqual(aiEditDraft('ai', { ...saved, instructions: '  ' }), { instructions: '', range: null });
    assert.notDeepEqual(aiEditDraft('ai', saved), aiEditDraft('ai', { ...saved, instructions: 'Make this brighter' }));
});

test('removing a section is a separate saved trim action', () => {
    const values = { trimStart: 13, trimEnd: 16, trimMode: 'remove' };
    assert.deepEqual(aiEditDraft('trim', values), { start: 13, end: 16, mode: 'remove' });
    assert.notDeepEqual(aiEditDraft('trim', values), aiEditDraft('trim', { ...values, trimMode: 'keep' }));
});
