import test from 'node:test';
import assert from 'node:assert/strict';
import { savedOneWordPreference } from './captionPreferences.js';

test('one-word grouping survives saving and reopening a grouped preset', () => {
    const preset = { oneWord: false };
    const savedStyle = JSON.parse(JSON.stringify({ max_chars: 1 }));
    assert.equal(savedOneWordPreference(savedStyle, preset), true);
});

test('an explicit grouped save overrides a one-word preset default', () => {
    assert.equal(savedOneWordPreference({ max_chars: 16 }, { oneWord: true }), false);
    assert.equal(savedOneWordPreference(null, { oneWord: true }), true);
    assert.equal(savedOneWordPreference({}, { oneWord: false }), false);
});
