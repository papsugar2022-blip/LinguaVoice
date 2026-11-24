"""Provider routing without character-specific code or shared engine dependencies."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from .storage import ROOT


class BackendRouter:
    def __init__(self):
        self.process=None;self.key=None;self.responses=None;self.log=None

    def unload(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        if self.process:
            self.process.stdin.close();self.process.stdout.close()
        if self.log:self.log.close()
        self.process=None;self.key=None;self.responses=None;self.log=None

    def synthesize(self,voice,text,language,speed,destination):
        provider=voice['provider']
        if provider not in {'gpt-sovits','qwen3-tts'}:raise ValueError('未接入此语音引擎')
        config_path=ROOT/'private/runtimes.json'
        config=json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
        python=Path(config.get(provider,{}).get('python') or ROOT/f'.engines/{provider}/Scripts/python.exe')
        if not python.is_file() and provider=='gpt-sovits':python=Path(sys.executable)
        if not python.is_file():raise ValueError('此声音需要的运行组件尚未安装，请在安装程序中准备该引擎')
        key=(provider,str(python))
        if self.key!=key or self.process is None or self.process.poll() is not None:
            self.unload()
            self.responses=queue.Queue()
            env=os.environ.copy();env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
            self.log=(ROOT/'private'/f'{provider}-worker.log').open('ab')
            self.process=subprocess.Popen([str(python),str(ROOT/'linguavoice/backend_worker.py')],cwd=ROOT,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,encoding='utf-8',
                env=env,bufsize=1,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.key=key
            process,responses=self.process,self.responses
            def read():
                try:
                    for line in process.stdout:
                        try:responses.put(json.loads(line))
                        except ValueError:pass
                finally:responses.put({'ok':False,'error':'语音进程已退出，请重试；持续失败时查看本机引擎日志'})
            threading.Thread(target=read,daemon=True).start()
        request={'voice':voice,'text':text,'language':language,'speed':speed,'destination':str(Path(destination).resolve())}
        self.process.stdin.write(json.dumps(request,ensure_ascii=False)+'\n');self.process.stdin.flush()
        try:reply=self.responses.get(timeout=300)
        except queue.Empty:self.unload();raise RuntimeError('合成等待超时，已释放引擎，请缩短内容后重试')
        if not reply['ok']:raise RuntimeError(reply['error'])
        return reply['result']
