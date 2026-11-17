"""Real Whisper alignment shapes must not silently remove recognized words."""
from copy import deepcopy

import pytest

from clipcutter_captions import generate_clipcutter_ass
from subtitles import generate_ass, generate_srt, merge_untimed_caption_words


def aligned_words():
    # This shape occurred in the mandatory source at 1530.58 seconds.
    return [
        {'word': ' a', 'start': 1530.58, 'end': 1530.58},
        {'word': ' lot', 'start': 1530.58, 'end': 1530.58},
        {'word': ' of', 'start': 1530.58, 'end': 1530.64},
    ]


@pytest.mark.parametrize('alignment_padding', [0, .001])
def test_zero_duration_words_share_the_next_real_interval_without_mutation(alignment_padding):
    words = aligned_words()
    for word in words[:2]:
        word['end'] += alignment_padding  # legacy virtual_transcript repair
    before = deepcopy(words)
    result = merge_untimed_caption_words(words)
    assert len(result) == 1
    assert result[0]['word'].split() == ['a', 'lot', 'of']
    assert (result[0]['start'], result[0]['end']) == (1530.58, 1530.64)
    assert words == before


@pytest.mark.parametrize('renderer', [generate_srt, generate_ass, generate_clipcutter_ass])
def test_caption_renderers_retain_recognized_zero_duration_words(tmp_path, renderer):
    output = tmp_path / 'captions.ass'
    words = aligned_words()
    transcript = {'segments': [{'start': 1530.58, 'end': 1530.64, 'text': 'a lot of', 'words': words}]}
    options = {'preset': 'mozi', 'words_per_caption': 1} if renderer is generate_clipcutter_ass else {'max_chars': 1}
    assert renderer(transcript, 1530.5, 1530.7, str(output), **options)
    content = output.read_text(encoding='utf-8-sig').lower()
    assert 'a lot of' in content


def test_untimed_final_word_uses_touching_previous_interval():
    assert merge_untimed_caption_words([
        {'word': ' I', 'start': 232.16, 'end': 232.6},
        {'word': ' mean,', 'start': 232.6, 'end': 232.6},
    ]) == [{'word': ' I mean,', 'start': 232.16, 'end': 232.6}]
