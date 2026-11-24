"""Register the supplied local plugin using the host's supported CLI."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import winreg
ROOT=Path(__file__).resolve().parents[1]

def find_desktop_cli():
    executable=shutil.which('codex')
    if executable:
        return executable
    # Explorer-launched windows do not inherit the desktop agent's PATH.
    powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
            "Get-AppxPackage | Where-Object { $_.Name -eq 'OpenAI.Codex' -or $_.Name -eq 'OpenAI.ChatGPT' } | "
            "Sort-Object Version -Descending | Select-Object -ExpandProperty InstallLocation | ConvertTo-Json -Compress"],
            capture_output=True,encoding='utf-8',errors='replace',timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            return None
        locations=json.loads(result.stdout.strip() or '[]')
        if isinstance(locations,str):
            locations=[locations]
        for location in locations:
            candidate=Path(location)/'app/resources/codex.exe'
            if candidate.is_file():
                return str(candidate)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        pass
    return None


def register():
    executable=find_desktop_cli()
    if not executable:
        raise RuntimeError('未找到桌面插件安装工具。请先安装支持插件的 ChatGPT/Codex 桌面客户端；当前客户端可能没有此入口。')
    (ROOT/'private').mkdir(parents=True,exist_ok=True)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\LinguaVoice') as key:
        winreg.SetValueEx(key,'InstallPath',0,winreg.REG_SZ,str(ROOT))
    for arguments in [('plugin','marketplace','add',str(ROOT)), ('plugin','add','linguavoice@linguavoice-local')]:
        result=subprocess.run([executable,*arguments],cwd=ROOT,capture_output=True,encoding='utf-8',errors='replace',
                              timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            (ROOT/'private/plugin-registration.log').write_text(result.stderr+'\n'+result.stdout,encoding='utf-8')
            raise RuntimeError('桌面客户端未完成插件安装，详情已保存在本机日志。请确认客户端支持本地插件。')
    (ROOT/'private/desktop-plugin-installed.json').write_text(json.dumps({'installed':True}),encoding='utf-8')
    return '本地插件已安装。请重新打开支持本地插件的新会话并启用 LinguaVoice；无需运行密钥。免费账号能力仍取决于客户端和额度。'

if __name__=='__main__':
    print(register())
