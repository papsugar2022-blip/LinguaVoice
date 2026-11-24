"""Normalize a single-voice raw archive without assuming a character identity."""
import json
from pathlib import Path
import re
import shutil


def normalize_raw_pack(folder):
    folder = Path(folder)
    gpt = list(folder.rglob('*.ckpt'))
    sovits = list(folder.rglob('*.pth'))
    if not gpt or not sovits:
        raise ValueError('ZIP 缺少配对的 GPT 与 SoVITS 模型，请下载完整声音包')

    def choose(paths):
        if len(paths) == 1:
            return paths[0]
        candidates = []
        for path in paths:
            match = re.fullmatch(r'(.+?)[_-]e(\d+)(?:[_-].*)?', path.stem, re.I)
            if not match:
                raise ValueError('ZIP 有多份模型，无法确定配对；请每个包只保留一对需要的权重')
            candidates.append((match[1].casefold(), int(match[2]), path))
        if len({x[0] for x in candidates}) != 1 or len({x[1] for x in candidates}) != len(candidates):
            raise ValueError('ZIP 包含多个声音或不明确的训练轮次，请分别导入')
        return max(candidates, key=lambda x: x[1])[2]

    selected_gpt, selected_sovits = choose(gpt), choose(sovits)
    listings = list(folder.rglob('*.list'))
    if not listings:
        lines = []
        for audio in folder.rglob('*'):
            if audio.suffix.lower() not in {'.wav', '.ogg', '.flac', '.mp3'}:
                continue
            # Some authors provide Chinese reference recordings named by their full transcript.
            # This identifies import metadata only; it never chooses what the AI should speak.
            transcript = audio.stem.strip()
            if len(transcript) >= 4 and re.fullmatch(r'[\u3400-\u9fff，。！？、；：“”‘’…—\s]+', transcript):
                lines.append(f'{audio.name}|reference|zh|{transcript}')
        if not lines:
            raise ValueError('缺少参考台词标注：请提供 .list 文件，或用完整中文台词命名参考录音')
        (folder/'reference.list').write_text('\n'.join(lines), encoding='utf-8')
    model_dir = folder/'models'
    model_dir.mkdir(exist_ok=True)
    for source, name in [(selected_gpt, 'gpt.ckpt'), (selected_sovits, 'sovits.pth')]:
        target = model_dir/name
        if source != target:
            shutil.copy2(source, target)
    from .model_versions import validate_version
    version = validate_version(model_dir/'sovits.pth')
    selection = {'gpt': selected_gpt.name, 'sovits': selected_sovits.name,
                 'strategy': '同一模型系列采用最高训练轮次；这不代表音质最好'}
    config = {'name': '新声音', 'version': version, 'model_selection': selection}
    path = folder/'config.json'
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    return path
