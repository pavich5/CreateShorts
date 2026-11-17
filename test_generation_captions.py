import tempfile
import unittest
from pathlib import Path
from subtitles import (AUTO_CAPTION_STYLE, CAPTION_PRESETS, generation_caption_style,
                       generation_caption_metadata, generate_ass)

class GenerationCaptionTests(unittest.TestCase):
    def test_every_preset_produces_the_selected_ass_look(self):
        transcript = {'segments':[{'start':0,'end':2,'text':'Create great clips',
            'words':[{'word':'Create','start':0,'end':0.6},
                     {'word':'great','start':0.6,'end':1.2},
                     {'word':'clips','start':1.2,'end':2}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            for preset in CAPTION_PRESETS:
                with self.subTest(preset=preset):
                    style = generation_caption_style(preset)
                    path = Path(tmp)/(preset+'.ass')
                    options = {k:v for k,v in style.items() if k not in ('style','font_size')}
                    self.assertTrue(generate_ass(transcript,0,2,str(path),fontsize=style['font_size'],**options))
                    self.assertIn(style['font_name'],path.read_text())
                    metadata = generation_caption_metadata(preset)
                    self.assertEqual(metadata['position'],'bottom')
                    self.assertEqual(metadata['effect'],CAPTION_PRESETS[preset]['effect'])
                    self.assertEqual(metadata['max_chars'],CAPTION_PRESETS[preset]['max_chars'])
                    self.assertNotIn('alignment',metadata)

    def test_clipcutter_styles_keep_real_timing_and_split_placement(self):
        transcript={'segments':[{'start':10,'end':11,'text':'To get started',
            'words':[{'word':'To','start':10.05,'end':10.2},
                     {'word':'get','start':10.2,'end':10.4},
                     {'word':'started','start':10.45,'end':10.95}]}]}
        from clipcutter_caption_settings import CAPTION_STYLES
        from clipcutter_captions import generate_clipcutter_ass
        with tempfile.TemporaryDirectory() as tmp:
            outputs=set()
            for style in CAPTION_STYLES:
                path=Path(tmp)/(style+'.ass')
                self.assertTrue(generate_clipcutter_ass(transcript,10,11,path,style,split_ranges=[(0,1)]))
                text=path.read_text();outputs.add(text)
                lines=[line for line in text.splitlines() if line.startswith('Dialogue:')]
                self.assertEqual(bool(lines),style!='none')
                if lines:self.assertIn(r'\an5\pos(540,960)',text)
                if style=='typewriter':
                    self.assertEqual(len(lines),len('Togetstarted')+1)  # cursor stays visible across the real pause
                    self.assertIn('0:00:00.45,0:00:00.52',text)
                self.assertIn('00:00:00,000 --> 00:00:01,000',path.with_suffix('.srt').read_text())
            self.assertEqual(len(outputs),len(CAPTION_STYLES))
            sentence={'segments':[{'start':10.1,'end':10.9,'text':'Whole sentence'}]}
            path=Path(tmp)/'sentence.ass'
            generate_clipcutter_ass(sentence,10,11,path,'typewriter')
            self.assertEqual(path.read_text().count('Dialogue:'),1)
            self.assertNotIn('▌',path.read_text())

    def test_shared_creation_and_editor_styles_match_server_presets(self):
        import json
        source=Path('dashboard/src/components/captionStyles.js').read_text()
        styles=json.loads(source.split(' = ',1)[1].rstrip(';\n'))
        self.assertEqual(len(styles),8)
        for style in styles:
            preset=CAPTION_PRESETS[style['id']]
            for client,server in [('fontName','font_name'),('fontSize','font_size'),('effect','effect'),('highlightColor','highlight_color')]:
                self.assertEqual(style[client],preset[server],style['id'])

    def test_default_stays_consistent_and_resolution_does_not_mutate_defaults(self):
        before = dict(AUTO_CAPTION_STYLE)
        self.assertEqual(generation_caption_style('default')['font_size'],44)
        self.assertEqual(generation_caption_style('oneword')['max_chars'],1)
        self.assertTrue(generation_caption_style('hormozi')['reveal'])
        generation_caption_metadata('clean')
        self.assertEqual(AUTO_CAPTION_STYLE,before)
        with self.assertRaises(ValueError):generation_caption_style('invalid')

if __name__ == '__main__':unittest.main()
