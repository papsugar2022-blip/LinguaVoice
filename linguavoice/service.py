import copy
import queue
import threading
import time
import uuid
from .storage import speed_value
from .backends import BackendRouter


class VoiceService:
    def __init__(self, store, backend=None, desktop=False):
        self.store = store
        self.backend = backend or BackendRouter()
        self.audio_dir = store.directory/'audio'
        self.audio_dir.mkdir(exist_ok=True)
        self.jobs = {}
        self.lock = threading.RLock()
        self.queue = queue.Queue(maxsize=12)
        self.state = 'idle'
        self.revision = 0
        self.command = {'revision': 0, 'action': 'none'}
        self.playback = {'state': 'not_connected', 'last_seen': 0, 'job_id': None}
        self.player = None
        if desktop:
            from .player import DesktopPlayer
            self.player = DesktopPlayer(self._play_allowed, self._play_report)
        self.worker = threading.Thread(target=self._run, daemon=True, name='LinguaVoice-GPU')
        self.worker.start()

    def submit(self, text, voice_id=None, role=None, language='zh', speed=None,
               hide_text=False, source='console'):
        if not isinstance(text, str) or not text.strip() or len(text) > 400:
            raise ValueError('朗读内容应为1～400字；更长内容由AI按语义分段调用')
        settings = self.store.snapshot()['settings']
        if not settings['speech_enabled']:
            raise ValueError('语音功能已关闭')
        voice = self.store.voice(voice_id, role)
        from .model_versions import VERSIONS
        if voice['provider'] == 'gpt-sovits' and voice['version'] not in VERSIONS:
            raise ValueError('当前精简版只启用 V5Turbo 和秋猫引擎；此声音的原文件仍保留在本机')
        from .languages import LANGUAGES
        if language not in LANGUAGES.get(voice['provider'], ()):
            raise ValueError('这个声音引擎不支持所选语言；请换语言，不会擅自换声音')
        chosen_speed = speed_value(settings['speed'] if speed is None else speed)
        job_id = uuid.uuid4().hex
        with self.lock:
            if self.queue.full():
                raise ValueError('待合成队列已满，请稍后重试')
            # Bound the in-memory queue/history. Generated files stay private.
            if len(self.jobs) >= 80:
                expired = next((k for k,v in self.jobs.items() if v['status'] in {'ready','error','cancelled'}), None)
                if expired:
                    del self.jobs[expired]
                else:
                    raise ValueError('任务数量已达上限')
            job = {'id': job_id, 'status': 'queued', 'text': text.strip(), 'hide_text': bool(hide_text),
                'voice_id': voice['id'], 'voice_name': voice['name'], 'role': role,
                'language': language, 'speed': chosen_speed, 'source': source, 'created': time.time(),
                'error': None, 'cancelled': False,
                'playback_target': 'desktop' if self.player and settings.get('desktop_playback', True) and source == 'chatgpt-mcp' else 'browser'}
            self.jobs[job_id] = job
            self.queue.put_nowait((job_id, voice))
            return self.job(job_id)

    def job(self, job_id, reveal=False):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError('没有找到这段语音')
            job = copy.deepcopy(self.jobs[job_id])
        job.pop('cancelled', None)
        if job['hide_text'] and not reveal:
            job.pop('text', None)
        if job['status'] == 'ready':
            job['audio_path'] = '/api/audio/' + job_id + '.wav'
        return job

    def snapshot(self):
        with self.lock:
            ids = list(self.jobs)[-30:]
            playback = copy.deepcopy(self.playback)
            playback['connected'] = time.time()-playback['last_seen'] < 10
            if self.player and self.store.snapshot()['settings'].get('desktop_playback', True):
                playback = self.player.snapshot()
            return {'engine_state': self.state, 'jobs': [self.job(k) for k in ids],
                    'playback': playback, 'command': copy.deepcopy(self.command)}

    def control(self, action):
        if action not in {'pause', 'resume', 'stop'}:
            raise ValueError('未知播放操作')
        with self.lock:
            self.revision += 1
            self.command = {'revision': self.revision, 'action': action}
            if action == 'stop':
                for job in self.jobs.values():
                    if job['status'] in {'queued', 'generating'}:
                        job['cancelled'] = True
                        job['status'] = 'cancelled'
            if self.player:
                self.player.control(action)
            return {'requested': action, 'delivery': '已发送至电脑播放器' if self.player else '等待控制台播放器执行'}

    def _play_allowed(self, manual=False):
        settings = self.store.snapshot()['settings']
        return settings['speech_enabled'] and settings.get('desktop_playback', True) and (manual or settings['autoplay'])

    def replay(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job['status'] != 'ready':
                raise ValueError('这段语音还不能重播')
            if not self.player or not self._play_allowed(manual=True):
                raise ValueError('请先开启电脑后台播放和语音功能，或使用网页音频播放器')
            self.player.control('stop')
            self.player.enqueue(job_id, self.audio_dir/(job_id+'.wav'), manual=True)
            return {'queued': True, 'job_id': job_id}

    def _play_report(self, job_id, state, error):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job['playback_status'] = state
            job['playback_error'] = error
            if state == 'playing':
                job.setdefault('first_played_at', time.time())
                job['delivery_seconds'] = job['first_played_at'] - job['ready_at']
            if state == 'ended':
                job['playback_finished_at'] = time.time()

    def _run(self):
        while True:
            job_id, voice = self.queue.get()
            try:
                with self.lock:
                    job = self.jobs[job_id]
                    if job['cancelled']:
                        continue
                    job['status'] = 'generating'
                    job['started_at'] = time.time()
                    job['queue_seconds'] = job['started_at'] - job['created']
                    self.state = 'generating'
                    request = copy.deepcopy(job)
                result = self.backend.synthesize(voice, request['text'], request['language'],
                    request['speed'], self.audio_dir/(job_id+'.wav'))
                with self.lock:
                    job.update(result)
                    job['ready_at'] = time.time()
                    job['status'] = 'cancelled' if job['cancelled'] else 'ready'
                    if job['status'] == 'ready' and job['playback_target'] == 'desktop' and self._play_allowed():
                        self.player.enqueue(job_id, self.audio_dir/(job_id+'.wav'))
            except Exception as exc:
                with self.lock:
                    if job_id in self.jobs:
                        self.jobs[job_id].update(status='error', error=str(exc)[:400])
                # Drop a partly loaded/inconsistent model after an error.
                self.backend.unload()
            finally:
                with self.lock:
                    self.state = 'idle'
                self.queue.task_done()
