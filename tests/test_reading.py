import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import soundfile as sf
from linguavoice.segments import spoken_segments, synthesize_segments
from linguavoice.storage import Store
from linguavoice.service import VoiceService
from linguavoice.player import DesktopPlayer
from linguavoice.mcp_server import linguavoice_card_status


class ReadingTests(unittest.TestCase):
    def test_segments_preserve_all_words_and_real_audio_boundaries(self):
        text = '接着看 elevated。\nStress can lead to elevated blood pressure. Next example!'
        parts = spoken_segments(text)
        self.assertEqual(''.join(text.split()), ''.join(''.join(parts).split()))
        class Backend:
            def synthesize(self, voice, text, language, speed, dest):
                # Test fixture only; never presented as synthesized speech.
                sf.write(dest, np.ones(800, dtype=np.float32)*.1, 8000)
                return {'seconds': .1}
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'test.wav'
            result=synthesize_segments(Backend(), {'sentence_pause': .45}, text, 'zh', 1, dest)
            data, rate=sf.read(dest)
            self.assertEqual(len(result['segments']), 3)
            self.assertAlmostEqual(result['segments'][1]['start'], .55)
            self.assertAlmostEqual(result['segments'][2]['end'], 1.2)
            self.assertTrue(np.all(data[800:4400] == 0))
            self.assertEqual(len(data), 9600)
            self.assertEqual(list(Path(d).iterdir()), [dest])

    def test_settings_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d)
            for v in [True, -1, 3, float('nan'), '0.4']:
                with self.assertRaises(ValueError):s.update_settings({'sentence_pause': v})
            s.update_settings({'show_reading_indicator': False, 'sentence_pause': .75})
            self.assertEqual(Store(d).snapshot()['settings']['sentence_pause'], .75)
            self.assertFalse(Store(d).snapshot()['settings']['show_reading_indicator'])

    def test_hidden_text_and_disabled_indicator_do_not_leak(self):
        job={'id':'a'*32,'hide_text':False,'playback_target':'desktop','seconds':2,
             'segments':[{'text':'secret phrase','start':0,'end':1}]}
        service=object.__new__(VoiceService)
        service.job=lambda _:job
        settings={'show_reading_indicator':True}
        service.store=SimpleNamespace(snapshot=lambda:{'settings':settings})
        playback={'job_id':job['id'],'state':'playing','position':.5}
        service.player=SimpleNamespace(snapshot=lambda:playback)
        self.assertEqual(service.card_status(job['id'])['current_text'],'secret phrase')
        job['hide_text']=True
        self.assertEqual(service.card_status(job['id'])['current_text'],'')
        job['hide_text']=False;settings['show_reading_indicator']=False
        self.assertEqual(service.card_status(job['id'])['current_text'],'')
        settings['show_reading_indicator']=True;playback['job_id']='b'*32
        self.assertEqual(service.card_status(job['id'])['current_text'],'')

    def test_card_cannot_pause_another_recording(self):
        player=DesktopPlayer(lambda _:True,lambda *args:None)
        try:
            player.state.update(job_id='one',state='playing')
            with self.assertRaises(ValueError):player.control('pause',job_id='two')
            self.assertFalse(player.paused)
            player.control('pause',job_id='one')
            self.assertTrue(player.paused)
        finally:player.close()

    def test_transcript_only_in_widget_metadata(self):
        with patch('linguavoice.mcp_server.request',return_value={'current_text':'private teaching text'}):
            result=asyncio.run(linguavoice_card_status('a'*32))
        self.assertNotIn('private teaching text', str(result.structured_content))
        self.assertEqual(result.content, [])
        self.assertEqual(result.meta['reading']['current_text'],'private teaching text')
