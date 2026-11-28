"""Timing for already-selected speech. Never selects or discards teaching content."""
import re
import tempfile
import time
from pathlib import Path


def spoken_segments(text):
    # Newlines are semantic boundaries supplied by the conversation AI. Sentence
    # punctuation is a fallback for old callers, not a decision about what to read.
    parts = re.split(r'\n+|(?<=[。！？!?])\s*|(?<=\.)\s+(?=[A-Z\u4e00-\u9fff])', text)
    return [part.strip() for part in parts if part.strip()]


def synthesize_segments(backend, voice, text, language, speed, destination):
    import numpy as np
    import soundfile as sf
    started = time.perf_counter()
    parts = spoken_segments(text)
    pause = float(voice.get('sentence_pause', .45))
    timeline, buffers, offset, rate, result = [], [], 0, None, {}
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix='segments-', dir=destination.parent) as folder:
        for index, part in enumerate(parts):
            path = Path(folder) / f'{index}.wav'
            result = backend.synthesize(voice, part, language, speed, path)
            audio, current_rate = sf.read(path, dtype='float32', always_2d=True)
            if rate is not None and current_rate != rate:
                raise RuntimeError('分段音频采样率不一致，已停止合成')
            rate = current_rate
            if not len(audio) or not np.isfinite(audio).all():
                raise RuntimeError('分段音频无效')
            timeline.append({'text': part, 'start': offset / rate, 'end': (offset + len(audio)) / rate})
            buffers.append(audio)
            offset += len(audio)
            if index + 1 < len(parts):
                silence = np.zeros((round(pause * rate), audio.shape[1]), dtype='float32')
                buffers.append(silence)
                offset += len(silence)
        sf.write(destination, np.concatenate(buffers), rate)
    return {**result, 'seconds': offset / rate, 'sample_rate': rate,
            'generation_seconds': time.perf_counter() - started, 'segments': timeline,
            'sentence_pause': pause}
