"""Recuts restore the requested source pictures before captions are applied."""
import pytest

import layout_ranges


def test_source_pictures_are_restored_after_stock_visuals(tmp_path, monkeypatch):
    app = pytest.importorskip('app')
    import broll_edit
    import source_visual_edit
    calls = []
    stock, reveal = tmp_path / 'stock.mp4', tmp_path / 'reveal.mp4'
    stock.write_bytes(b'stock')
    reveal.write_bytes(b'actual car')
    layout_ranges.write(str(reveal), [(2, 5, 'wide', (.3, .7))])
    def stock_editor(path, record, segments):
        calls.append(('stock', path))
        return str(stock), {'status': 'applied'}
    def source_editor(path, source, visuals, segments):
        calls.append(('source', path, source, visuals, segments))
        return str(reveal)
    monkeypatch.setattr(broll_edit, 'reapply_broll', stock_editor)
    monkeypatch.setattr(source_visual_edit, 'reapply_source_visuals', source_editor)
    cues = [{'source_start': 10, 'source_end': 13, 'picture_start': 50, 'picture_end': 53}]
    clip = {'broll': {'cues': [{'asset': 'cached.jpg'}]}, 'source_visuals': cues}
    segments, changes = [{'start': 8, 'end': 15}], {}
    editor = app._recut_broll_editor(clip, segments, changes, source_path='retained.mp4')
    assert editor('recut.mp4') == str(reveal)
    assert calls == [('stock', 'recut.mp4'), ('source', str(stock), 'retained.mp4', cues, segments)]
    assert changes['layout_ranges'] == layout_ranges.read(str(reveal))
    assert changes['broll']['status'] == 'applied'


def test_source_restore_failure_aborts_instead_of_losing_the_reveal(monkeypatch):
    app = pytest.importorskip('app')
    import source_visual_edit
    def missing(*args):
        raise ValueError('The original source video is needed to preserve these source visuals.')
    monkeypatch.setattr(source_visual_edit, 'reapply_source_visuals', missing)
    clip = {'source_visuals': [{'source_start': 10, 'source_end': 13,
                                'picture_start': 50, 'picture_end': 53}]}
    changes = {}
    editor = app._recut_broll_editor(clip, [{'start': 8, 'end': 15}], changes)
    with pytest.raises(ValueError, match='preserve'):
        editor('recut.mp4')
    assert changes == {}
