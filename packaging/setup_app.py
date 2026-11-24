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


def main():
    window=tk.Tk();window.title('LinguaVoice · 安装与启动');window.geometry('780x650')
    icon=ASSETS/'linguavoice.ico' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]/'assets/branding/linguavoice.ico'
    if icon.is_file():window.iconbitmap(str(icon))
    ttk.Style().configure('.',font=('Microsoft YaHei UI',10))
    frame=ttk.Frame(window,padding=24);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='让 ChatGPT 用你选好的声音说话',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Windows / NVIDIA 显卡。按勾选内容下载，不强制安装秋猫。\nV5 角色包需按作者许可自行导入；不包含账号或第三方角色录音。',wraplength=650).pack(anchor='w',pady=12)
    activation=ttk.LabelFrame(frame,text='日常使用：打开 ChatGPT，直接 @',padding=12)
    activation.pack(fill='x',pady=(0,12))
    ttk.Label(activation,text='输入 @ → 从列表选择 LinguaVoice → 发送你想聊的话。\n不用另发启用口令；同一聊天后续正常交流即可。',font=('Microsoft YaHei UI',10,'bold'),wraplength=680).pack(anchor='w')
    ttk.Label(activation,text='停止播放：在聊天里说“停止播放”，或点网页里的“停止播放”。\n关闭后续语音：打开“网页设置”，关掉“让 AI 说话”；重新打开即可恢复。',wraplength=680).pack(anchor='w',pady=(6,0))
    ttk.Label(activation,text='首次安装并连接一次；新聊天再 @ 选择插件。\n网页开启“登录电脑后自动准备”后，日常不用再开安装器。',wraplength=680).pack(anchor='w',pady=(6,0))
    location=tk.StringVar(value=r'D:\AIProjects\LinguaVoice' if Path('D:/').exists() else str(Path.home()/'LinguaVoice'))
    ttk.Label(frame,text='安装位置').pack(anchor='w')
    entry=ttk.Entry(frame,textvariable=location);entry.pack(fill='x',pady=5)
    ttk.Button(frame,text='选择文件夹',command=lambda:location.set(filedialog.askdirectory() or location.get())).pack(anchor='w')
    ttk.Label(frame,text='使用范围：程序代码为 MIT。第三方组件、模型和参考录音遵守各自许可。\n不允许通过安装包转卖或冒充第三方资源；请保留来源和署名。',wraplength=650).pack(anchor='w',pady=14)
    extra=tk.BooleanVar(value=True);qwen=tk.BooleanVar(value=False)
    choices=ttk.Frame(frame);choices.pack(fill='x')
    ttk.Checkbutton(choices,text='自定义导入（V5Turbo 声音包）',variable=extra).pack(side='left')
    ttk.Checkbutton(choices,text='自带声音（作者自制：秋猫）',variable=qwen).pack(side='left',padx=12)
    space=tk.StringVar()
    def show_space(*_):
        try:space.set(f'安装时建议预留 {required_space_gb(extra.get(),qwen.get())} GB（含解压缓存，不是下载量）。未勾选不会卸载已有声音。')
        except ValueError:space.set('请至少勾选一项；只用 V5 角色包无需勾选秋猫。')
    extra.trace_add('write',show_space);qwen.trace_add('write',show_space);show_space()
    ttk.Label(frame,textvariable=space,wraplength=680).pack(anchor='w',pady=(4,0))
    notices=tk.StringVar(value='已经连接过：直接回 ChatGPT 使用。只有首次配置或连接异常时才需要下方连接设置。')
    ttk.Label(frame,textvariable=notices,wraplength=650).pack(anchor='w',pady=10)
    bar=ttk.Progressbar(frame,mode='determinate',value=0)
    messages=queue.Queue();cancelled=threading.Event();running=False
    buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=10)
    def begin():
        nonlocal running
        if running:return
        try:required_space_gb(extra.get(),qwen.get())
        except ValueError as exc:notices.set(str(exc));return
        running=True;cancelled.clear();start_button.config(state='disabled');entry.config(state='disabled');bar.config(mode='indeterminate');bar.pack(fill='x',pady=8,before=buttons);bar.start()
        target=location.get();include_gpt=extra.get();include_qwen=qwen.get()
        def worker():
            try:
                install(target,lambda text:messages.put(('progress',text)),cancelled,include_gpt,include_qwen)
                messages.put(('done','安装完成。请到网页设置导入 / 选择声音并试听，再完成首次连接。以后回 ChatGPT 直接 @ 使用。'))
            except Exception as exc:messages.put(('done',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    def launch(script):
        root=Path(location.get());python=root/'.venv/Scripts/pythonw.exe'
        if running:notices.set('请等安装完成后再打开。');return
        if not python.is_file():notices.set('请先安装程序。');return
        subprocess.Popen([str(python),*script],cwd=root,creationflags=FLAGS)
    start_button=ttk.Button(buttons,text='安装 / 检查修复',command=begin);start_button.pack(side='left',padx=3)
    ttk.Button(buttons,text='网页设置',command=lambda:launch(['-m','linguavoice.server','--open'])).pack(side='left',padx=3)
    ttk.Button(buttons,text='首次连接 / 修复',command=lambda:launch(['scripts/connect_chatgpt.py'])).pack(side='left',padx=3)
    ttk.Button(buttons,text='停止安装',command=cancelled.set).pack(side='left',padx=3)
    def poll():
        nonlocal running
        try:
            while True:
                kind,text=messages.get_nowait();notices.set(text)
                if kind=='done':running=False;bar.stop();bar.pack_forget();start_button.config(state='normal');entry.config(state='normal')
        except queue.Empty:pass
        window.after(150,poll)
    def close():
        if running:
            cancelled.set();notices.set('正在停止安装，请等候；已下载的文件会保留以便重试。')
        else:window.destroy()
    window.protocol('WM_DELETE_WINDOW',close);window.after(150,poll);window.mainloop()


if __name__=='__main__':main()
