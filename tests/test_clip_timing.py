from copy import deepcopy

import pytest

from clip_timing import recover_generated_timing, write_render_timing


def make_legacy_render(tmp_path):
    video = tmp_path / 'clip.mp4'
    video.write_bytes(b'rendered')
    transcript = {'language': 'en', 'segments': [{'words': []}]}
    cues = []
    for index, start in enumerate((.5, 2.5, 4.5)):
        texts = (f'phrase{index}', 'is', 'here')
        for token, lo, hi in zip(texts, (start, start + .2, start + .5),
                                  (start + .2, start + .5, start + .7)):
            transcript['segments'][0]['words'].append({
                'word': ' ' + token, 'start': 100 + lo, 'end': 100 + hi})
        cues.append(f'{index + 1}\n00:00:0{start:.3f} --> 00:00:0{start + .7:.3f}\n'
                    + ' '.join(texts))
    srt = tmp_path / 'autosubs_123_unique.srt'
    srt.write_text('\n\n'.join(cues).replace('.', ','))
    clip = {'start': 10, 'end': 14}
    info = {'duration': 6.2, 'audio_duration': 6}
    return video, srt, clip, transcript, info


@pytest.fixture
def legacy_render(tmp_path):
    return make_legacy_render(tmp_path)


def recover(fixture, **changes):
    video, _, clip, transcript, info = fixture
    return recover_generated_timing(video, 'subtitled_123_hooked_122_clip.mp4',
        changes.get('clip', clip), changes.get('transcript', transcript), info)


def test_caption_evidence_recovers_the_rendered_range_without_mutation(legacy_render):
    before = deepcopy(legacy_render[2:])
    assert recover(legacy_render) == [{'start': 100, 'end': 106}]
    assert legacy_render[2:] == before


@pytest.mark.parametrize('field,value', [
    ('recipe', {'segments': [{'start': 10, 'end': 14}]}),
    ('ai_edit', {'operations': []}), ('caption_words', []),
    ('source_edits', [{'mode': 'insert'}]), ('broll', {'mode': 'manual'}),
])
def test_existing_edits_are_never_reinterpreted_as_a_generated_range(legacy_render, field, value):
    assert recover(legacy_render, clip={**legacy_render[2], field: value}) is None


def test_missing_or_ambiguous_caption_files_cannot_recover_timing(legacy_render):
    _, srt, *_ = legacy_render
    duplicate = srt.with_name('autosubs_123_other.srt')
    duplicate.write_text(srt.read_text())
    assert recover(legacy_render) is None
    duplicate.unlink()
    srt.unlink()
    assert recover(legacy_render) is None


def test_caption_evidence_must_agree_across_the_whole_video(legacy_render):
    transcript = deepcopy(legacy_render[3])
    for word in transcript['segments'][0]['words'][-3:]:
        word['start'] += 1
        word['end'] += 1
    assert recover(legacy_render, transcript=transcript) is None


def test_repeated_source_phrases_do_not_choose_an_arbitrary_source_range(legacy_render):
    transcript = deepcopy(legacy_render[3])
    transcript['segments'][0]['words'] += [
        {**word, 'start': word['start'] + 100, 'end': word['end'] + 100}
        for word in transcript['segments'][0]['words']]
    assert recover(legacy_render, transcript=transcript) is None


@pytest.mark.parametrize('karaoke', [False, True])
def test_ass_only_captions_recover_the_source_range(legacy_render, karaoke):
    _, srt, _, transcript, _ = legacy_render
    ass = srt.with_suffix('.ass')
    srt.unlink()
    stamp = lambda value: f'0:00:{value:05.2f}'
    rows = []
    words = transcript['segments'][0]['words']
    if karaoke:
        for word in words:
            # The inactive words are deliberately not part of the source.
            text = (r'PREVIOUS {\c&H00E5FF&\fscx90\t(0,110,\fscx108)}'
                    + word['word'].strip().upper() + r'{\r} {\alpha&HFF&}FUTURE{\r}')
            rows.append(f"Dialogue: 0,{stamp(word['start'] - 100)},{stamp(word['end'] - 100)},Default,,0,0,0,,{text}")
    else:
        for index in range(0, len(words), 3):
            phrase = words[index:index + 3]
            text = ' '.join(word['word'].strip() for word in phrase)
            rows.append(f"Dialogue: 0,{stamp(phrase[0]['start'] - 100)},{stamp(phrase[-1]['end'] - 100)},Default,,0,0,0,,{text}")
    ass.write_text('[Events]\n' + '\n'.join(rows))
    assert recover(legacy_render) == [{'start': 100, 'end': 106}]


def test_render_provenance_recovers_without_captions_or_duration_mismatch(legacy_render):
    video, srt, clip, _, info = legacy_render
    srt.unlink()
    # An overwritten selection may even have the same duration as this render.
    clip.update(start=10, end=16.2)
    write_render_timing(video, 100, 106)
    assert recover_generated_timing(video, 'clip.mp4', clip, {}, info) == [{'start': 100, 'end': 106}]


def test_overwritten_render_cannot_reuse_previous_source_provenance(legacy_render):
    video, srt, *_ = legacy_render
    srt.unlink()
    write_render_timing(video, 100, 106)
    video.write_bytes(b'a different generation')
    assert recover(legacy_render) is None
