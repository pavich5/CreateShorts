import test from 'node:test';
import assert from 'node:assert/strict';
import { hookEditorSettings } from './hookSettings.js';

test('saved hook settings override unrelated last-used preferences after reopening', () => {
    const preferences = { pos: 'top', size: 'M', style: 'pill', font: 'montserrat', entranceAnimation: 'spring' };
    for (let iteration = 0; iteration < 10; iteration++) {
        const hook = { text: `Hook ${iteration}`, position: 'bottom', size: 'L', style: 'red', font: 'serif', duration_seconds: 3 + iteration / 10 };
        const settings = hookEditorSettings(JSON.parse(JSON.stringify(hook)), 'Old generated hook', preferences, 12, true);
        assert.deepEqual(settings, { text: hook.text, position: 'bottom', size: 'L', style: 'red', font: 'serif', displayDuration: hook.duration_seconds, entranceAnimation: 'none' });
    }
});

test('whole-clip hooks restore their real display duration and new hooks use preferences', () => {
    assert.equal(hookEditorSettings({ text: 'Full clip', duration_seconds: null }, '', {}, 22.125, true).displayDuration, 22.125);
    const settings = hookEditorSettings(null, 'Suggested text', { style: 'dark', pos: 'center' }, 1);
    assert.equal(settings.text, 'Suggested text');
    assert.equal(settings.style, 'dark');
    assert.equal(settings.position, 'center');
    assert.equal(settings.displayDuration, 1);
});
