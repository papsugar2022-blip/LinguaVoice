"""Real default-voice smoke check, safe to run on a fresh installation."""
import json
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider',choices=['qwen3-tts','gpt-sovits'],default='qwen3-tts')
    provider=parser.parse_args().provider
    from linguavoice.storage import Store, write_json
    from linguavoice.backends import BackendRouter
    import soundfile as sf
    import sounddevice as sd
    store = Store()
    voice = store.default_voice() if provider=='qwen3-tts' else next((v for v in store.data['voices'].values() if v['provider']=='gpt-sovits' and v.get('version')=='v5turbo'),None)
    if not voice:
        if provider=='qwen3-tts':raise RuntimeError('秋猫声音资料缺失，请重新安装所选组件')
        sd.query_devices(kind='output')
        write_json(ROOT/'private/install-verification-gpt-sovits.json',{'provider':provider,'status':'needs_voice','synthesis_verified':False,'output_device_detected':True})
        print('V5 components installed; import a licensed V5 voice ZIP in web settings to test speech.',flush=True)
        return
    output = ROOT/'private/install-test.wav'
    backend = BackendRouter()
    try:
        result = backend.synthesize(voice, '你好，我已经准备好了。我们可以开始聊天了。', 'zh', 1.0, output)
    finally:
        backend.unload()
    info = sf.info(output)
    assert info.duration > 1 and info.samplerate > 16000
    sd.query_devices(kind='output')
    if voice['id'] not in store.data['voices']:
        store.data['voices'][voice['id']] = voice
        if not store.data['settings']['default_voice']:
            store.data['settings']['default_voice']=voice['id']
        write_json(store.path, store.data)
    write_json(ROOT/f'private/install-verification-{provider}.json', {'passed':True, 'provider':provider, 'result':result,
        'output_device_detected':True, 'audio_quality_user_reviewed':False})
    evidence=ROOT/'private/verified-capabilities.json'
    existing=json.loads(evidence.read_text(encoding='utf-8')) if evidence.is_file() else {}
    write_json(evidence,{**existing,provider:True})
    print('PASS: default voice produced a real WAV; audio output device detected.', flush=True)

if __name__ == '__main__':
    main()
