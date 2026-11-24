import unittest
from unittest.mock import patch
from linguavoice.mcp_server import linguavoice_replay, linguavoice_show_player


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_player_only_reads_selected_job(self):
        job_id = 'a' * 32
        with patch('linguavoice.mcp_server.ensure_service'), patch('linguavoice.mcp_server.request') as request:
            request.return_value = {'id': job_id, 'status': 'ready', 'voice_name': 'Test',
                                    'text': 'Hidden exercise', 'hide_text': True, 'audio_path': '/api/audio/test.wav'}
            result = await linguavoice_show_player(job_id)
            request.assert_called_once_with('/api/jobs/' + job_id)
            self.assertEqual(result.structured_content, {'id': job_id, 'status': 'ready', 'voice_name': 'Test'})
            self.assertTrue(all(c.type == 'text' for c in result.content))
            self.assertNotIn('Hidden exercise', str(result))

    async def test_show_player_rejects_invalid_job(self):
        with patch('linguavoice.mcp_server.request') as request:
            with self.assertRaises(ValueError):
                await linguavoice_show_player('../audio')
            request.assert_not_called()

    async def test_existing_audio_is_replayed_without_synthesis(self):
        job_id = 'a' * 32
        with patch('linguavoice.mcp_server.ensure_service'), patch('linguavoice.mcp_server.request') as request:
            request.return_value = {'queued': True, 'job_id': job_id}
            self.assertEqual((await linguavoice_replay(job_id)).structured_content, request.return_value)
            request.assert_called_once_with('/api/replay', {'job_id': job_id})

    async def test_invalid_id_never_reaches_service(self):
        with patch('linguavoice.mcp_server.request') as request:
            for job_id in ['', '../audio', 'g' * 32]:
                with self.assertRaises(ValueError):
                    await linguavoice_replay(job_id)
            request.assert_not_called()
