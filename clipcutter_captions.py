"""ClipCutter's original caption timing and ASS effects, adapted for OpenShorts.
The caption event and visual-run algorithms are copied from the local project.
Only transcript conversion and OpenShorts split-screen placement are adapted.
"""
import math
import re
from pathlib import Path
from clipcutter_caption_settings import normalize_editing_settings, CAPTION_STYLES

def stamp(t, ass=False):
    unit = 100 if ass else 1000
    ticks = round(t * unit)
    h, ticks = divmod(ticks, 3600 * unit)
    m, ticks = divmod(ticks, 60 * unit)
    s, fraction = divmod(ticks, unit)
    return f'{h}:{m:02}:{s:02}.{fraction:02}' if ass else f'{h:02}:{m:02}:{s:02},{fraction:03}'

def ass_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    return text.replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\r', '').replace('\n', r'\N')

def _positioned_caption_runs(start, end, layouts, title_until, width, height, **kwargs):
    points = sorted({start, end, *[point for item in layouts for point in (item['start'],item['end']) if start < point < end]})
    for a,b in zip(points,points[1:]):
        split=any(item['layout']=='split' and item['start'] <= (a+b)/2 < item['end'] for item in layouts)
        yield a,b,(r'\an5\pos(' + f'{width//2},{height//2})') if split else ''


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid caption ' + label + '.')
    return float(value)

def _interval(item, limit_ms):
    start = _number(item.get('start_time'), 'start time')
    end = _number(item.get('end_time'), 'end time')
    if end < start or end > limit_ms + 350:
        raise ValueError('Caption timing does not match the prepared clip.')
    return min(start, limit_ms) / 1000, min(end, limit_ms) / 1000

def caption_events(utterances, duration, max_words=4):
    """Return short timed phrases, without inferring missing word boundaries."""
    if not isinstance(utterances, list) or not utterances:
        raise ValueError('Transcription returned no recognized speech for this clip.')
    events = []
    for utterance in utterances:
        start, end = _interval(utterance, duration * 1000)
        text = utterance.get('text')
        if not isinstance(text, str) or not text.strip():
            continue
        words = []
        for word in utterance.get('words') or []:
            if not isinstance(word.get('text'), str):
                raise ValueError('A caption word is missing its text.')
            if not word['text'].strip():
                continue
            a, b = _interval(word, duration * 1000)
            if a < start - .05 or b > end + .05:
                raise ValueError('Caption word timing falls outside its utterance.')
            words.append({'text': word['text'].strip(), 'start': a, 'end': b})
        if not words:
            if end > start:
                # Sentence-only results remain sentence-timed; no word animation.
                events.append({'start': start, 'end': end, 'text': text.strip(), 'active': None})
            continue
        if any(b['start'] < a['start'] or b['start'] < a['end'] - .05
               for a, b in zip(words, words[1:])):
            raise ValueError('Caption word timing is out of order or overlapping.')
        groups, group = [], []
        for word in words:
            candidate = group + [word]
            if group and (len(candidate) > max_words or len(' '.join(x['text'] for x in candidate)) > 24
                          or word['start'] - group[-1]['end'] > .7):
                groups.append(group)
                group = []
            group.append(word)
        if group:
            groups.append(group)
        for group in groups:
            phrase = ' '.join(x['text'] for x in group)
            for index, word in enumerate(group):
                if word['end'] > word['start']:
                    events.append({'start': word['start'], 'end': word['end'], 'text': phrase,
                                   'active': index, 'words': [x['text'] for x in group]})
                if index + 1 < len(group):
                    next_start = group[index + 1]['start']
                    if 0 < next_start - word['end'] <= .4:
                        events.append({'start': word['end'], 'end': next_start, 'text': phrase, 'active': None,
                                       'words': [x['text'] for x in group], 'revealed': index})
    if not events:
        raise ValueError('Transcription returned no usable timed captions.')
    return sorted(events, key=lambda event: (event['start'], event['end']))

def _ass_color(value, opacity=100):
    """Convert a validated CSS color into ASS's alpha/BGR representation."""
    value = value.lstrip('#')
    alpha = round(255 * (1 - opacity / 100))
    return f'&H{alpha:02X}{value[4:6]}{value[2:4]}{value[:2]}'

def _caption_case(text, settings):
    mode = settings['caption_case']
    if mode == 'auto':
        mode = ('original' if settings['caption_style'] in
                ('soft_build', 'word_cut', 'handwritten', 'typewriter') else 'upper')
    return text.upper() if mode == 'upper' else text.lower() if mode == 'lower' else text

def _styled_caption_runs(event, settings, size):
    """Yield visual intervals; character reveals stay inside real word timings."""
    style = settings['caption_style']
    start, end = event['start'], event['end']
    text = _caption_case(event['text'], settings)
    words = [_caption_case(word, settings) for word in event.get('words', [])]
    active = event['active']
    revealed = event.get('revealed', active)
    base = '&H' + _ass_color(settings['caption_color'])[4:] + '&'
    highlight = '&H' + _ass_color(settings['highlight_color'])[4:] + '&'
    opacity = round(255 * (1 - settings['caption_opacity'] / 100))
    if not words:
        # Recognition supplied a sentence interval, so retain that interval and
        # wording instead of synthesizing word or character timing.
        yield start, end, ass_text(text)
        return
    escaped = [ass_text(word) for word in words]
    if style == 'word_cut':
        yield start, end, escaped[revealed] if revealed is not None else ass_text(text)
    elif style == 'typewriter' and revealed is not None:
        prefix = ' '.join(escaped[:revealed])
        prefix += ' ' if prefix else ''
        cursor = r'{\c' + highlight + '}▌' + r'{\c' + base + '}'
        if active is None:
            yield start, end, prefix + escaped[revealed] + cursor
        else:
            word = words[active]
            for index in range(1, len(word) + 1):
                a = start + (end - start) * (index - 1) / len(word)
                b = start + (end - start) * index / len(word)
                yield a, b, prefix + ass_text(word[:index]) + cursor
    elif style == 'soft_build' and revealed is not None:
        visible = escaped[:revealed + 1]
        if active is not None:
            fade_ms = min(160, round((end - start) * 600))
            visible[-1] = (r'{\1a&HFF&\t(0,' + str(fade_ms) +
                           r',\1a&H' + f'{opacity:02X}' + '&)}' + visible[-1] +
                           r'{\1a&H' + f'{opacity:02X}' + '&}')
        yield start, end, ' '.join(visible)
    elif style == 'simple':
        yield start, end, ' '.join(escaped)
    else:
        if active is not None:
            if style == 'marker_sweep':
                sweep_ms = max(1, round((end - start) * 1000))
                escaped[active] = (r'{\rMarker\fs' + str(size) +
                                   r'\fscx65\t(0,' + str(sweep_ms) +
                                   r',\fscx100)}' + escaped[active] +
                                   r'{\rCaption\fs' + str(size) + '}')
            elif style == 'karaoke':
                duration_cs = max(1, round((end - start) * 100))
                for index in range(active):
                    escaped[index] = r'{\c' + highlight + '}' + escaped[index]
                escaped[active] = (r'{\1c' + highlight + r'\2c' + base +
                                   r'\kf' + str(duration_cs) + '}' + escaped[active] +
                                   r'{\c' + base + '}')
            elif style == 'negative_keywords':
                dim = min(255, opacity + 90)
                escaped = [r'{\1a&H' + f'{dim:02X}' + r'&\c' + base + '}' + word for word in escaped]
                escaped[active] = (r'{\1a&H' + f'{opacity:02X}' +
                                   r'&\c' + highlight + '}' + ass_text(words[active]))
            elif style == 'handwritten':
                escaped[active] = (r'{\u1\c' + highlight + '}' + escaped[active] +
                                   r'{\u' + ('1' if settings['caption_underline'] else '0') +
                                   r'\c' + base + '}')
            else:  # Mozi: bold active-word color, preserving the original style.
                escaped[active] = r'{\c' + highlight + '}' + escaped[active] + r'{\c' + base + '}'
        elif revealed is not None and style == 'karaoke':
            # Keep already sung words highlighted through real pauses.
            for index in range(revealed + 1):
                escaped[index] = r'{\c' + highlight + '}' + escaped[index] + r'{\c' + base + '}'
        elif style == 'negative_keywords':
            # A word boundary must not flash the whole phrase back to full alpha.
            dim = min(255, opacity + 90)
            escaped = [r'{\1a&H' + f'{dim:02X}' + r'&\c' + base + '}' + word for word in escaped]
            if revealed is not None:
                escaped[revealed] = (r'{\1a&H' + f'{opacity:02X}' +
                                     r'&\c' + highlight + '}' + ass_text(words[revealed]))
        yield start, end, ' '.join(escaped)

def _validated_layout_ranges(ranges, duration):
    if ranges is None:
        return []
    if not isinstance(ranges, list):
        raise ValueError('Caption layout ranges must be a list.')
    result = []
    for item in ranges:
        if not isinstance(item, dict):
            raise ValueError('Invalid caption layout range.')
        start = _number(item.get('start'), 'layout start')
        end = _number(item.get('end'), 'layout end')
        if (end <= start or end > duration + .001
                or item.get('layout') not in ('solo', 'split', 'left', 'right', 'fit')
                or result and start < result[-1]['end'] - .000001):
            raise ValueError('Caption layout ranges must match the output timeline.')
        result.append(dict(item, start=start, end=min(end, duration)))
    return result

def write_caption_files(ass_file, srt_file, utterances, duration, settings=None,
                        width=1080, height=1920, layout_ranges=None,
                        opening_title_text=None):
    settings = normalize_editing_settings(settings)
    events = caption_events(utterances, duration, settings['words_per_caption'])
    layouts = _validated_layout_ranges(layout_ranges, duration)
    # None preserves legacy placement. An explicit empty string means that no
    # title was baked into this clip, so there is no occupied title interval.
    known_title = False
    title_until = (min(duration, settings['title_duration'])
                   if (known_title or layout_ranges is not None) and settings['opening_title'] else 0)
    obstacle = None
    scale = min(width / 1080, height / 1920)
    fontsize = max(12, round(settings['caption_size'] * scale))
    fonts = {'marker_sweep': 'Impact', 'simple': 'Impact', 'handwritten': 'Comic Sans MS',
             'typewriter': 'Courier New'}
    font = fonts.get(settings['caption_style'], 'Arial') if settings['caption_font'] == 'auto' else settings['caption_font']
    primary = _ass_color(settings['caption_color'], settings['caption_opacity'])
    secondary = _ass_color(settings['highlight_color'], settings['caption_opacity'])
    outline = _ass_color(settings['outline_color'], settings['caption_opacity'])
    background = _ass_color(settings['background_color'], settings['caption_opacity'] * settings['caption_background_opacity'] / 100)
    italic = settings['caption_italic'] or (settings['caption_font'] == 'auto' and settings['caption_style'] == 'handwritten')
    row = {'bottom': 0, 'center': 3, 'top': 6}[settings['caption_position']]
    alignment = row + {'left': 1, 'center': 2, 'right': 3}[settings['caption_alignment']]
    margin_x = round(width / 12)
    margin_y = round(height * (.1875 if settings['caption_position'] != 'top' else .18))
    borderstyle = 3 if settings['caption_background'] else 1
    bordercolor = background if settings['caption_background'] else outline
    borderwidth = max(6 * scale, settings['outline_width'] * scale) if borderstyle == 3 else settings['outline_width'] * scale
    style_tail = (f'{-int(settings["caption_bold"])},{-int(italic)},'
                  f'{-int(settings["caption_underline"])},0,100,100,'
                  f'{settings["caption_spacing"] * scale:g},0,')
    ending = f'{settings["shadow"] * scale:g},{alignment},{margin_x},{margin_x},{margin_y},1'
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{fontsize},{primary},{secondary},{bordercolor},&H80000000,{style_tail}{borderstyle},{borderwidth:g},{ending}
Style: Marker,{font},{fontsize},{primary},{secondary},{secondary},&H80000000,{style_tail}3,{max(4, 8 * scale):g},{ending}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines = []
    if settings['caption_style'] != 'none':
        for event in events:
            size = fontsize if len(event['text']) <= 24 else max(round(fontsize * .55), fontsize * 24 // len(event['text']))
            for start, end, text in _styled_caption_runs(event, settings, size):
                # ASS stores centiseconds; the recognition JSON and SRT retain ms.
                for a, b, position in _positioned_caption_runs(start, end, layouts, title_until, width, height,
                        settings=settings, text=_caption_case(event['text'], settings), size=size,
                        obstacle=obstacle, known_title=known_title):
                    if stamp(a, True) != stamp(b, True):
                        lines.append(f'Dialogue: 0,{stamp(a, True)},{stamp(b, True)},Caption,,0,0,0,,{{\\fs{size}{position}}}{text}')
    ass_file.write_text(header + '\n'.join(lines) + '\n', encoding='utf-8')
    cues = []
    for utterance in utterances:
        start, end = _interval(utterance, duration * 1000)
        if end > start and utterance.get('text', '').strip():
            cues.append(f'{len(cues) + 1}\n{stamp(start)} --> {stamp(end)}\n{utterance["text"].strip()}')
    srt_file.write_text('\n\n'.join(cues) + '\n', encoding='utf-8')

def generate_clipcutter_ass(transcript, start, end, output, preset, *, fontsize=72,
                            alignment='bottom', font_name=None, font_color='#FFFFFF',
                            highlight_color='#00FF00', border_color='#000000',
                            border_width=5, shadow=1, uppercase=True, split_ranges=None,
                            words_per_caption=4, bg_color='#000000', bg_opacity=0):
    duration=end-start
    if duration <= 0:return False
    from subtitles import merge_untimed_caption_words
    utterances=[]
    for segment in transcript.get('segments',[]):
        a=max(start,float(segment.get('start',start)));b=min(end,float(segment.get('end',end)))
        if b<=a:continue
        words=[]
        selected = [word for word in segment.get('words') or []
                    if (float(word.get('end',b)) > start or
                        float(word.get('start',a)) == float(word.get('end',b)) == start)
                    and float(word.get('start',a)) < end]
        for word in merge_untimed_caption_words(selected):
            wa=max(start,float(word.get('start',a)));wb=min(end,float(word.get('end',b)))
            if wb>wa and str(word.get('word','')).strip():
                words.append({'text':str(word['word']).strip(),'start_time':(wa-start)*1000,'end_time':(wb-start)*1000})
        text=' '.join(w['text'] for w in words) if words else segment.get('text','').strip()
        if text:utterances.append({'text':text,'start_time':(a-start)*1000,'end_time':(b-start)*1000,'words':words})
    if not utterances:return False
    options={'caption_style':preset,'caption_size':max(32,min(120,fontsize)),
             'caption_position':'center' if alignment=='middle' else alignment,
             'caption_color':font_color,'highlight_color':highlight_color,
             'outline_color':border_color,'outline_width':border_width,'shadow':shadow,
             'caption_case':'upper' if uppercase else 'original','opening_title':False,
             'caption_background':bg_opacity > 0, 'background_color':bg_color,
             'caption_background_opacity':bg_opacity * 100,
             'words_per_caption':words_per_caption}
    if font_name:options['caption_font']=font_name
    layouts=[{'start':max(0,a),'end':min(duration,b),'layout':'split'} for a,b in split_ranges or [] if min(duration,b)>max(0,a)]
    output=Path(output)
    write_caption_files(output,output.with_suffix('.srt'),utterances,duration,options,
                        layout_ranges=layouts)
    return True
