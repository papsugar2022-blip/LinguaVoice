import threading
import time
import unittest
import numpy as np
from linguavoice.player import DesktopPlayer


class PlayerTests(unittest.TestCase):
    def test_pause_stop_queue_and_recovery(self):
        reports = []
        written = threading.Event()
        class Stream:
            def __init__(self, **kw): pass
            def start(self): pass
            def stop(self): pass
            def abort(self): pass
            def close(self): pass
            def write(self, frames):
                written.set()
                time.sleep(.01)
        player = DesktopPlayer(lambda manual: True, lambda *args: reports.append(args), Stream,
                               lambda p: (np.zeros((1024*100, 1), dtype='float32'), 48000))
        self.addCleanup(player.close)
        player.enqueue('first', 'fixture');player.enqueue('stale', 'fixture')
        self.assertTrue(written.wait(2))
        player.control('pause')
        deadline = time.monotonic()+2
        while not any(x[:2] == ('first','paused') for x in reports) and time.monotonic()<deadline:time.sleep(.01)
        self.assertIn(('first','paused',None), reports)
        player.control('stop')
        player.enqueue('new', 'fixture')
        player.queue.join()
        self.assertIn(('first','stopped',None), reports)
        self.assertNotIn(('stale','playing',None), reports)
        self.assertIn(('new','ended',None), reports)

    def test_output_failure_does_not_kill_worker(self):
        reports=[]
        def fail(**kw):raise OSError('No output device')
        player=DesktopPlayer(lambda manual: True,lambda *args: reports.append(args), fail,
                             lambda p:(np.zeros((5,1),dtype='float32'),48000))
        self.addCleanup(player.close)
        for name in ('one','two'):player.enqueue(name,'fixture')
        player.queue.join()
        self.assertEqual([x[1] for x in reports],['error','error'])
        self.assertTrue(player.worker.is_alive())

    def test_autoplay_off_skips_queued_audio(self):
        reports=[]
        player=DesktopPlayer(lambda manual: False,lambda *args:reports.append(args))
        self.addCleanup(player.close)
        player.enqueue('off','must-not-read.wav');player.queue.join()
        self.assertEqual(reports,[('off','stopped',None)])

if __name__=='__main__':unittest.main()
