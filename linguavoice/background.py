"""Windows logon supervisor. Uses this user's encrypted credential, never a shared key."""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from .storage import ROOT, write_json

STATUS = ROOT/'private/background-status.json'
RUN_NAME = 'LinguaVoice'


def startup_enabled():
    if os.name != 'nt':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
        return value == startup_command()
    except FileNotFoundError:
        return False


def startup_command():
    python = ROOT/'.venv/Scripts/pythonw.exe'
    return subprocess.list2cmdline([str(python), str(ROOT/'run_background.py')])


def set_startup(enabled):
    if type(enabled) is not bool or os.name != 'nt':
        raise ValueError('此功能仅支持 Windows，且需要明确开关值')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            if not (ROOT/'.venv/Scripts/pythonw.exe').is_file():
                raise ValueError('运行环境尚未安装好')
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
    return {'enabled': startup_enabled()}


def status():
    result = {'startup_enabled': startup_enabled(), 'state': 'not_started'}
    try:
        data = json.loads(STATUS.read_text(encoding='utf-8'))
        result.update(data)
        if time.time()-data.get('updated_at', 0) > 120:
            result['state'] = 'not_running'
    except (OSError, ValueError):
        pass
    return result


def service_ready():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8769/health', timeout=2) as response:
            return json.load(response).get('service') == 'linguavoice'
    except Exception:
        return False


def tunnel_status(client):
    result = subprocess.run([str(client), 'runtimes', 'status', 'linguavoice', '--json'],
        capture_output=True, encoding='utf-8', errors='replace', timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        return False
    found = {}
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'process_running', 'healthy', 'ready'} and type(item) is bool:
                    found[key] = item
                elif isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(json.loads(result.stdout))
    return found.get('process_running', False) and found.get('ready', False) and found.get('healthy', False)


def start():
    subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'), str(ROOT/'run_background.py')],
        cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run():
    # One supervisor per install and Windows session; keep the handle alive.
    import hashlib
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, 'Local\\LinguaVoice-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:20])
    if not mutex or ctypes.get_last_error() == 183:
        return
    from scripts.connect_chatgpt import CLIENT, SECRET, PROFILE, connect
    failures = 0
    retry_at = 0
    while True:
        result = {'state': 'starting', 'updated_at': time.time(), 'pid': os.getpid()}
        try:
            if not service_ready():
                with (ROOT/'private/background-service.log').open('ab') as log:
                    subprocess.Popen([sys.executable, '-m', 'linguavoice.server'], cwd=ROOT,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(2)
            result['service_ready'] = service_ready()
            if not result['service_ready']:
                raise RuntimeError('Local service is not ready')
            if not all(p.is_file() for p in (CLIENT, SECRET, PROFILE)):
                if (ROOT/'private/desktop-plugin-installed.json').is_file():
                    result.update(state='local_ready', message='本机语音服务已就绪。本地插件需在聊天中启用。')
                else:
                    result.update(state='needs_connection', message='请先在连接向导安装本地插件，或连接自己的安全隧道。')
            elif tunnel_status(CLIENT):
                failures = 0
                result.update(state='ready', message='ChatGPT 连接已就绪。设置页面可以关闭。')
            elif time.monotonic() >= retry_at:
                import win32crypt
                key = win32crypt.CryptUnprotectData(SECRET.read_bytes(), None, None, None, 0)[1].decode('utf-8')
                try:
                    connect(key, False)
                finally:
                    key = None
                if not tunnel_status(CLIENT):
                    raise RuntimeError('Connection not ready')
                failures = 0
                result.update(state='ready', message='ChatGPT 连接已恢复。')
            else:
                result.update(state='reconnecting', message='正在等待网络恢复，将自动重试。')
        except Exception:
            failures += 1
            retry_at = time.monotonic() + min(300, 15 * 2**min(failures, 5))
            result.update(state='reconnecting', message='连接暂未就绪，正在自动重试；持续失败时请重新连接账号。')
        result['updated_at'] = time.time()
        write_json(STATUS, result)
        time.sleep(15)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--disable', action='store_true')
    args = parser.parse_args()
    if args.enable or args.disable:
        print(json.dumps(set_startup(args.enable), ensure_ascii=False))
        if args.enable:
            start()
    else:
        run()
