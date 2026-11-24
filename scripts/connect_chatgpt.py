"""Local-only secret entry. Plaintext keys never enter arguments or project files."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import re
import webbrowser
import tkinter as tk
from tkinter import ttk
import yaml

ROOT=Path(__file__).resolve().parents[1]
CLIENT=ROOT/'private/tunnel-client/verified/tunnel-client.exe'
PROFILE_DIR=ROOT/'private/tunnel-profiles'
PROFILE=PROFILE_DIR/'linguavoice.yaml'
SECRET=ROOT/'private/tunnel-runtime-key.dpapi'
STATUS=ROOT/'private/tunnel-connection-status.json'


def connect(key, remember, tunnel_id=None):
    if not CLIENT.is_file():
        raise ValueError('缺少连接组件，请运行安装程序完成安装。')
    if not key.startswith('sk-') or len(key)<25:
        raise ValueError('请粘贴完整的运行密钥，不要粘贴 tunnel ID。')
    if not PROFILE.is_file():
        if not re.fullmatch(r'tunnel_[a-zA-Z0-9_-]{8,100}', tunnel_id or ''):
            raise ValueError('首次连接需要填写 OpenAI 页面提供的连接编号（tunnel_ 开头）。')
        PROFILE_DIR.mkdir(parents=True,exist_ok=True)
        command=subprocess.list2cmdline([str(ROOT/'.venv/Scripts/python.exe'),str(ROOT/'run_mcp.py')])
        result=subprocess.run([str(CLIENT),'init','--profile','linguavoice','--profile-dir',str(PROFILE_DIR),
            '--tunnel-id',tunnel_id,'--mcp-command',command,'--control-plane-api-key-ref','env:LINGUAVOICE_TUNNEL_KEY',
            '--health-listen-addr','127.0.0.1:0'],capture_output=True,encoding='utf-8',errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
        if result.returncode:raise RuntimeError('连接配置没有创建成功，请检查连接编号。')
    config=yaml.safe_load(PROFILE.read_text(encoding='utf-8'))
    tunnel_id=config['control_plane']['tunnel_id']
    command=config['mcp']['commands'][0]['command']
    env=os.environ.copy();env['LINGUAVOICE_TUNNEL_KEY']=key
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    args=[str(CLIENT),'runtimes','connect','--alias','linguavoice','--profile','linguavoice',
          '--profile-dir',str(PROFILE_DIR),'--tunnel-id',tunnel_id,'--runtime-api-key',
          'env:LINGUAVOICE_TUNNEL_KEY','--mcp-command',command,'--json']
    result=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',
                          errors='replace',creationflags=flags,timeout=100)
    sanitized=(result.stdout+'\n'+result.stderr).replace(key,'[redacted]')
    (ROOT/'private/tunnel-connect.log').write_text(sanitized,encoding='utf-8')
    if result.returncode:
        raise RuntimeError('隧道未启动成功。错误已写入本机脱敏日志，我会继续排查；请勿把密钥发到聊天。')
    if remember:
        import win32crypt
        blob=win32crypt.CryptProtectData(key.encode('utf-8'),'LinguaVoice tunnel runtime',None,None,None,0)
        SECRET.write_bytes(blob)
    STATUS.write_text(json.dumps({'started':True,'tunnel_id':tunnel_id,'encrypted_key_saved':remember},ensure_ascii=False),encoding='utf-8')
    if remember or SECRET.is_file():
        import sys
        subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),str(ROOT/'run_background.py')],cwd=ROOT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    return '本机连接已启动。请在 ChatGPT 添加你自己的连接并启用 LinguaVoice。\n完成一次授权后，可在控制台开启“登录电脑后自动准备”。'


def main():
    window=tk.Tk();window.title('LinguaVoice · 连接 ChatGPT');window.geometry('740x650')
    icon=ROOT/'assets/branding/linguavoice.ico'
    if icon.is_file():window.iconbitmap(str(icon))
    ttk.Style().configure('.',font=('Microsoft YaHei UI',10))
    frame=ttk.Frame(window,padding=22);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='把声音接到 ChatGPT',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w',pady=(0,14))
    ttk.Label(frame,text='首次连接后：打开 ChatGPT，直接 @ 选择 LinguaVoice。',font=('Microsoft YaHei UI',11,'bold'),wraplength=650).pack(anchor='w',pady=(0,12))
    tabs=ttk.Notebook(frame);tabs.pack(fill='both',expand=True)
    guide=ttk.Frame(tabs,padding=18);remote=ttk.Frame(tabs,padding=18);local=ttk.Frame(tabs,padding=18)
    tabs.add(guide,text='日常使用');tabs.add(remote,text='高级：首次连接 / 修复');tabs.add(local,text='高级：Work / Codex')
    activation=ttk.LabelFrame(guide,text='直接 @，不用额外口令',padding=12)
    activation.pack(fill='x',pady=(0,12))
    ttk.Label(activation,text='1. 在 ChatGPT 输入框输入 @。\n2. 从列表选择 LinguaVoice，再发送你想聊的话。\n3. 后续正常聊天，不用每句话重新 @。',font=('Microsoft YaHei UI',10,'bold'),wraplength=680).pack(anchor='w')
    ttk.Label(activation,text='停止播放：在聊天里说“停止播放”，或点网页里的“停止播放”。\n关闭后续语音：打开“网页设置”，关闭“让 AI 说话”。\n恢复语音：重新打开“让 AI 说话”。',wraplength=680).pack(anchor='w',pady=(6,0))
    ttk.Label(activation,text='新聊天再 @ 选择一次。首次连接完成后，不用每天打开本窗口。\n网页开启“登录电脑后自动准备”，连接会在后台恢复。',wraplength=680).pack(anchor='w',pady=(6,0))
    def open_settings():
        subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),'-m','linguavoice.server','--open'],cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
    ttk.Button(guide,text='网页设置',command=open_settings).pack(anchor='w',pady=10)
    ttk.Button(guide,text='首次连接 / 修复（已有连接无需重复设置）',command=lambda:tabs.select(remote)).pack(anchor='w',pady=12)
    ttk.Label(local,text='仅用于支持本地工具的 Work / Codex 会话',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=12)
    ttk.Label(local,text='本地插件不需要填写 API 密钥，声音仍在本机生成。\n\n普通 ChatGPT 聊天请使用“高级：首次连接 / 修复”。当前实测中，普通聊天即使能选中本地插件，也不能调用其声音工具。\n\n如果普通聊天已有可用的 LinguaVoice，请保留它；两种连接方式不是新旧版本，不要为安装本地插件而卸载原连接。\n\n免费账号是否支持相应入口，需要用实际账号验证。',wraplength=570).pack(anchor='w',pady=10)
    notice=tk.StringVar(value='已有可用连接请直接保留，无需反复安装。')
    messages=queue.Queue();buttons=[]
    def run_action(action):
        for button in buttons:button.configure(state='disabled')
        notice.set('正在连接，请稍候…')
        def work():
            try:messages.put(action())
            except Exception as exc:messages.put(str(exc))
        threading.Thread(target=work,daemon=True).start()
    def install_local():
        from register_desktop import register
        return register()
    local_button=ttk.Button(local,text='安装 Work / Codex 本地插件',command=lambda:run_action(install_local))
    local_button.pack(anchor='w',pady=16);buttons.append(local_button)
    ttk.Label(remote,text='普通聊天通过你自己的安全连接调用本机声音。\n已有 LinguaVoice：在 ChatGPT 的插件页面启用原连接。\n连接已保存时，无需重填编号和密钥，点击下方“恢复已保存连接”。\n首次连接需要账号支持自定义 MCP，并使用自己的编号和运行密钥。',wraplength=570).pack(anchor='w',pady=8)
    ttk.Button(remote,text='打开 ChatGPT 插件页面',command=lambda:webbrowser.open('https://chatgpt.com/plugins')).pack(anchor='w',pady=(0,6))
    ttk.Button(remote,text='打开官方连接管理页',command=lambda:webbrowser.open('https://platform.openai.com/settings/organization/tunnels')).pack(anchor='w')
    ttk.Label(remote,text='连接编号（tunnel_ 开头）').pack(anchor='w',pady=(12,0))
    tunnel_entry=ttk.Entry(remote);tunnel_entry.pack(fill='x',pady=5)
    if PROFILE.is_file():
        tunnel_entry.insert(0,'已保存本机连接，无需重新填写');tunnel_entry.configure(state='disabled')
    ttk.Label(remote,text='运行密钥（仅需 Tunnels Read + Use 权限）').pack(anchor='w')
    entry=ttk.Entry(remote,show='●');entry.pack(fill='x',pady=5)
    remember=tk.BooleanVar(value=False)
    ttk.Checkbutton(remote,text='在此 Windows 账户下加密保存',variable=remember).pack(anchor='w',pady=6)
    def begin_remote():
        secret=entry.get().strip();entry.delete(0,'end')
        if not secret and SECRET.is_file():
            try:
                import win32crypt
                secret=win32crypt.CryptUnprotectData(SECRET.read_bytes(),None,None,None,0)[1].decode('utf-8')
            except Exception:notice.set('无法读取保存的凭据，请重新输入。');return
        save=remember.get();chosen=tunnel_entry.get().strip() if not PROFILE.is_file() else None
        def action():
            try:return connect(secret,save,chosen)
            except Exception as exc:raise RuntimeError(str(exc).replace(secret,'[redacted]') if secret else str(exc)) from None
        run_action(action)
    remote_button=ttk.Button(remote,text='恢复已保存连接' if SECRET.is_file() else '连接自己的安全隧道',command=begin_remote)
    remote_button.pack(anchor='w',pady=12);buttons.append(remote_button)
    ttk.Label(frame,textvariable=notice,wraplength=620).pack(anchor='w',pady=(15,0))
    def poll():
        try:
            notice.set(messages.get_nowait())
            for button in buttons:button.configure(state='normal')
        except queue.Empty:pass
        window.after(200,poll)
    window.after(200,poll);window.mainloop()


if __name__=='__main__':main()
