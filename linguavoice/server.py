import argparse
import base64
import json
import secrets
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import __version__
from .service import VoiceService
from .storage import ROOT, Store
from .installer import Installer


class ConsoleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, store, service):
        self.store, self.service = store, service
        self.installer = Installer(store)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'LinguaVoice/' + __version__

    def log_message(self, *_):
        pass  # Do not log private spoken text, model paths, tokens, or request URLs.

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def gate(self, authenticated=True):
        port = self.server.server_port
        allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in allowed_hosts:
            self.send(403, {'error': 'Host不受支持'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{h}' for h in allowed_hosts}:
            self.send(403, {'error': '跨站请求被拒绝'})
            return False
        if authenticated:
            supplied = self.headers.get('Authorization', '').removeprefix('Bearer ')
            if not secrets.compare_digest(supplied, self.server.store.token):
                self.send(401, {'error': '请通过“启动控制台”打开已授权的本机页面'})
                return False
        return True

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.gate(authenticated=path.startswith('/api/')):
            return
        try:
            if path == '/health':
                return self.send(200, {'service': 'linguavoice', 'version': __version__})
            static = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in static:
                name, content_type = static[path]
                return self.send(200, (ROOT/'linguavoice/web'/name).read_bytes(), content_type)
            if path == '/api/state':
                from .background import status
                return self.send(200, {**self.server.store.public_state(), **self.server.service.snapshot(), 'background': status()})
            if path == '/api/export':
                return self.send(200, self.server.store.export_preset())
            if path == '/api/catalog':
                from .catalog import catalog_items
                return self.send(200, {'voices': [{k: v for k,v in item.items() if k != 'file'} for item in catalog_items()]})
            if path == '/api/support':
                from .capabilities import support_state
                return self.send(200, support_state(self.server.store))
            if path.startswith('/api/installs/'):
                return self.send(200, self.server.installer.get(path.rsplit('/', 1)[-1]))
            if path.startswith('/api/card-status/'):
                return self.send(200, self.server.service.card_status(path.rsplit('/',1)[-1]))
            if path.startswith('/api/jobs/'):
                return self.send(200, self.server.service.job(path.rsplit('/',1)[-1]))
            if path.startswith('/api/references/'):
                voice = self.server.store.voice(path.rsplit('/',1)[-1])
                return self.send(200, {'locked': voice.get('reference_locked', False),
                    'selected_index': next((i for i,r in enumerate(voice['references']) if r==voice['reference']), None), 'references': [
                    {'index': i, 'text': r['text'], 'language': r['language'], 'filename': Path(r['audio']).name}
                    for i,r in enumerate(voice['references'])]})
            if path.startswith('/api/audio/'):
                name = path.rsplit('/',1)[-1]
                job_id = name.removesuffix('.wav')
                job = self.server.service.job(job_id)
                if name != job_id+'.wav' or job['status'] != 'ready':
                    raise ValueError('语音尚未准备好')
                return self.send(200, (self.server.service.audio_dir/name).read_bytes(), 'audio/wav')
            return self.send(404, {'error': '接口不存在'})
        except (KeyError, ValueError, OSError) as exc:
            self.send(400, {'error': str(exc)})

    def do_POST(self):
        if not self.gate():
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if path in {'/api/import-voice', '/api/install'}:
                if not 0 < length <= 1024**3:
                    raise ValueError('模型包上传限制为1GB')
                if self.headers.get('X-Model-Trust') != 'yes':
                    raise ValueError('请确认模型来源与使用许可')
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                self.connection.settimeout(30)
                upload_dir = self.server.store.directory/'uploads'
                upload_dir.mkdir(exist_ok=True)
                pending = None
                try:
                    with tempfile.NamedTemporaryFile(dir=upload_dir, suffix='.zip', delete=False) as archive:
                        pending = Path(archive.name)
                        remaining = length
                        while remaining:
                            chunk = self.rfile.read(min(1024*1024, remaining))
                            if not chunk:
                                raise ValueError('上传没有完成，请重新拖入角色包')
                            archive.write(chunk)
                            remaining -= len(chunk)
                    if path == '/api/install':
                        result = self.server.installer.start(pending, query.get('name', [''])[0])
                        pending = None  # The installation worker now owns cleanup.
                        return self.send(202, result)
                    with pending.open('rb') as archive:
                        result = self.server.store.import_zip(archive, query.get('name', [''])[0], trusted=True)
                    return self.send(200, result)
                finally:
                    if pending is not None:
                        pending.unlink(missing_ok=True)
            if not 0 <= length <= 1024*1024:
                raise ValueError('请求体大小无效')
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise ValueError('需要JSON对象')
            service, store = self.server.service, self.server.store
            if path == '/api/settings':
                result = store.update_settings(data)
                if not result['speech_enabled'] or not result.get('desktop_playback', True) or not result['autoplay']:
                    service.control('stop')
            elif path == '/api/startup':
                from .background import set_startup, start
                result = set_startup(data.get('enabled'))
                if result['enabled']:
                    start()
            elif path == '/api/speak':
                result = service.submit(**data)
            elif path == '/api/card-control':
                result = service.card_control(data['job_id'], data['action'])
            elif path == '/api/control':
                result = service.control(data.get('action'))
            elif path == '/api/replay':
                result = service.replay(data.get('job_id'))
            elif path == '/api/reveal':
                result = service.job(data['job_id'], reveal=True)
            elif path == '/api/import-settings':
                result = store.import_preset(data)
            elif path == '/api/reference':
                store.choose_reference(data['voice_id'], data['index'])
                result = {'saved': True}
            elif path == '/api/rename-voice':
                store.rename_voice(data['voice_id'], data['name'])
                result = {'saved': True}
            elif path == '/api/playback':
                state = data.get('state')
                if state not in {'idle','playing','paused','blocked','ended','error','armed'}:
                    raise ValueError('播放状态无效')
                with service.lock:
                    service.playback = {'state': state, 'job_id': data.get('job_id'), 'last_seen': time.time()}
                    job = service.jobs.get(data.get('job_id'))
                    if state == 'playing' and job and job['status'] == 'ready':
                        job.setdefault('first_played_at', time.time())
                        job['delivery_seconds'] = job['first_played_at'] - job['ready_at']
                result = {'ok': True}
            else:
                return self.send(404, {'error': '接口不存在'})
            self.send(200, result)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            self.send(400, {'error': str(exc)[:400]})
        except Exception:
            self.send(500, {'error': '操作失败；模型包结构或服务状态异常，请检查本机日志'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    store = Store()
    url = f'http://127.0.0.1:{args.port}'
    try:
        with urllib.request.urlopen(url+'/health', timeout=1) as response:
            running = json.load(response).get('service') == 'linguavoice'
    except Exception:
        running = False
    if running:
        if args.open:
            webbrowser.open(url+'/#token='+store.token)
        return
    server = ConsoleHTTPServer(('127.0.0.1', args.port), store, VoiceService(store, desktop=True))
    print(f'LinguaVoice console: {url} (loopback only)', flush=True)
    if args.open:
        webbrowser.open(url+'/#token='+store.token)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
