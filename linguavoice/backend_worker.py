"""One provider per process, so incompatible dependencies and CUDA memory are isolated."""
import contextlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.update(PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                  HF_HOME=str(ROOT/'private/hf-cache'),HF_HUB_CACHE=str(ROOT/'private/hf-cache/hub'))


def main():
    protocol=sys.stdout
    backend=None
    for line in sys.stdin:
        try:
            request=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                provider=request['voice']['provider']
                if backend is None:
                    if provider=='qwen3-tts':
                        from linguavoice.qwen_backend import QwenBackend
                        backend=QwenBackend()
                    elif provider=='gpt-sovits':
                        from linguavoice.engine import GPTSoVITSBackend
                        backend=GPTSoVITSBackend()
                    else:raise ValueError('未接入此语音引擎')
                result=backend.synthesize(request['voice'],request['text'],request['language'],request['speed'],Path(request['destination']))
            reply={'ok':True,'result':result}
        except Exception as exc:
            import traceback
            traceback.print_exc(file=sys.stderr)
            reply={'ok':False,'error':str(exc)[:500]}
        protocol.write(json.dumps(reply,ensure_ascii=False)+'\n');protocol.flush()


if __name__=='__main__':main()
