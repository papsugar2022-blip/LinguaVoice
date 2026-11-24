"""Allowlist-only export: never include weights, credentials or private output."""
import argparse
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('destination');args=p.parse_args()
    dest=Path(args.destination).resolve();dest.parent.mkdir(parents=True,exist_ok=True)
    files=[ROOT/name for name in ['README.md','LICENSE','THIRD_PARTY_NOTICES.md',
           'requirements-console.txt','requirements-tested.txt','requirements-runtime.txt','requirements-qwen.txt','voice_profiles.py',
           'run_mcp.py','run_background.py','启动控制台.cmd','.gitignore']]
    for folder in ['linguavoice','docs','tests','scripts','patches','desktop-plugin','packaging','config','.agents','.github']:
        files.extend(f for f in (ROOT/folder).rglob('*') if f.is_file() and '__pycache__' not in f.parts
                     and f.suffix in {'.py','.md','.html','.css','.js','.patch','.ps1','.cmd','.yaml','.json'})
    files.extend(ROOT/'assets/default-voice'/name for name in ['reference.wav','prompt.pt','SOURCE.md'])
    files.extend(ROOT/'assets/branding'/name for name in ['linguavoice.ico','linguavoice.png'])
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
        for f in sorted(set(files)):
            if not f.is_relative_to(ROOT):raise ValueError('Unexpected source path')
            z.write(f,Path('LinguaVoice')/f.relative_to(ROOT))
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None
        assert not any('/private/' in n or n.endswith(('.pth','.ckpt','.env')) for n in z.namelist())
        assert [n for n in z.namelist() if n.endswith('.wav')] == ['LinguaVoice/assets/default-voice/reference.wav']
        print(f'{len(z.namelist())} source files, {dest.stat().st_size} bytes; private resources excluded.')

if __name__=='__main__':main()
