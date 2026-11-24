"""Private, asynchronous ZIP installation with truthful progress and bounded work."""
import copy
import threading
import uuid
from pathlib import Path
from .storage import ROOT
from .model_versions import VERSIONS

class Installer:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.jobs = {}
        self.active = None

    def update(self, job_id, **changes):
        with self.lock:
            self.jobs[job_id].update(changes)

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('没有找到这次安装，请重新添加角色包')
            return copy.deepcopy(self.jobs[job_id])

    def start(self, archive, name=''):
        with self.lock:
            if self.active:
                raise ValueError('已有角色正在安装，请等它完成后再添加')
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {'id': job_id, 'status': 'installing', 'progress': 0,
                                 'message': '角色包已收到，正在检查'}
            self.active = job_id
            for old in list(self.jobs)[:-20]:
                del self.jobs[old]
        threading.Thread(target=self._run, args=(job_id, Path(archive), name), daemon=True).start()
        return self.get(job_id)

    def _run(self, job_id, archive, name):
        try:
            with archive.open('rb') as stream:
                result = self.store.import_zip(stream, name, trusted=True,
                    progress=lambda n, message: self.update(job_id, progress=n, message=message))
            voice = self.store.voice(result['id'])
            self.update(job_id, progress=85, message='正在检查运行条件', voice_id=voice['id'])
            base = ROOT/'vendor/GPT-SoVITS/GPT_SoVITS/pretrained_models'
            if voice['provider'] == 'qwen3-tts':
                missing = [] if (Path(voice['model_dir'])/'model.safetensors').is_file() else ['Qwen3-TTS']
            else:
                if voice['version'] not in VERSIONS:
                    raise ValueError('当前精简版只启用 V5Turbo；已有声音文件保留，未删除')
                missing = [p for p in VERSIONS[voice['version']]['files'] if not (base/p).is_file()]
            # Installation must not claim a usable voice when required engines are absent.
            if missing:
                self.update(job_id, status='needs_setup', progress=100, result=result,
                    message=f'角色已保存，但这台电脑还缺少 {voice["version"]} 语音组件，暂时不能试听。请保留角色包，补齐组件后即可使用')
            else:
                self.update(job_id, status='ready', progress=100, result=result,
                    message='这个角色已经添加过了，无需重复安装' if result.get('duplicate') else '安装完成，可以试听了')
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, OSError)) else '安装没有完成。请确认下载的是完整角色包，然后重试'
            self.update(job_id, status='error', message=message[:300])
        finally:
            archive.unlink(missing_ok=True)
            with self.lock:
                self.active = None
