"""Every offered caption font and background setting is renderable."""
import pytest

from clipcutter_caption_settings import normalize_editing_settings
from subtitles import generate_ass


@pytest.mark.parametrize('font', ['Anton', 'Montserrat ExtraBold', 'Verdana', 'Arial', 'Impact', 'Helvetica', 'Georgia', 'Courier New'])
def test_every_dashboard_font_is_valid_for_clipcutter(font):
    assert normalize_editing_settings({'caption_font': font})['caption_font'] == font


@pytest.mark.parametrize('opacity,ass_color', [(.25, '&HBF332211'), (.5, '&H80332211'), (1, '&H00332211')])
def test_clipcutter_background_color_and_opacity_are_forwarded(tmp_path, opacity, ass_color):
    output = tmp_path / 'caption.ass'
    transcript = {'segments': [{'start': 0, 'end': 1, 'words': [
        {'word': ' visible', 'start': 0, 'end': 1}]}]}
    assert generate_ass(transcript, 0, 1, str(output), effect='clipcutter:mozi',
                        font_name='Arial', bg_color='#112233', bg_opacity=opacity)
    caption_style = next(line for line in output.read_text().splitlines() if line.startswith('Style: Caption,'))
    assert ass_color in caption_style
    assert caption_style.split(',')[15] == '3'  # BorderStyle: opaque box
