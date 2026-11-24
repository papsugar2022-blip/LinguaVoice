import copy
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from .model_versions import validate_version

ROOT = Path(__file__).resolve().parents[1]


def display_voice_name(config, selected_language):
    name = str(config.get('name') or config.get('id') or '新角色')
    return name


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def speed_value(value):
    value = float(value)
    if not math.isfinite(value) or not 0.6 <= value <= 1.5:
        raise ValueError('合成语速必须在 0.6～1.5 之间')
    return value


class Store:
    def __init__(self, directory=None):
        self.directory = Path(directory or ROOT / 'private/console')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.path = self.directory / 'settings.json'
        token_path = self.directory / 'access-token.txt'
        if not token_path.exists():
            token_path.write_text(secrets.token_urlsafe(36), encoding='ascii')
        self.token = token_path.read_text(encoding='ascii').strip()
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        else:
            self.data = {'schema_version': 1, 'settings': {'speed': 1.0, 'autoplay': True,
                'speech_enabled': True, 'desktop_playback': True, 'default_voice': '', 'role_bindings': {}}, 'voices': {}}
            if directory is None:
                selection_path=ROOT/'private/install-selection.json'
                selection=json.loads(selection_path.read_text(encoding='utf-8')) if selection_path.is_file() else {}
                voice = self.default_voice() if selection.get('qwen',True) else None
                if voice:
                    self.data['voices'][voice['id']] = voice
                    self.data['settings']['default_voice'] = voice['id']
            write_json(self.path, self.data)
        from .catalog import provenance
        for voice in self.data['voices'].values():
            digest = voice.get('archive_sha256', '')
            known = provenance(digest)
            if not voice.get('provenance') or known['license_status'] == 'publisher_statement_checked':
                voice['provenance'] = known
                voice['license'] = known['license']

    @staticmethod
    def default_voice():
        config = ROOT/'config/default-voice.json'
        if not config.is_file():
            return None
        voice = json.loads(config.read_text(encoding='utf-8'))
        runtime_path = ROOT/'private/runtimes.json'
        runtimes = json.loads(runtime_path.read_text(encoding='utf-8')) if runtime_path.is_file() else {}
        voice['model_dir'] = str(Path(runtimes.get(voice['provider'], {}).get('model_dir') or ROOT/voice['model_dir']))
        for ref in [voice['reference'], *voice['references']]:
            ref['audio'] = str(ROOT/ref['audio'])
        voice['prompt_file'] = str(ROOT/voice['prompt_file'])
        return voice

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def public_state(self):
        data = self.snapshot()
        voices = []
        for voice in data['voices'].values():
            from .model_versions import VERSIONS
            if voice['provider'] == 'gpt-sovits' and voice['version'] not in VERSIONS:
                continue  # Keep private files and settings; hide engines disabled in this release.
            voices.append({k: voice[k] for k in ('id', 'name', 'provider', 'version', 'profile', 'license')})
            voices[-1]['reference_locked'] = voice.get('reference_locked', False)
            voices[-1]['reference_text'] = voice['reference']['text']
            voices[-1]['reference_count'] = len(voice['references'])
            voices[-1]['provenance'] = voice.get('provenance', {})
            voices[-1]['model_selection'] = voice.get('model_selection')
            from .languages import LANGUAGES
            voices[-1]['languages'] = LANGUAGES.get(voice['provider'], ())
        return {'settings': data['settings'], 'voices': voices}

    def update_settings(self, changes):
        allowed = {'speed', 'autoplay', 'speech_enabled', 'default_voice', 'role_bindings', 'desktop_playback'}
        if set(changes) - allowed:
            raise ValueError('包含未知设置')
        with self.lock:
            new = {**self.data['settings'], **changes}
            new['speed'] = speed_value(new['speed'])
            new.setdefault('desktop_playback', True)
            for key in ('autoplay', 'speech_enabled', 'desktop_playback'):
                if type(new[key]) is not bool:
                    raise ValueError(f'{key} 必须是布尔值')
            if new['default_voice'] not in self.data['voices'] and new['default_voice'] != '':
                raise ValueError('默认音色不存在')
            bindings = new['role_bindings']
            if not isinstance(bindings, dict) or len(bindings) > 50:
                raise ValueError('角色绑定无效')
            for role, voice in bindings.items():
                if not isinstance(role, str) or not 1 <= len(role.strip()) <= 80 or voice not in self.data['voices']:
                    raise ValueError('角色名称或绑定音色无效')
            self.data['settings'] = new
            write_json(self.path, self.data)
            return copy.deepcopy(new)

    def voice(self, voice_id=None, role=None):
        data = self.snapshot()
        if role:
            bound = data['settings']['role_bindings'].get(role)
            if not bound:
                raise ValueError('该角色尚未绑定音色；请先在控制台绑定，不会自动冒用其他音色')
            if voice_id and voice_id != bound:
                raise ValueError('指定音色与角色绑定冲突，请先在控制台确认')
            voice_id = bound
        voice_id = voice_id or data['settings']['default_voice']
        if voice_id not in data['voices']:
            raise ValueError('未找到指定音色，请先导入并选择')
        return data['voices'][voice_id]

    def choose_reference(self, voice_id, index):
        with self.lock:
            voice = self.data['voices'][voice_id]
            if voice.get('reference_locked', False):
                raise ValueError('已确认的参考录音已锁定；可另行导入为新音色进行试验')
            if type(index) is not int or not 0 <= index < len(voice['references']):
                raise ValueError('参考录音索引无效')
            import soundfile as sf
            reference = voice['references'][index]
            info = sf.info(reference['audio'])
            maximum = 30 if voice['provider'] == 'qwen3-tts' else 10
            if not 3 <= info.duration <= maximum:
                raise ValueError(f'请选择3～{maximum}秒的参考录音')
            voice['reference'] = copy.deepcopy(reference)
            if voice['provider'] == 'qwen3-tts':
                voice['reference_sha256'] = hashlib.sha256(Path(reference['audio']).read_bytes()).hexdigest()
                voice.pop('prompt_file', None)
            write_json(self.path, self.data)

    def export_preset(self):
        # No local paths, keys, reference recordings, model weights, or conversation text.
        return {'format': 'linguavoice-settings', 'schema_version': 1,
                'settings': self.snapshot()['settings']}

    def import_preset(self, data):
        if data.get('format') != 'linguavoice-settings' or data.get('schema_version') != 1:
            raise ValueError('不是受支持的 LinguaVoice 设置文件')
        settings = dict(data['settings'])
        voices = self.snapshot()['voices']
        missing = [v for v in settings.get('role_bindings', {}).values() if v not in voices]
        if settings.get('default_voice') and settings['default_voice'] not in voices:
            missing.append(settings['default_voice'])
        if missing:
            raise ValueError('请先导入设置引用的音色：' + ', '.join(sorted(set(missing))))
        return self.update_settings(settings)

    def import_zip(self, archive, display_name='', trusted=False, progress=None):
        if trusted is not True:
            raise ValueError('需要确认模型来自可信来源且你有权使用')
        report = progress or (lambda *args: None)
        report(5, '正在检查角色包')
        archive.seek(0)
        digest = hashlib.file_digest(archive, 'sha256').hexdigest()
        archive.seek(0)
        with self.lock:
            existing = next((v for v in self.data['voices'].values() if v.get('archive_sha256') == digest), None)
            if existing:
                return {'id': existing['id'], 'name': existing['name'],
                        'reference_count': len(existing['references']), 'duplicate': True}
        voice_id = 'voice-' + uuid.uuid4().hex[:12]
        dest = (self.directory / 'voices' / voice_id).resolve()
        dest.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(archive) as z:
                total = 0
                if len(z.infolist()) > 12000:
                    raise ValueError('模型包文件数量超限')
                seen = set()
                for index, item in enumerate(z.infolist()):
                    filename = item.filename
                    if not item.flag_bits & 0x800:
                        try:
                            decoded = filename.encode('cp437').decode('gb18030')
                            if re.search(r'[\u3400-\u9fff]', decoded):
                                filename = decoded
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            pass
                    path = PurePosixPath(filename.replace('\\', '/'))
                    if path.is_absolute() or '..' in path.parts or any(':' in p for p in path.parts):
                        raise ValueError('模型包含有不安全路径')
                    if stat.S_ISLNK(item.external_attr >> 16):
                        raise ValueError('模型包不允许包含符号链接')
                    target = (dest / Path(*path.parts)).resolve()
                    if not target.is_relative_to(dest) or str(target).lower() in seen:
                        raise ValueError('模型包路径冲突')
                    seen.add(str(target).lower())
                    total += item.file_size
                    if total > 3 * 1024**3 or item.file_size > 2 * 1024**3:
                        raise ValueError('模型包展开体积超限')
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if target.suffix.lower() not in {'.json', '.list', '.txt', '.md', '.pth', '.ckpt', '.wav', '.ogg', '.flac', '.mp3'} and target.name.lower() not in {'license','copying','notice','authors'}:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(item) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                    if index % 25 == 0:
                        report(10 + round(65 * (index + 1) / len(z.infolist())), '正在安装角色文件')
            report(78, '正在检查声音和参考录音')
            manifests = list(dest.rglob('voice.json'))
            if manifests:
                if len(manifests) != 1:
                    raise ValueError('每个 ZIP 只能包含一个声音清单')
                return self.import_reference_pack(manifests[0], dest, voice_id, digest, display_name)
            configs = list(dest.rglob('config.json'))
            configs = [p for p in configs if (p.parent/'models/gpt.ckpt').is_file() and (p.parent/'models/sovits.pth').is_file()]
            if not configs:
                from .import_formats import normalize_raw_pack
                configs = [normalize_raw_pack(dest)]
            if len(configs) != 1:
                raise ValueError('这不是完整的角色安装包。请使用作者提供的 voice.zip；单独的录音包或多个角色的合集暂不能直接安装')
            pack = configs[0].parent
            config = json.loads(configs[0].read_text(encoding='utf-8-sig'))
            version = validate_version(pack/'models/sovits.pth', config.get('version'))
            audio_by_name = {}
            for p in pack.rglob('*'):
                if p.suffix.lower() in {'.wav', '.ogg', '.flac', '.mp3'}:
                    if p.name in audio_by_name:
                        raise ValueError('参考录音存在重复文件名，无法可靠匹配标注')
                    audio_by_name[p.name] = p
            references = []
            for listing in pack.rglob('*.list'):
                for line in listing.read_text(encoding='utf-8-sig').splitlines():
                    fields = line.split('|', 3)
                    if len(fields) != 4:
                        continue
                    filename, _, language, text = fields
                    p = audio_by_name.get(filename.replace('\\', '/').split('/')[-1])
                    lang = language.lower()
                    if p and text.strip() and lang in {'zh', 'en', 'ja', 'ko', 'yue'}:
                        references.append({'audio': str(p), 'text': text.strip(), 'language': lang})
            if not references:
                raise ValueError('没有找到带文本标注的参考录音')
            import soundfile as sf
            selected = next((r for r in references if 3 <= sf.info(r['audio']).duration <= 10), None)
            if selected is None:
                raise ValueError('没有3～10秒的参考录音')
            from .catalog import provenance
            rights = provenance(digest)
            voice = {'id': voice_id, 'name': str(display_name or display_voice_name(config, selected['language']))[:80],
                'provider': 'gpt-sovits', 'version': version, 'profile': 'original',
                'gpt': str(pack/'models/gpt.ckpt'), 'sovits': str(pack/'models/sovits.pth'),
                'reference': selected, 'references': references, 'trusted': True,
                'license': rights['license'], 'provenance': rights,
                'archive_sha256': digest, 'model_selection': config.get('model_selection')}
            with self.lock:
                updated = copy.deepcopy(self.data)
                updated['voices'][voice_id] = voice
                write_json(self.path, updated)
                self.data = updated
            return {'id': voice_id, 'name': voice['name'], 'reference_count': len(references)}
        except Exception as exc:
            # Only remove the freshly created, verified private import directory.
            if dest.parent == (self.directory/'voices').resolve() and dest.name == voice_id:
                shutil.rmtree(dest)
            if isinstance(exc, zipfile.BadZipFile):
                raise ValueError('这个 ZIP 已损坏或没有下载完整，请重新下载后再拖入') from exc
            raise

    def rename_voice(self, voice_id, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('请填写1～80字的角色名称')
        with self.lock:
            if voice_id not in self.data['voices']:
                raise ValueError('没有找到这个角色')
            self.data['voices'][voice_id]['name'] = name.strip()
            write_json(self.path, self.data)

    def import_reference_pack(self, manifest, directory, voice_id, digest, display_name):
        config = json.loads(manifest.read_text(encoding='utf-8-sig'))
        if config.get('format') != 'linguavoice-qwen-reference' or config.get('schema_version') != 1:
            raise ValueError('不支持这个声音清单格式')
        ref = config.get('reference', {})
        relative = PurePosixPath(str(ref.get('audio', '')).replace('\\', '/'))
        if relative.is_absolute() or '..' in relative.parts or any(':' in p for p in relative.parts):
            raise ValueError('参考录音路径无效')
        audio = (manifest.parent/Path(*relative.parts)).resolve()
        if not audio.is_relative_to(directory) or not audio.is_file():
            raise ValueError('声音包中没有找到参考录音')
        text = ref.get('text')
        from .languages import LANGUAGES
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000 or ref.get('language') not in LANGUAGES['qwen3-tts']:
            raise ValueError('参考台词或语言无效')
        import soundfile as sf
        if not 3 <= sf.info(audio).duration <= 30:
            raise ValueError('这类参考声音包需要3～30秒录音')
        voice = self.default_voice()
        if not voice:
            raise ValueError('请先安装默认声音引擎')
        reference = {'audio':str(audio), 'text':text.strip(), 'language':ref['language']}
        from .catalog import provenance
        rights = provenance(digest)
        voice.update(id=voice_id, name=str(display_name or config.get('name') or '新声音')[:80],
                     reference=reference, references=[reference], reference_locked=False,
                     reference_sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
                     archive_sha256=digest, provenance=rights, license=rights['license'])
        voice.pop('prompt_file', None)
        with self.lock:
            updated=copy.deepcopy(self.data);updated['voices'][voice_id]=voice
            write_json(self.path, updated);self.data=updated
        return {'id':voice_id, 'name':voice['name'], 'reference_count':1}
