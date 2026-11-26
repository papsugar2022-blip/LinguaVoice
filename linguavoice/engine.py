import gc
import os
import sys
import time
from typing import Protocol
from .storage import ROOT
from .model_versions import check_runtime


class SpeechBackend(Protocol):
    def synthesize(self, voice: dict, text: str, language: str, speed: float, destination): ...
    def unload(self): ...


class GPTSoVITSBackend:
    def __init__(self):
        self.tts = None
        self.loaded_key = None

    def unload(self):
        accelerator = getattr(self.tts, 't2s_accel', None)
        if accelerator is not None:
            accelerator.close()
        self.tts = None
        self.loaded_key = None
        gc.collect()
        if 'torch' in sys.modules:
            sys.modules['torch'].cuda.empty_cache()

    def synthesize(self, voice, text, language, speed, destination):
        engine = ROOT/'vendor/GPT-SoVITS'
        base = engine/'GPT_SoVITS/pretrained_models'
        inference_policy = check_runtime(voice, base)
        os.chdir(engine)
        for p in (str(ROOT), str(engine), str(engine/'GPT_SoVITS')):
            if p not in sys.path:
                sys.path.insert(0, p)
        os.environ.update(version=voice['version'], language='zh_CN', NLTK_DATA=str(ROOT/'private/nltk_data'))
        runtime = str(engine/'runtime')
        if runtime not in os.environ.get('PATH', '').split(os.pathsep):
            os.environ['PATH'] = runtime + os.pathsep + os.environ.get('PATH', '')
        import numpy as np
        import soundfile as sf
        import torch
        from TTS_infer_pack.TTS import TTS, TTS_Config
        from voice_profiles import apply_voice_profile
        # The optional accelerator sets process-wide precision on import. Load it before
        # TTS.run resets the FP32 policy, so the first request follows the same policy as later ones.
        try:
            from GPT_SoVITS.Accel.adapter import AccelInference
        except (ImportError, OSError, RuntimeError):
            pass  # The vendor adapter reports unavailable acceleration and retains ordinary inference.
        # Speech shapes vary each turn; avoid repeated cuDNN algorithm search and its memory spike.
        torch.backends.cudnn.benchmark = False
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA不可用；请检查兼容的NVIDIA驱动和PyTorch，不会静默改为CPU')
        key = (voice['version'], voice['gpt'], voice['sovits'], voice['profile'])
        started = time.perf_counter()
        if self.loaded_key != key:
            self.unload()
            base = engine/'GPT_SoVITS/pretrained_models'
            cfg = TTS_Config({'custom': {'device': 'cuda', 'is_half': False,
                'version': voice['version'], 't2s_weights_path': voice['gpt'],
                'vits_weights_path': voice['sovits'], 'bert_base_path': str(base/'chinese-roberta-wwm-ext-large'),
                'cnhuhbert_base_path': str(base/'chinese-hubert-base')}})
            cfg.configs_path = str(ROOT/'private/console/engine-runtime.yaml')
            self.tts = TTS(cfg)
            if self.tts.configs.version != voice['version']:
                self.unload()
                raise ValueError('实际加载的模型版本与角色包不符，已停止合成')
            apply_voice_profile(self.tts, voice['profile'], base/'gsv-v5-pretrained/s2Gv5turbo.pth')
            self.loaded_key = key
        language_map = {'zh': 'all_zh', 'en': 'en', 'ja': 'all_ja', 'ko': 'all_ko', 'yue': 'all_yue'}
        ref = voice['reference']
        chunks = list(self.tts.run({'text': text, 'text_lang': ('zh' if language == 'zh' else language_map[language]),
            'ref_audio_path': ref['audio'], 'prompt_text': ref['text'],
            'prompt_lang': language_map[ref['language']], 'speed_factor': speed,
            'seed': 20261124, **inference_policy, 'batch_size': 1,
            # Keep the final phrase in its own inference segment; long mixed-language input lost tail words.
            'text_split_method': 'cut5', 'parallel_infer': False, 'split_bucket': False,
            'fragment_interval': 0.0, 'return_fragment': False, 'streaming_mode': False}))
        if not chunks or len({sr for sr, _ in chunks}) != 1:
            raise RuntimeError('合成未返回有效音频')
        sr = chunks[0][0]
        data = np.concatenate([audio for _, audio in chunks])
        if not data.size or not np.isfinite(data).all() or not np.any(data):
            raise RuntimeError('合成返回空白或无效波形')
        sf.write(destination, data, sr)
        accelerator = self.tts.t2s_accel
        return {'seconds': len(data)/sr, 'sample_rate': sr,
                'generation_seconds': time.perf_counter()-started, 'precision': 'fp32',
                'model_version': voice['version'], 'sample_steps': inference_policy['sample_steps'],
                'acceleration': accelerator.backend if accelerator and accelerator.graph_enabled else 'ordinary'}
