"""Real default-voice smoke check, safe to run on a fresh installation."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    from linguavoice.storage import Store, write_json
    from linguavoice.backends import BackendRouter
    import soundfile as sf
    import sounddevice as sd
    store = Store()
    voice = store.default_voice()
    if not voice:
        raise RuntimeError('默认声音资料缺失，请重新安装')
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
        write_json(store.path, store.data)
    write_json(ROOT/'private/install-verification.json', {'passed':True, 'provider':'qwen3-tts', 'result':result,
        'output_device_detected':True, 'audio_quality_user_reviewed':False})
    evidence=ROOT/'private/verified-capabilities.json'
    existing=json.loads(evidence.read_text(encoding='utf-8')) if evidence.is_file() else {}
    write_json(evidence,{**existing,'qwen3-tts':True})
    print('PASS: default voice produced a real WAV; audio output device detected.', flush=True)

if __name__ == '__main__':
    main()
