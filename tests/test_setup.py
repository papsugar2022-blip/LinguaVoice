import importlib.util
import io
import tempfile
import unittest
import zipfile
import json
import threading
import types
from contextlib import ExitStack
from unittest.mock import patch, MagicMock
from pathlib import Path

spec=importlib.util.spec_from_file_location('lv_setup',Path(__file__).resolve().parents[1]/'packaging/setup_app.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def test_only_selected_engines_are_downloaded_and_verified(self):
        for gpt,qwen in ((True,False),(False,True),(True,True)):
            with self.subTest(gpt=gpt,qwen=qwen), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                for name in ('linguavoice/storage.py','.venv/Scripts/python.exe','.engines/qwen3-tts/Scripts/python.exe','private/tunnel-client/verified/tunnel-client.exe'):
                    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
                saved={'other-existing-provider':{'python':'keep'}}
                (root/'private/runtimes.json').write_text(json.dumps(saved))
                settings=root/'private/console/settings.json';settings.parent.mkdir()
                settings.write_text('{"existing_voice":"keep"}')
                def process(args,**kwargs):
                    return types.SimpleNamespace(returncode=1 if 'faster_qwen3_tts' in str(args) else 0,stdout='GPU')
                with ExitStack() as stack:
                    stack.enter_context(patch.object(setup.shutil,'disk_usage',return_value=types.SimpleNamespace(free=100*1024**3)))
                    stack.enter_context(patch.object(setup.subprocess,'run',side_effect=process))
                    stack.enter_context(patch.object(setup,'safe_extract'))
                    downloads=stack.enter_context(patch.object(setup,'download',side_effect=lambda item,path,*a:path))
                    commands=stack.enter_context(patch.object(setup,'command'))
                    gpt_install=stack.enter_context(patch.object(setup,'install_gpt'))
                    stack.enter_context(patch.dict('sys.modules',{'winreg':MagicMock()}))
                    setup.install(root,lambda _:None,threading.Event(),gpt,qwen)
                calls=[' '.join(map(str,c.args[0])) for c in commands.call_args_list]
                self.assertEqual(any('requirements-qwen.txt' in c for c in calls),qwen)
                self.assertEqual(any('requirements-runtime.txt' in c for c in calls),gpt)
                self.assertEqual(any('--provider qwen3-tts' in c for c in calls),qwen)
                self.assertEqual(any('--provider gpt-sovits' in c for c in calls),gpt)
                self.assertEqual(bool(downloads.call_count),qwen)
                self.assertEqual(gpt_install.call_count,int(gpt))
                installed=json.loads((root/'private/runtimes.json').read_text())
                self.assertEqual(installed['other-existing-provider'],saved['other-existing-provider'])
                self.assertEqual(settings.read_text(),'{"existing_voice":"keep"}')
                self.assertEqual(json.loads((root/'private/install-selection.json').read_text()),{'gpt':gpt,'qwen':qwen})

    def test_no_component_selection_is_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'unused'
            with self.assertRaisesRegex(ValueError,'至少勾选'):
                setup.install(root,lambda _:None,threading.Event(),False,False)
            self.assertFalse(root.exists())

    def test_v5_only_does_not_register_bundled_voice_or_remove_existing(self):
        import linguavoice.storage as storage
        with tempfile.TemporaryDirectory() as directory, patch.object(storage,'ROOT',Path(directory)):
            root=Path(directory);(root/'private').mkdir()
            (root/'private/install-selection.json').write_text('{"qwen":false,"gpt":true}')
            with patch.object(storage.Store,'default_voice') as bundled:
                store=storage.Store()
                bundled.assert_not_called()
                self.assertEqual(store.data['voices'],{})
                store.data['voices']['existing']={'id':'existing','name':'keep'}
                storage.write_json(store.path,store.data)
                self.assertIn('existing',storage.Store().data['voices'])

    def test_archive_escape_is_rejected(self):
        for name in ('../outside','/absolute','C:/outside','root/../../outside'):
            archive=io.BytesIO()
            with zipfile.ZipFile(archive,'w') as z:z.writestr(name,'bad')
            archive.seek(0)
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):setup.safe_extract(archive,Path(directory)/'target')

    def test_exact_patch_rejects_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);engine=root/'engine';engine.mkdir()
            (engine/'a.py').write_text('one\ntwo\nthree\n')
            patch=root/'changes.patch'
            patch.write_text('diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n one\n-two\n+changed\n three\n')
            setup.apply_patch(engine,patch)
            self.assertEqual((engine/'a.py').read_text(),'one\nchanged\nthree\n')
            with self.assertRaises(ValueError):setup.apply_patch(engine,patch)

    def test_existing_other_project_is_untouched(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'keep.txt').write_text('keep')
            with self.assertRaisesRegex(ValueError,'已有其他文件'):
                setup.install(root,lambda x:None,threading.Event())
            self.assertEqual(list(root.iterdir()),[root/'keep.txt'])

if __name__=='__main__':unittest.main()
