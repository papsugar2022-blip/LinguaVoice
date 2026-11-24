import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

from linguavoice.storage import Store, speed_value, write_json
from linguavoice.service import VoiceService
from linguavoice.server import ConsoleHTTPServer
from linguavoice.engine import GPTSoVITSBackend
from linguavoice.model_versions import check_runtime, validate_version
from linguavoice.installer import Installer


class BlockingBackend:
    """No fake audio is produced: this stub is only for queue/privacy unit checks."""
    def __init__(self): self.release=threading.Event()
    def synthesize(self,*args):
        self.release.wait(5)
        return {'seconds':1}
    def unload(self): pass


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        voice={'id':'test','name':'Test fixture','provider':'gpt-sovits','version':'v5turbo',
               'profile':'cfm-base-50','reference_locked':True,'license':'fixture','reference':{'text':'reference'},'references':[]}
        self.store.data['voices']['test']=voice
        self.store.update_settings({'default_voice':'test','role_bindings':{'test-role':'test'}})

    def test_speed_and_persistence(self):
        for value in [float('nan'),float('inf'),0.59,1.51]:
            with self.assertRaises(ValueError):speed_value(value)
        self.store.update_settings({'speed':.8})
        self.assertEqual(Store(self.tmp.name).snapshot()['settings']['speed'],.8)

    def test_accelerator_is_closed_when_voice_unloads(self):
        accelerator=Mock()
        backend=GPTSoVITSBackend()
        backend.tts=SimpleNamespace(t2s_accel=accelerator)
        backend.loaded_key=('fixture',)
        backend.unload()
        accelerator.close.assert_called_once_with()
        self.assertIsNone(backend.tts)
        self.assertIsNone(backend.loaded_key)

    def test_role_never_falls_back(self):
        with self.assertRaises(ValueError):self.store.voice(role='unknown')
        with self.assertRaises(ValueError):self.store.voice(voice_id='other',role='test-role')

    def test_accepted_profile_is_locked(self):
        with self.assertRaises(ValueError):self.store.choose_reference('test',0)

    def test_settings_export_has_no_private_files(self):
        export=self.store.export_preset()
        self.assertEqual(set(export),{'format','schema_version','settings'})
        self.assertNotIn(self.store.token,json.dumps(export))
        self.assertNotIn(self.tmp.name,json.dumps(export))
        export['settings']['default_voice']='missing'
        with self.assertRaises(ValueError):self.store.import_preset(export)

    def zip_bytes(self,files):
        result=io.BytesIO()
        with zipfile.ZipFile(result,'w') as z:
            for name,content in files.items():z.writestr(zipfile.ZipInfo(name),content)
        result.seek(0)
        return result

    def test_reject_zip_traversal_and_untrusted(self):
        for name in ['../escape.txt','C:/escape.txt','nested/../../escape.txt']:
            with self.assertRaises(ValueError):self.store.import_zip(self.zip_bytes({name:'bad'}),'bad',True)
        with self.assertRaises(ValueError):self.store.import_zip(self.zip_bytes({'readme.txt':'ok'}),'bad',False)
        self.assertFalse((Path(self.tmp.name)/'escape.txt').exists())
        self.assertEqual(list((Path(self.tmp.name)/'voices').iterdir()),[])

    def test_import_metadata_format(self):
        # Fixture weights are never loaded; real inference is checked separately by integration_mcp.py.
        audio=io.BytesIO()
        with wave.open(audio,'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*64000)
        files={'voice/config.json':json.dumps({'version':'v5turbo'}),
               'voice/models/gpt.ckpt':b'fixture','voice/models/sovits.pth':b'08fixture',
               'voice/dataset/a.wav':audio.getvalue(),'voice/dataset/text.list':'a.wav|speaker|ZH|测试录音'}
        result=self.store.import_zip(self.zip_bytes(files),'Imported fixture',True)
        self.assertEqual(result['reference_count'],1)
        self.assertEqual(self.store.voice(result['id'])['profile'],'original')
        duplicate=self.store.import_zip(self.zip_bytes(files),'Ignored duplicate name',True)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['id'],result['id'])
        self.assertEqual(len(list((Path(self.tmp.name)/'voices').iterdir())),1)
        self.store.rename_voice(result['id'],'测试角色')
        self.assertEqual(Store(self.tmp.name).voice(result['id'])['name'],'测试角色')
        files['voice/config.json']=json.dumps({'version':'v2'})
        with self.assertRaises(ValueError):self.store.import_zip(self.zip_bytes(files),'wrong version',True)

        # Auto-detection is based on the weight header, not a declared version or name.
        files['voice/config.json']='{}'
        files['voice/models/sovits.pth']=b'03fixture'
        with self.assertRaisesRegex(ValueError,'仅启用 V5Turbo'):
            self.store.import_zip(self.zip_bytes(files),'unsupported fixture',True)
        files['voice/config.json']=json.dumps({'version':'v5turbo'})
        with self.assertRaisesRegex(ValueError,'不符'):
            self.store.import_zip(self.zip_bytes(files),'mislabelled',True)
        self.assertEqual(len(list((Path(self.tmp.name)/'voices').iterdir())),1)

    def test_version_runtime_isolation(self):
        base=Path(self.tmp.name)/'base';base.mkdir()
        weight=base/'voice.pth';weight.write_bytes(b'08fixture')
        voice={'version':'v5turbo','profile':'original','sovits':str(weight),'gpt':str(weight)}
        with self.assertRaisesRegex(ValueError,'基础文件'):check_runtime(voice,base)
        for name in ('gsv-v5-pretrained/s2Gv5turbo.pth','gsv-v5-pretrained/vocoder.pth'):
            p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
        self.assertEqual(check_runtime(voice,base)['sample_steps'],4)
        for header in (b'03',b'04',b'05'):
            weight.write_bytes(header+b'fixture')
            with self.assertRaisesRegex(ValueError,'仅启用 V5Turbo'):validate_version(weight)
        weight.write_bytes(b'not a model')
        with self.assertRaisesRegex(ValueError,'无法可靠识别'):validate_version(weight)

    def test_broken_zip_reports_error_and_cleans_upload(self):
        path=Path(self.tmp.name)/'broken.zip';path.write_bytes(b'broken download')
        installer=Installer(self.store);job=installer.start(path)
        deadline=time.monotonic()+5
        while installer.active and time.monotonic()<deadline:time.sleep(.02)
        result=installer.get(job['id'])
        self.assertEqual(result['status'],'error')
        self.assertIn('没有下载完整',result['message'])
        self.assertFalse(path.exists())
        self.assertEqual(list((Path(self.tmp.name)/'voices').iterdir()),[])

    def test_missing_runtime_is_not_reported_as_ready(self):
        path=Path(self.tmp.name)/'pending.zip';path.write_bytes(b'fixture')
        installer=Installer(self.store)
        voice=self.store.data['voices']['test'];voice['version']='v5turbo'
        self.store.import_zip=Mock(return_value={'id':'test','name':'test','reference_count':1})
        from unittest.mock import patch
        with patch('linguavoice.installer.ROOT',Path(self.tmp.name)):
            job=installer.start(path)
            deadline=time.monotonic()+5
            while installer.active and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(installer.get(job['id'])['status'],'needs_setup')
        self.assertFalse(path.exists())

    def test_hidden_text_cancellation_and_disabled(self):
        backend=BlockingBackend();service=VoiceService(self.store,backend)
        job=service.submit('Do not expose transcript',hide_text=True)
        self.assertNotIn('text',job)
        self.assertNotIn('text',service.snapshot()['jobs'][0])
        self.assertEqual(service.job(job['id'],reveal=True)['text'],'Do not expose transcript')
        service.control('stop');backend.release.set();service.queue.join()
        self.assertEqual(service.job(job['id'])['status'],'cancelled')
        self.store.update_settings({'speech_enabled':False})
        with self.assertRaises(ValueError):service.submit('disabled')

    def test_http_auth_origin_and_host(self):
        service=VoiceService(self.store,BlockingBackend())
        server=ConsoleHTTPServer(('127.0.0.1',0),self.store,service)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        for headers,status in [({},401),({'Authorization':'Bearer '+self.store.token,'Origin':'https://example.com'},403),({'Host':'evil.example'},403)]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(base+'/api/state',headers=headers))
            self.assertEqual(caught.exception.code,status)
        req=urllib.request.Request(base+'/api/state',headers={'Authorization':'Bearer '+self.store.token})
        with urllib.request.urlopen(req) as r:self.assertEqual(r.status,200)


if __name__=='__main__':unittest.main()
