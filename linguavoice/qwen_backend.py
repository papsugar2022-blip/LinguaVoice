"""Reference-conditioned Qwen speech backend; voice identity is supplied as data."""
import hashlib
import os
from pathlib import Path
import time


class QwenBackend:
    def __init__(self):
        self.model=None
        self.key=None
        self.prompt=None

    def unload(self):
        self.model=None;self.prompt=None;self.key=None
        import gc
        gc.collect()
        import torch
        torch.cuda.empty_cache()

    def synthesize(self, voice, text, language, speed, destination):
        languages={'zh':'Chinese','en':'English','ja':'Japanese','ko':'Korean','de':'German',
                   'fr':'French','ru':'Russian','pt':'Portuguese','es':'Spanish','it':'Italian'}
        if language not in languages:
            raise ValueError('这个后端尚未接入所选语言，不会擅自换语言')
        started=time.perf_counter()
        import torch
        import numpy as np
        import soundfile as sf
        from faster_qwen3_tts import FasterQwen3TTS
        reference=voice['reference']
        fingerprint=hashlib.sha256(Path(reference['audio']).read_bytes()).hexdigest()
        if fingerprint != voice['reference_sha256']:
            raise ValueError('参考声音与已确认配置不一致，已停止合成')
        key=(voice['model_dir'],fingerprint,reference['text'])
        if self.key!=key:
            self.unload()
            if not torch.cuda.is_available():raise RuntimeError('CUDA 不可用，请检查显卡驱动')
            self.model=FasterQwen3TTS.from_pretrained(voice['model_dir'],device='cuda',dtype=torch.bfloat16,
                attn_implementation='sdpa',max_seq_len=1024,backend='torch',local_files_only=True)
            self.model.warmup(prefill_len=100)
            prompt_file=voice.get('prompt_file')
            if prompt_file and Path(prompt_file).is_file():
                saved=torch.load(prompt_file,map_location='cpu',weights_only=True)
                if saved['sha256']!=fingerprint or saved['ref_text']!=reference['text']:
                    raise ValueError('保存的声音提示与参考录音不一致，已停止合成')
                self.prompt={k:[v.to(self.model.model.device) if torch.is_tensor(v) else v for v in values]
                             for k,values in saved['prompt'].items()}
            else:
                items=self.model.model.create_voice_clone_prompt(ref_audio=reference['audio'],ref_text=reference['text'],x_vector_only_mode=False)
                self.prompt=self.model.model._prompt_items_to_voice_clone_prompt(items)
            self.key=key
        chunks=[];rates=set();steps=0
        # The model receives the AI-selected utterance verbatim; selection is not a regex filter.
        for audio,rate,timing in self.model.generate_voice_clone_streaming(text=text,language=languages[language],
                voice_clone_prompt=self.prompt,ref_text=reference['text'],chunk_size=8,max_new_tokens=1024):
            chunks.append(np.asarray(audio,dtype=np.float32).reshape(-1));rates.add(rate)
            steps=int(timing.get('total_steps_so_far',steps+timing.get('chunk_steps',0)))
        torch.cuda.synchronize()
        if not chunks or len(rates)!=1 or steps>=1024:
            raise RuntimeError('语音未完整生成，请按意思缩短这一段后重试')
        audio=np.concatenate(chunks);rate=rates.pop()
        if not audio.size or not np.isfinite(audio).all() or not np.any(audio):
            raise RuntimeError('模型未返回有效声音')
        if speed!=1.0:
            import librosa
            audio=librosa.effects.time_stretch(audio,rate=speed)
        sf.write(destination,audio,rate)
        return {'seconds':len(audio)/rate,'sample_rate':rate,'generation_seconds':time.perf_counter()-started,
                'precision':'bf16','model_version':voice['version'],'acceleration':'torch',
                'speed_mode':'original' if speed==1.0 else 'pitch_preserving_time_stretch'}
