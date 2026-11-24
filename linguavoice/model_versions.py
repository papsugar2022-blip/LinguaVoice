"""Version-specific runtime policy; never infer compatibility from a filename."""
import hashlib
from pathlib import Path

VERSIONS = {
    'v5turbo': {'sample_steps': 4, 'cfg_rate': 0.0, 'files': (
        'gsv-v5-pretrained/s2Gv5turbo.pth', 'gsv-v5-pretrained/vocoder.pth')},
}
HEADERS = {b'00': 'v1', b'01': 'v2', b'02': 'v3', b'03': 'v3',
           b'04': 'v4', b'05': 'v2Pro', b'06': 'v2ProPlus',
           b'07': 'v5dev', b'08': 'v5turbo'}
BASE_HASHES = {'43797be674a37c1c83ee81081941ed0f': 'v3',
               '4f26b9476d0c5033e04162c486074374': 'v4',
               '6bc5cb2a195cc8ec22b2e723cd4d6f47': 'v5turbo'}


def detect_version(path):
    with Path(path).open('rb') as f:
        header = f.read(8192)
    version = HEADERS.get(header[:2]) or BASE_HASHES.get(hashlib.md5(header).hexdigest())
    if not version:
        raise ValueError('无法可靠识别这份 SoVITS 权重的版本；请检查模型来源，不能只改文件名或配置')
    return version


def validate_version(path, declared=None):
    version = detect_version(path)
    if declared and declared != version:
        raise ValueError(f'模型实际为 {version}，与角色包声明的 {declared} 不符')
    if version not in VERSIONS:
        raise ValueError(f'已识别 {version}，当前版本仅启用 V5Turbo；此版本未启用')
    return version


def check_runtime(voice, base):
    version = validate_version(voice['sovits'], voice['version'])
    if voice['profile'] != 'original' and version != 'v5turbo':
        raise ValueError('当前角色专属配置不能用于其他版本')
    required = list(VERSIONS[version]['files'])
    # The v5 vocoder name follows the installed upstream implementation.
    missing = [f for f in required if not (Path(base)/f).is_file()]
    if missing:
        raise ValueError(f'{version} 基础文件尚未齐全：' + '、'.join(missing))
    if not Path(voice['gpt']).is_file():
        raise ValueError('角色的 GPT 权重文件不存在')
    return {k: VERSIONS[version][k] for k in ('sample_steps', 'cfg_rate')}
