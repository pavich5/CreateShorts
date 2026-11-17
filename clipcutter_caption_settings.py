"""Caption defaults and validation ported from the local ClipCutter project."""
import math
import re

CAPTION_STYLES = ('none', 'marker_sweep', 'karaoke', 'mozi', 'soft_build',
                  'word_cut', 'negative_keywords', 'handwritten', 'simple', 'typewriter')

FONTS = ('auto', 'Arial', 'Helvetica', 'Georgia', 'Courier New', 'Comic Sans MS', 'Impact',
         'Anton', 'Montserrat ExtraBold', 'Verdana')

ASPECT_RATIOS = {
    '9:16': (1080, 1920), '16:9': (1920, 1080), '1:1': (1080, 1080),
    '4:5': (1080, 1350), '4:3': (1440, 1080), '3:4': (1080, 1440),
    '21:9': (2520, 1080), 'original': None,
}

DEFAULT_EDITING_SETTINGS = {
    'caption_style': 'mozi', 'caption_font': 'auto', 'caption_size': 72,
    'caption_color': '#FFFFFF', 'highlight_color': '#00FF00',
    'outline_color': '#000000', 'outline_width': 5, 'shadow': 1,
    'caption_position': 'bottom', 'caption_case': 'auto', 'words_per_caption': 4,
    'caption_background': False, 'background_color': '#000000', 'caption_background_opacity': 75,
    'caption_bold': True, 'caption_italic': False, 'caption_underline': False,
    'caption_alignment': 'center', 'caption_spacing': 0, 'caption_opacity': 100,
    'aspect_ratio': '9:16', 'framing': 'crop', 'opening_title': True,
    'title_duration': 3,
    'title_font': 'auto', 'title_size': 66, 'title_color': '#000000',
    'title_background': True, 'title_background_color': '#FFFFFF',
    'title_background_opacity': 100, 'title_x': 50, 'title_y': 55,
    'title_max_width': 84, 'title_padding_x': 25, 'title_padding_y': 16,
    'title_line_spacing': -1, 'title_letter_spacing': 0,
    'title_border_radius': 22, 'title_bold': True, 'title_alignment': 'center',
    'pacing': 'original', 'layout_mode': 'auto',
    'music_file': '', 'music_gain_db': -24, 'music_ducking': True,
    'broll_file': '', 'broll_start': 3, 'broll_duration': 3,
}

PRESET_DEFAULTS = {
    'marker_sweep': {'highlight_color': '#C7A6F8', 'outline_width': 2},
    'soft_build': {'caption_bold': False, 'caption_size': 64, 'outline_width': 0, 'shadow': 0},
    'negative_keywords': {'highlight_color': '#BC9A84'},
    'handwritten': {'highlight_color': '#FFE66A', 'caption_italic': True,
                    'outline_width': 0, 'shadow': 0},
    'typewriter': {'highlight_color': '#FFDF35', 'caption_size': 56, 'outline_width': 0, 'shadow': 0},
}

def normalize_editing_settings(settings=None):
    """Return a complete safe recipe; reject invalid input before any rendering."""
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError('Editing settings must be an object.')
    unknown = set(settings) - set(DEFAULT_EDITING_SETTINGS)
    if unknown:
        raise ValueError('Unknown editing setting: ' + ', '.join(sorted(map(str, unknown))))
    style = settings.get('caption_style', DEFAULT_EDITING_SETTINGS['caption_style'])
    preset = PRESET_DEFAULTS.get(style, {}) if isinstance(style, str) else {}
    result = dict(DEFAULT_EDITING_SETTINGS, **preset)
    result.update(settings)
    choices = {
        'caption_style': CAPTION_STYLES, 'caption_font': FONTS,
        'title_font': FONTS, 'title_alignment': ('left', 'center', 'right'),
        'caption_position': ('bottom', 'center', 'top'),
        'caption_case': ('auto', 'upper', 'original', 'lower'),
        'caption_alignment': ('left', 'center', 'right'),
        'aspect_ratio': ASPECT_RATIOS, 'framing': ('crop', 'fit'),
        'pacing': ('original', 'gentle'), 'layout_mode': ('auto', 'solo', 'split', 'fit'),
    }
    for key, values in choices.items():
        if not isinstance(result[key], str) or result[key] not in values:
            raise ValueError('Choose a supported ' + key.replace('_', ' ') + '.')
    for key in ('caption_background', 'opening_title', 'caption_bold',
                'caption_italic', 'caption_underline', 'music_ducking',
                'title_background', 'title_bold'):
        if type(result[key]) is not bool:
            raise ValueError(key.replace('_', ' ').capitalize() + ' must be true or false.')
    ranges = {'caption_size': (32, 120), 'outline_width': (0, 12), 'shadow': (0, 8),
              'caption_background_opacity': (0, 100),
              'words_per_caption': (1, 8), 'title_duration': (.5, 10),
              'caption_spacing': (-2, 12), 'caption_opacity': (10, 100),
              'music_gain_db': (-40, 0), 'broll_start': (0, 90), 'broll_duration': (.5, 10),
              'title_size': (28, 120), 'title_background_opacity': (0, 100),
              'title_x': (5, 95), 'title_y': (5, 95), 'title_max_width': (30, 95),
              'title_padding_x': (0, 80), 'title_padding_y': (0, 60),
              'title_line_spacing': (-10, 60), 'title_letter_spacing': (-2, 12),
              'title_border_radius': (0, 60)}
    integers = {'words_per_caption', 'caption_opacity'}
    for key, (low, high) in ranges.items():
        value = result[key]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not low <= value <= high
                or (key in integers and value != int(value))):
            raise ValueError(f'{key.replace("_", " ").capitalize()} must be between {low} and {high}'
                             + (' (whole numbers).' if key in integers else '.'))
        if key in integers:
            result[key] = int(value)
    for key in ('caption_color', 'highlight_color', 'outline_color', 'background_color',
                'title_color', 'title_background_color'):
        value = result[key]
        if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            raise ValueError('Choose a six-digit hex color for ' + key.replace('_', ' ') + '.')
        result[key] = value.upper()
    for key in ('music_file', 'broll_file'):
        if not isinstance(result[key], str) or len(result[key]) > 4096 or '\x00' in result[key]:
            raise ValueError('Choose a valid local ' + key.replace('_file', '') + ' file.')
    return result
