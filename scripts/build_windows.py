"""Build an uncredentialed installer and source archive. No model or environment bundled."""
import argparse
import json
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('destination');args=parser.parse_args()
    output=Path(args.destination).resolve();output.mkdir(parents=True,exist_ok=True)
    work=(ROOT/'private/package-build'/uuid.uuid4().hex).resolve()
    assert work.is_relative_to((ROOT/'private/package-build').resolve())
    work.mkdir(parents=True)
    source=work/'source.zip'
    subprocess.run([sys.executable,str(ROOT/'scripts/package_source.py'),str(source)],check=True,cwd=ROOT)
    manifest=json.loads((ROOT/'packaging/downloads.json').read_text(encoding='utf-8'))
    assert {'uv','engine','base','nltk','tunnel'} <= set(manifest)
    with zipfile.ZipFile(source) as z:
        assert not any('/private/' in name or '/vendor/' in name or '/.venv/' in name for name in z.namelist())
        for name in z.namelist():
            if name.endswith(('.py','.md','.js','.html','.json','.yaml')):
                value=z.read(name)
                import re
                assert not re.search(rb'sk-[A-Za-z0-9_-]{25,}',value), 'Credential-like content in '+name
    licenses=work/'LICENSES';licenses.mkdir()
    shutil.copy2(ROOT/'LICENSE',licenses/'LinguaVoice-MIT.txt')
    shutil.copy2(ROOT/'THIRD_PARTY_NOTICES.md',licenses/'THIRD_PARTY_NOTICES.md')
    python=Path(sys.base_prefix)
    for candidate in (python/'LICENSE.txt',python/'tcl/tcl8.6/license.terms',python/'tcl/tk8.6/license.terms'):
        if candidate.is_file():shutil.copy2(candidate,licenses/(candidate.parent.name+'-'+candidate.name))
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--windowed',
        '--name','LinguaVoice-安装器','--icon',str(ROOT/'assets/branding/linguavoice.ico'),
        '--workpath',str(work/'build'),'--specpath',str(work),
        '--distpath',str(output),'--add-data',str(source)+';.',
        '--add-data',str(ROOT/'packaging/downloads.json')+';.',
        '--add-data',str(ROOT/'assets/branding/linguavoice.ico')+';.',
        '--add-data',str(licenses)+';LICENSES',str(ROOT/'packaging/setup_app.py')],cwd=ROOT,check=True)
    shutil.copy2(source,output/'LinguaVoice-0.3-源码.zip')
    shutil.copytree(licenses,output/'LinguaVoice-许可说明',dirs_exist_ok=True)
    print('Installer and source built; only the approved default reference is included; no third-party voices, credentials or environments.')


if __name__=='__main__':main()
