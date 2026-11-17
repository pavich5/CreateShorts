"""The frontend timing fixture must remain the actual backend algorithm output."""
import json
from pathlib import Path

from clipcutter_captions import caption_events


def test_preview_fixture_matches_backend_caption_events():
    fixtures = json.loads(Path('dashboard/src/lib/fixtures/clipcutterCaptionEvents.json').read_text())
    for fixture in fixtures:
        assert caption_events(fixture['utterances'], fixture['duration'], fixture['maxWords']) == fixture['events']
