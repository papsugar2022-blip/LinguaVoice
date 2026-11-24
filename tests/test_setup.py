import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

spec=importlib.util.spec_from_file_location('lv_setup',Path(__file__).resolve().parents[1]/'packaging/setup_app.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
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
