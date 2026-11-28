"""Small Windows installer. Dependencies are downloaded at installation, not redistributed."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import urllib.request
import zipfile

ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def safe_extract(archive, destination, strip_root=False):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as source:
        if len(source.infolist()) > 30000 or sum(x.file_size for x in source.infolist()) > 5*1024**3:
            raise ValueError('下载包大小异常')
        seen = set()
        for item in source.infolist():
            parts = PurePosixPath(item.filename.replace('\\', '/')).parts
            if not parts or '..' in parts or any(':' in p for p in parts) or parts[0] == '/' or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('下载包包含不安全路径')
            parts = parts[1:] if strip_root else parts
            if not parts:
                continue
            target = (destination/Path(*parts)).resolve()
            if not target.is_relative_to(destination) or str(target).lower() in seen:
                raise ValueError('下载包路径异常')
            seen.add(str(target).lower())
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as src, target.open('wb') as out:
                    shutil.copyfileobj(src, out, 1024*1024)


def download(item, target, report, cancelled):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == item['size'] and digest(target) == item['sha256']:
        return target
    if cancelled.is_set():
        raise InterruptedError('安装已停止，可以稍后继续')
    partial = target.with_suffix(target.suffix+'.part')
    # Validated ranges support resumable downloads. Keep incomplete chunks for retry.
    chunk_size = 4*1024**2
    ranges = [(start, min(start+chunk_size, item['size'])-1) for start in range(0, item['size'], chunk_size)]
    chunks = target.parent/(target.name+'.chunks')
    chunks.mkdir(exist_ok=True)
    completed = 0
    lock = threading.Lock()
    def one(bounds):
        nonlocal completed
        start,end = bounds
        part = chunks/f'{start:012d}'
        length = end-start+1
        if not part.is_file() or part.stat().st_size != length:
            for attempt in range(3):
                if cancelled.is_set():
                    raise InterruptedError('安装已停止，可以稍后继续')
                try:
                    req = urllib.request.Request(item['url'], headers={'Range': f'bytes={start}-{end}', 'User-Agent':'LinguaVoice-Setup/0.2'})
                    with urllib.request.urlopen(req, timeout=35) as response:
                        expected = f'bytes {start}-{end}/{item["size"]}'
                        if response.status != 206 or response.headers.get('Content-Range') != expected:
                            raise ValueError('range_unsupported')
                        data = response.read(length+1)
                        if len(data) != length:
                            raise IOError('下载未完成')
                    part.write_bytes(data)
                    break
                except Exception as exc:
                    if isinstance(exc, ValueError) or attempt == 2:
                        raise
                    time.sleep(attempt+1)
        with lock:
            completed += length
            report(f'正在下载 {target.name}：{completed/item["size"]:.0%}')
        return part
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            parts = list(pool.map(one, ranges))
        with partial.open('wb') as out:
            for part in parts:
                with part.open('rb') as src:
                    shutil.copyfileobj(src, out, 1024*1024)
    except ValueError as exc:
        if str(exc) != 'range_unsupported':
            raise
        with urllib.request.urlopen(item['url'], timeout=45) as response, partial.open('wb') as out:
            count = 0
            while data := response.read(1024*1024):
                if cancelled.is_set():raise InterruptedError('安装已停止，可以稍后继续')
                count += len(data)
                if count > item['size']:raise ValueError('下载大小不符，已停止')
                out.write(data)
                report(f'正在下载 {target.name}：{count/item["size"]:.0%}')
    if partial.stat().st_size != item['size'] or digest(partial) != item['sha256']:
        raise ValueError('下载文件校验失败，未安装；请重试或联系维护者检查上游文件是否变化')
    os.replace(partial, target)
    return target


def apply_patch(engine, patch):
    """Apply only exact-context unified hunks to this pinned upstream tree."""
    lines = Path(patch).read_text(encoding='utf-8').splitlines(keepends=True)
    i=0
    while i < len(lines):
        if not lines[i].startswith('+++ b/'):
            i+=1;continue
        target = (engine/lines[i][6:].strip()).resolve()
        if not target.is_relative_to(engine.resolve()):raise ValueError('补丁路径无效')
        original=target.read_text(encoding='utf-8').splitlines(keepends=True)
        result=[];cursor=0;i+=1
        while i<len(lines) and not lines[i].startswith('diff --git'):
            if not lines[i].startswith('@@'):
                i+=1;continue
            match=re.match(r'@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@',lines[i])
            if not match:raise ValueError('补丁格式无效')
            start=int(match[1])-1
            result.extend(original[cursor:start]);cursor=start;i+=1
            while i<len(lines) and not lines[i].startswith(('@@','diff --git')):
                line=lines[i];i+=1
                if not line or line[0] not in ' +-':break
                if line[0] in ' -':
                    if cursor>=len(original) or original[cursor]!=line[1:]:raise ValueError('引擎版本与补丁不符，已停止')
                    cursor+=1
                if line[0] in ' +':result.append(line[1:])
        result.extend(original[cursor:]);target.write_text(''.join(result),encoding='utf-8',newline='\n')


def command(args, root, report, cancelled, env=None):
    # Parallel package downloads substantially reduce first-install waiting time.
    uv_candidates=list((root/'private/setup-uv').rglob('uv.exe'))
    if len(args)>3 and list(args[1:4])==['-m','pip','install'] and uv_candidates:
        args=[uv_candidates[0],'pip','install','--python',args[0],*args[4:]]
    with (root/'private/setup.log').open('ab') as log:
        process=subprocess.Popen([str(x) for x in args],cwd=root,env=env,stdout=log,stderr=log,creationflags=FLAGS)
        while process.poll() is None:
            if cancelled.wait(.5):
                # This is our own installer subprocess tree, never another user's process.
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=FLAGS)
                raise InterruptedError('安装已停止，可以稍后继续')
        if process.returncode:
            raise RuntimeError('这一步没有完成。可点击重试；详细原因保存在安装目录 private/setup.log。')


def required_space_gb(with_gpt, with_qwen):
    if not with_gpt and not with_qwen:
        raise ValueError('请至少勾选一种声音支持')
    return 30 if with_gpt and with_qwen else (20 if with_gpt else 15)


def install(root, report, cancelled, with_gpt=False, with_qwen=True):
    space_gb=required_space_gb(with_gpt,with_qwen)
    root=Path(root).resolve()
    if root == Path(root.anchor) or any(c in str(root) for c in '\r\n%&|<>^'):
        raise ValueError('请选择普通文件夹路径，例如 D:\\AIProjects\\LinguaVoice')
    if root.exists() and any(root.iterdir()) and not (root/'linguavoice/storage.py').is_file():
        raise ValueError('这个文件夹已有其他文件，请选一个空文件夹；不会修改其他工程')
    root.mkdir(parents=True,exist_ok=True);(root/'private').mkdir(exist_ok=True)
    if shutil.disk_usage(root).free < space_gb*1024**3:raise ValueError(f'所选组件安装时需预留 {space_gb} GB 可用空间（含解压和缓存，不是下载量）')
    try:
        gpu=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader'],capture_output=True,encoding='utf-8',errors='replace',timeout=15,creationflags=FLAGS)
        if gpu.returncode or not gpu.stdout.strip():raise RuntimeError()
    except Exception:
        raise ValueError('没有检测到可用的 NVIDIA 显卡驱动。当前电脑版需 NVIDIA 显卡，暂不自动切换其他方案')
    report('正在准备程序文件，私人角色不会包含在安装包中')
    manifest=json.loads((ASSETS/'downloads.json').read_text(encoding='utf-8'))
    safe_extract(ASSETS/'source.zip',root,strip_root=True)
    cache=root/'private/setup-downloads'
    (root/'private/setup-temp').mkdir(exist_ok=True)
    env=os.environ.copy();env.update(PYTHONIOENCODING='utf-8',UV_PYTHON_INSTALL_DIR=str(root/'private/python'),UV_CACHE_DIR=str(root/'private/uv-cache'),UV_NO_MODIFY_PATH='1',PIP_CACHE_DIR=str(root/'private/pip-cache'),TEMP=str(root/'private/setup-temp'),TMP=str(root/'private/setup-temp'))
    def prepare_python(folder):
        python=root/folder/'Scripts/python.exe'
        if not python.is_file():
            report('正在准备独立运行环境，不改动已有 Python')
            wheel=download(manifest['uv'],cache/'uv.whl',report,cancelled)
            safe_extract(wheel,root/'private/setup-uv')
            uv=next((root/'private/setup-uv').rglob('uv.exe'))
            candidates=[]
            if not getattr(sys,'frozen',False):candidates.append(Path(sys.base_prefix)/'python.exe')
            launcher=shutil.which('py')
            if launcher:
                found=subprocess.run([launcher,'-3.11','-c','import sys; print(sys.executable)'],capture_output=True,text=True,creationflags=FLAGS)
                if found.returncode==0:candidates.append(Path(found.stdout.strip()))
            compatible=None
            for candidate in candidates:
                if candidate.is_file() and subprocess.run([str(candidate),'-c',"import sys; assert sys.version_info[:3]==(3,11,9)"],capture_output=True,creationflags=FLAGS).returncode==0:
                    compatible=candidate;break
            args=[uv,'venv','--python',compatible or '3.11.9','--seed',root/folder]
            if compatible is None:args.extend(['--python-preference','only-managed'])
            command(args,root,report,cancelled,env)
        return python
    python=prepare_python('.venv')
    command([python,'-m','pip','install','-r',root/'requirements-console.txt'],root,report,cancelled,env)
    runtime_file=root/'private/runtimes.json'
    runtimes=json.loads(runtime_file.read_text(encoding='utf-8')) if runtime_file.is_file() else {}
    if with_qwen:
        # An existing explicitly configured provider can be reused after actual compatibility checks.
        configured=runtimes.get('qwen3-tts',{})
        qpython=Path(configured.get('python') or root/'.engines/qwen3-tts/Scripts/python.exe')
        qprobe="import torch,transformers,faster_qwen3_tts; import importlib.metadata as m; assert torch.__version__=='2.9.1+cu128'; assert transformers.__version__=='5.15.1'; assert m.version('faster-qwen3-tts')=='0.5.4'; assert torch.cuda.is_available()"
        ok=qpython.is_file() and subprocess.run([str(qpython),'-c',qprobe],capture_output=True,creationflags=FLAGS).returncode==0
        if not ok:
            # Never alter an externally configured environment. Repair our own provider environment.
            qpython=prepare_python('.engines/qwen3-tts')
            report('正在安装所选的秋猫声音引擎')
            command([qpython,'-m','pip','install','torch==2.9.1','torchaudio==2.9.1','--index-url','https://download.pytorch.org/whl/cu128'],root,report,cancelled,env)
            command([qpython,'-m','pip','install','-r',root/'requirements-qwen.txt'],root,report,cancelled,env)
        model=Path(configured.get('model_dir') or root/'models/Qwen3-TTS-12Hz-0.6B-Base')
        if not model.is_relative_to(root):
            complete=all((model/i['path']).is_file() and digest(model/i['path'])==i['sha256'] for i in manifest['qwen']['files'])
            if not complete:model=root/'models/Qwen3-TTS-12Hz-0.6B-Base'
        for item in manifest['qwen']['files']:
            download(item,model/item['path'],report,cancelled)
        runtimes['qwen3-tts']={'python':str(qpython),'model_dir':str(model)}
        runtime_file.write_text(json.dumps(runtimes,indent=2),encoding='utf-8')
        command([qpython,'-m','pip','check'],root,report,cancelled,env)
        command([qpython,'-c',qprobe],root,report,cancelled,env)
    if with_gpt:
        report('正在安装可选 GPT-SoVITS 引擎')
        command([python,'-m','pip','install','torch==2.9.1','torchaudio==2.9.1','--index-url','https://download.pytorch.org/whl/cu128'],root,report,cancelled,env)
        command([python,'-m','pip','install','-r',root/'requirements-runtime.txt'],root,report,cancelled,env)
        install_gpt(root,python,manifest,cache,report,cancelled,env)
        runtimes['gpt-sovits']={'python':str(python)}
        runtime_file.write_text(json.dumps(runtimes,indent=2),encoding='utf-8')
    command([python,'-m','pip','check'],root,report,cancelled,env)
    client=root/'private/tunnel-client/verified/tunnel-client.exe'
    if not client.is_file():
        archive=download(manifest['tunnel'],cache/'tunnel-client.zip',report,cancelled)
        safe_extract(archive,client.parent)
    (root/'private/install-selection.json').write_text(json.dumps({'qwen':with_qwen,'gpt':with_gpt}),encoding='utf-8')
    if with_qwen:
        report('正在实际生成秋猫声音，首次准备需要稍等')
        command([python,root/'scripts/verify_install.py','--provider','qwen3-tts'],root,report,cancelled,env)
    if with_gpt:
        report('正在检查 V5 声音支持；没有角色包时需之后自行导入')
        command([python,root/'scripts/verify_install.py','--provider','gpt-sovits'],root,report,cancelled,env)
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\LinguaVoice') as key:
        winreg.SetValueEx(key,'InstallPath',0,winreg.REG_SZ,str(root))
    report('所选组件安装完成。请到网页设置选择或导入声音，再连接自己的 ChatGPT。')
    (root/'private/setup-complete.json').write_text(json.dumps({'runtime_installed':True,'chatgpt_connected':False,'voice_bundled':'default-voice' if with_qwen else None,'gpt_installed':with_gpt,'qwen_installed':with_qwen}),encoding='utf-8')
    return root


def install_gpt(root,python,manifest,cache,report,cancelled,env):
    engine=root/'vendor/GPT-SoVITS'
    if not (engine/'GPT_SoVITS/TTS_infer_pack/TTS.py').is_file():
        report('正在安装已固定版本的语音引擎')
        archive=download(manifest['engine'],cache/'engine.zip',report,cancelled)
        safe_extract(archive,engine,strip_root=True)
        apply_patch(engine,root/'patches/torch29-soundfile.patch')
        (engine/'.linguavoice-patched').write_text(manifest['engine']['sha256'])
    for item in manifest['base']:
        base=engine/'GPT_SoVITS'
        if item['name'].endswith('.zip'):
            marker=root/'private'/('installed-'+item['name']+'.txt')
            if marker.is_file() and marker.read_text()==item['sha256']:continue
            package=download(item,cache/item['name'],report,cancelled)
            safe_extract(package,base)
            marker.write_text(item['sha256'])
        else:
            download(item,base/'pretrained_models/gsv-v5-pretrained'/item['name'],report,cancelled)
    for item in manifest['nltk']:
        target=root/'private/nltk_data'/item['path']
        archive=download(item,target,report,cancelled)
        safe_extract(archive,target.parent)
    report('正在准备音频读取组件')
    command([python,'-c',"import imageio_ffmpeg,shutil,pathlib; p=pathlib.Path('vendor/GPT-SoVITS/runtime'); p.mkdir(exist_ok=True); shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(),p/'ffmpeg.exe')"],root,report,cancelled,env)
    command([python,'-c',"import torch,onnxruntime,peft,rich; assert torch.ones(64,device='cuda').sum().item()==64"],root,report,cancelled,env)


RELEASES_URL = 'https://github.com/papsugar2022-blip/LinguaVoice/releases'


def version_number(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-preview)?', value or '')
    return tuple(map(int, match.groups())) if match else None


def source_version(root):
    try:
        text = (Path(root)/'linguavoice/__init__.py').read_text(encoding='utf-8')
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", text)
        return match[1] if match and version_number(match[1]) else None
    except OSError:
        return None


def bundled_version():
    if not getattr(sys, 'frozen', False):
        return source_version(Path(__file__).resolve().parents[1])
    with zipfile.ZipFile(ASSETS/'source.zip') as archive:
        text = archive.read('LinguaVoice/linguavoice/__init__.py').decode('utf-8')
        return re.search(r"__version__\s*=\s*['\"]([^'\"]+)", text)[1]


def latest_release():
    # The project currently ships previews; /latest excludes them.
    request = urllib.request.Request('https://api.github.com/repos/papsugar2022-blip/LinguaVoice/releases?per_page=30',
        headers={'User-Agent': 'LinguaVoice-Setup', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        releases = json.load(response)
    choices = []
    for release in releases:
        number = version_number(release.get('tag_name'))
        if release.get('draft') or not number:
            continue
        expected = 'LinguaVoice-Setup-v' + '.'.join(map(str, number)) + '.exe'
        if any(a.get('name') == expected for a in release.get('assets', [])):
            choices.append((number, release['tag_name']))
    if not choices:
        raise ValueError('暂时没有可下载的安装包')
    return max(choices)


def main():
    import webbrowser
    window=tk.Tk();window.title('LinguaVoice · 安装与更新');window.geometry('820x680');window.minsize(780,650)
    icon=ASSETS/'linguavoice.ico' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]/'assets/branding/linguavoice.ico'
    if icon.is_file():window.iconbitmap(str(icon))
    style=ttk.Style();style.configure('.',font=('Microsoft YaHei UI',10))
    style.configure('Title.TLabel',font=('Microsoft YaHei UI',18,'bold'))
    frame=ttk.Frame(window,padding=24);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='LinguaVoice · 安装与更新',style='Title.TLabel').pack(anchor='w')
    package=bundled_version()
    versions=tk.StringVar();ttk.Label(frame,textvariable=versions).pack(anchor='w',pady=(8,12))
    default=r'D:\AIProjects\LinguaVoice' if Path('D:/').exists() else str(Path.home()/'LinguaVoice')
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\LinguaVoice') as key:
            default=winreg.QueryValueEx(key,'InstallPath')[0]
    except OSError:pass
    location=tk.StringVar(value=default)
    notices=tk.StringVar(value='先选择安装目录。检查更新只查询版本，不会开始安装或下载模型。')
    messages=queue.Queue();cancelled=threading.Event();running=False;checking=False
    targetbox=ttk.LabelFrame(frame,text='安装目录',padding=10);targetbox.pack(fill='x',pady=(0,12))
    entry=ttk.Entry(targetbox,textvariable=location);entry.pack(side='left',fill='x',expand=True)
    choose=ttk.Button(targetbox,text='选择文件夹',command=lambda:location.set(filedialog.askdirectory() or location.get()));choose.pack(side='right',padx=(8,0))
    choices=ttk.LabelFrame(frame,text='需要哪些声音支持？',padding=12);choices.pack(fill='x')
    extra=tk.BooleanVar(value=True);qwen=tk.BooleanVar(value=False)
    gpt_choice=ttk.Checkbutton(choices,text='自定义导入：V5Turbo（角色声音包需自行导入）',variable=extra);gpt_choice.pack(anchor='w')
    qwen_choice=ttk.Checkbutton(choices,text='自带声音：秋猫（作者自制，可选）',variable=qwen);qwen_choice.pack(anchor='w',pady=(6,0))
    space=tk.StringVar();ttk.Label(choices,textvariable=space,wraplength=710).pack(anchor='w',pady=(8,0))
    def show_space(*_):
        try:space.set(f'首次安装预留 {required_space_gb(extra.get(),qwen.get())} GB，包含解压缓存，不是安装包大小。')
        except ValueError:space.set('安装或修复前，请至少选择一种声音支持。')
    extra.trace_add('write',show_space);qwen.trace_add('write',show_space);show_space()
    ttk.Label(frame,textvariable=notices,wraplength=710).pack(anchor='w',pady=12)
    bar=ttk.Progressbar(frame,mode='indeterminate')
    actions=ttk.Frame(frame);actions.pack(fill='x',pady=(0,12))
    def refresh(*_):
        installed=source_version(location.get())
        ready=bool(installed and (Path(location.get())/'.venv/Scripts/pythonw.exe').is_file())
        versions.set(f'此安装包：{package}     所选目录：'+(f'已安装 {installed}' if installed else '尚未安装'))
        newer=installed and version_number(installed)>version_number(package)
        primary.config(text='立即安装' if not installed else f'更新到 {package}' if version_number(installed)<version_number(package) else '已是此安装包版本' if not newer else '请下载新版安装包',state='normal' if not running and not checking and (not installed or version_number(installed)<version_number(package)) else 'disabled')
        repair.config(state='normal' if installed and not newer and not running and not checking else 'disabled')
        check.config(state='disabled' if running or checking else 'normal')
        for button in (settings,connection):button.config(state='normal' if ready and not running else 'disabled')
        for control in (entry,choose,gpt_choice,qwen_choice):control.config(state='disabled' if running else 'normal')
        if running:cancel.pack(side='right')
        else:cancel.pack_forget()
    def load_selection(*_):
        try:
            saved=json.loads((Path(location.get())/'private/install-selection.json').read_text(encoding='utf-8'))
            extra.set(bool(saved['gpt']));qwen.set(bool(saved['qwen']))
        except (OSError,ValueError,KeyError,TypeError):pass
        refresh()
        notices.set('已找到安装目录。检查更新只查询版本，不会开始安装。' if source_version(location.get()) else '此目录尚未安装，点击“立即安装”开始。检查更新不会下载模型。')
    def begin():
        nonlocal running
        if running:return
        try:required_space_gb(extra.get(),qwen.get())
        except ValueError as exc:notices.set(str(exc));return
        installed=source_version(location.get())
        if installed and version_number(installed)>version_number(package):
            notices.set('本机版本更新，不会用旧安装包覆盖；请先检查更新。');return
        target=location.get();include_gpt=extra.get();include_qwen=qwen.get()
        running=True;cancelled.clear();cancel.config(state='normal',text='取消本次安装');refresh()
        notices.set('正在准备，请稍候。已有音色和设置会保留。')
        bar.pack(fill='x',pady=(0,10),before=actions);bar.start()
        def worker():
            try:
                install(target,lambda text:messages.put(('progress',text)),cancelled,include_gpt,include_qwen)
                messages.put(('done','已完成。打开网页设置试听；更新后请到 ChatGPT 的 LinguaVoice 设置刷新工具。'))
            except Exception as exc:messages.put(('done',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    def check_update():
        nonlocal checking
        checking=True;refresh();notices.set('正在查询 GitHub 最新安装包，不下载模型…')
        def worker():
            try:messages.put(('release',latest_release()))
            except Exception:messages.put(('check_error','检查更新失败，可能是网络暂时无法访问 GitHub。可以稍后重试，或打开下载页。'))
        threading.Thread(target=worker,daemon=True).start()
    def stop():
        cancelled.set();cancel.config(state='disabled',text='正在取消…')
        notices.set('正在取消，请等当前步骤停止；已下载的文件保留，方便下次继续。')
    def launch(script):
        root=Path(location.get());python=root/'.venv/Scripts/pythonw.exe'
        if running or not python.is_file():return
        subprocess.Popen([str(python),*script],cwd=root,creationflags=FLAGS)
    primary=ttk.Button(actions,command=begin);primary.pack(side='left',padx=(0,8))
    check=ttk.Button(actions,text='检查更新',command=check_update);check.pack(side='left',padx=(0,8))
    repair=ttk.Button(actions,text='修复安装',command=begin);repair.pack(side='left',padx=(0,8))
    cancel=ttk.Button(actions,text='取消本次安装',command=stop)
    download_button=ttk.Button(frame,text='打开版本下载页',command=lambda:webbrowser.open(RELEASES_URL));download_button.pack(anchor='w')
    daily=ttk.LabelFrame(frame,text='已安装？从这里使用',padding=12);daily.pack(fill='x',pady=14)
    dailybuttons=ttk.Frame(daily);dailybuttons.pack(anchor='w')
    settings=ttk.Button(dailybuttons,text='打开声音设置',command=lambda:launch(['-m','linguavoice.server','--open']));settings.pack(side='left',padx=(0,8))
    connection=ttk.Button(dailybuttons,text='连接 ChatGPT',command=lambda:launch(['scripts/connect_chatgpt.py']));connection.pack(side='left')
    ttk.Label(daily,text='连接一次后，在 ChatGPT 输入 @，选择 LinguaVoice 再聊天。\n停止当前声音：说“停止播放”；关闭后续语音：声音设置 → 关闭“让 AI 说话”。',wraplength=700).pack(anchor='w',pady=(10,0))
    ttk.Label(frame,text='仅支持 Windows / NVIDIA。程序代码 MIT；声音模型遵守各自许可。\n第三方角色模型与私人凭据不随安装包发布；未勾选不会删除已有音色。',wraplength=710).pack(anchor='w')
    location.trace_add('write',load_selection);load_selection()
    def poll():
        nonlocal running,checking
        try:
            while True:
                kind,value=messages.get_nowait()
                if kind=='release':
                    checking=False;number,tag=value;refresh()
                    current=source_version(location.get())
                    if number>version_number(package):
                        notices.set(f'发现新版 {tag}。请点“下载新版安装包”，下载后运行，选择原目录更新。')
                        download_button.config(text='下载新版安装包',command=lambda t=tag:webbrowser.open(RELEASES_URL+'/tag/'+t))
                    elif number<version_number(package):
                        notices.set(f'GitHub 最新发布为 {tag}；此安装包 {package} 比公开版本更新。')
                    elif current and version_number(current)<version_number(package):
                        notices.set(f'此安装包已是最新发布版本；点击“更新到 {package}”更新所选目录。')
                    else:notices.set('没有发现更新的安装包。'+('本机已是最新版本。' if current else '还未安装，点击“立即安装”。'))
                elif kind=='check_error':checking=False;notices.set(value);refresh()
                else:
                    notices.set(value)
                    if kind=='done':running=False;bar.stop();bar.pack_forget();refresh()
        except queue.Empty:pass
        window.after(150,poll)
    def close():
        if running:stop()
        else:window.destroy()
    window.protocol('WM_DELETE_WINDOW',close);window.after(150,poll);window.mainloop()


if __name__=='__main__':main()
