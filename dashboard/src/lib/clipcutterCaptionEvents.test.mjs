import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { clipcutterCaptionEvents } from './clipcutterCaptionEvents.js';

const fixtures = JSON.parse(readFileSync(new URL('./fixtures/clipcutterCaptionEvents.json', import.meta.url), 'utf8'));

for (const fixture of fixtures) {
    test(`ClipCutter preview matches actual backend events with ${fixture.maxWords} words per phrase`, () => {
        assert.deepEqual(clipcutterCaptionEvents(fixture.captions, fixture.maxWords), fixture.events);
    });
}

test('utterance boundaries remain phrase boundaries even across short pauses', () => {
    const events = clipcutterCaptionEvents([
        { text: 'First', startMs: 0, endMs: 200, utteranceId: 0 },
        { text: 'Second', startMs: 250, endMs: 450, utteranceId: 1 },
    ]);
    assert.deepEqual(events.map(event => event.text), ['First', 'Second']);
});
