import unittest
from unittest.mock import patch
from linguavoice.mcp_server import audio_result

class PayloadTests(unittest.IsolatedAsyncioTestCase):
    def job(self, **changes):
        return dict({'id': 'a'*32, 'status': 'ready', 'voice_name': 'Test',
            'text': 'PRIVATE TRANSCRIPT', 'hide_text': True, 'audio_path': '/api/audio/test.wav',
            'playback_target': 'desktop', 'seconds': 30}, **changes)

    async def test_desktop_repeated_queries_never_fetch_or_embed_waveform(self):
        with patch('linguavoice.mcp_server.request', return_value=self.job()) as request:
            for _ in range(3):
                result = await audio_result('a'*32)
                self.assertTrue(all(c.type == 'text' for c in result.content))
                self.assertLess(len(result.model_dump_json().encode()), 1500)
                self.assertNotIn('PRIVATE TRANSCRIPT', result.model_dump_json())
                self.assertEqual(result.structured_content['id'], 'a'*32)
            self.assertEqual(request.call_count, 3)
            self.assertTrue(all(c.args == ('/api/jobs/'+'a'*32,) for c in request.call_args_list))

    async def test_no_route_or_legacy_parameter_can_embed_waveform(self):
        for target in ('browser', 'desktop', None):
            for include in (False, True):
                with self.subTest(target=target, include=include), patch('linguavoice.mcp_server.request',
                        return_value=self.job(playback_target=target)) as request:
                    result=await audio_result('a'*32, include_audio=include)
                    self.assertTrue(all(c.type == 'text' for c in result.content))
                    self.assertLess(len(result.model_dump_json().encode()), 1500)
                    request.assert_called_once_with('/api/jobs/'+'a'*32)

    async def test_pending_error_cancelled_do_not_echo_transcripts_or_paths(self):
        for status in ('queued','generating','cancelled','error'):
            with self.subTest(status=status), patch('linguavoice.mcp_server.request', return_value=self.job(status=status)):
                result=await audio_result('a'*32)
                text=result.model_dump_json()
                self.assertNotIn('PRIVATE TRANSCRIPT', text)
                self.assertNotIn('/api/audio/', text)
                self.assertEqual(result.is_error, status in {'error','cancelled'})
