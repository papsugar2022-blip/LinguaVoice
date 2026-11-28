"""Local speaker output, independent of a browser. Never captures microphone audio."""
import copy
import queue
import threading
import time


class DesktopPlayer:
    def __init__(self, allowed, report, stream_factory=None, read_audio=None):
        self.allowed, self.report = allowed, report
        self.stream_factory, self.read_audio = stream_factory, read_audio
        self.queue = queue.Queue(maxsize=80)
        self.condition = threading.Condition()
        self.epoch = 0
        self.paused = False
        self.closed = False
        self.state = {'state': 'armed', 'job_id': None, 'connected': True, 'target': 'desktop', 'error': None}
        self.worker = threading.Thread(target=self._run, daemon=True, name='LinguaVoice-speaker')
        self.worker.start()

    def snapshot(self):
        with self.condition:
            return copy.deepcopy(self.state)

    def enqueue(self, job_id, path, manual=False):
        with self.condition:
            self.queue.put_nowait((job_id, path, self.epoch, manual))

    def control(self, action, job_id=None):
        with self.condition:
            if job_id is not None and (self.state.get('job_id') != job_id or self.state['state'] not in {'playing', 'paused'}):
                raise ValueError('这段语音当前没有播放，不能控制其他录音')
            if action == 'pause':
                self.paused = True
            elif action == 'resume':
                self.paused = False
            elif action == 'stop':
                self.epoch += 1
                self.paused = False
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.epoch += 1
            self.condition.notify_all()
        self.worker.join(timeout=2)

    def _status(self, job_id, state, error=None):
        with self.condition:
            self.state.update(state=state, job_id=job_id, error=error, last_seen=time.time())
        self.report(job_id, state, error)

    def _valid(self, epoch, manual):
        return not self.closed and epoch == self.epoch and self.allowed(manual)

    def _run(self):
        while not self.closed:
            try:
                job_id, path, epoch, manual = self.queue.get(timeout=.2)
            except queue.Empty:
                continue
            stream = None
            try:
                if not self._valid(epoch, manual):
                    self._status(job_id, 'stopped')
                    continue
                if self.read_audio is None:
                    import soundfile
                    data, rate = soundfile.read(path, dtype='float32', always_2d=True)
                else:
                    data, rate = self.read_audio(path)
                if self.stream_factory is None:
                    import sounddevice
                    factory = sounddevice.OutputStream
                else:
                    factory = self.stream_factory
                stream = factory(samplerate=rate, channels=data.shape[1], dtype='float32', latency='low')
                with self.condition:
                    self.state.update(job_id=job_id, position=0.0, duration=len(data)/rate)
                stream.start()
                started = False
                for offset in range(0, len(data), 1024):
                    if not self._valid(epoch, manual):
                        break
                    if self.paused:
                        stream.stop()
                        self._status(job_id, 'paused')
                        with self.condition:
                            while self.paused and self._valid(epoch, manual):
                                self.condition.wait(.1)
                        if not self._valid(epoch, manual):
                            break
                        stream.start()
                        self._status(job_id, 'playing')
                    stream.write(data[offset:offset+1024])
                    with self.condition:
                        self.state['position'] = min(offset + 1024, len(data)) / rate
                    if not started:
                        started = True
                        self._status(job_id, 'playing')
                if self._valid(epoch, manual):
                    stream.stop()  # Drain the device before reporting completion.
                    self._status(job_id, 'ended')
                else:
                    stream.abort()
                    self._status(job_id, 'stopped')
            except Exception:
                self._status(job_id, 'error', '电脑扬声器播放失败，请检查输出设备；可在控制台手动重播。')
            finally:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                self.queue.task_done()
