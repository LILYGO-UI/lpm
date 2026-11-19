"""K230 pack prerequisites are ready before the application's CMake configure."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from lpm import k230_sdk as sdk
from lpm.errors import LpmError


class SDKTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='lpm K230 ')
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name).resolve()
        self.build = self.project / 'build/k230-debian'
        self.env = self.enterContext(patch.dict(os.environ, LILYGO_UI_DEBIAN_SYSROOT=''))
        self.host = self.enterContext(patch.object(sdk, '_prepare_host'))
        self.root = self.project / '.cache/k230-bsp/20261116T104343Z/sysroot'
        archive = self.project / 'asset.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name in ('usr/include/stdio.h', 'usr/lib/riscv64-linux-gnu/crt1.o',
                         'usr/lib/riscv64-linux-gnu/pkgconfig/libdrm.pc', 'var/lib/dpkg/status',
                         'usr/share/canaan-sdk/manifest.json'):
                item = tarfile.TarInfo('sysroot/' + name)
                item.size = 1
                tar.addfile(item, io.BytesIO(b'x'))
        self.digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.archive = archive
        actual_run = sdk.run
        def local_release(command, **kwargs):
            if command[0] == 'cmake' and '-P' in command:
                command = [command[0], f'-DK230_BSP_URL={archive.as_uri()}',
                           f'-DK230_BSP_SHA256={self.digest}', *command[1:]]
            return actual_run(command, **kwargs)
        self.enterContext(patch.object(sdk, 'run', side_effect=local_release))

    def prepare(self):
        return sdk.prepare_pack(self.project, self.build)

    def test_app_helper_not_required_and_old_external_sysroot_migrates(self):
        self.build.mkdir(parents=True)
        (self.build / 'CMakeCache.txt').write_text('LILYGO_UI_DEBIAN_SYSROOT:PATH=/some-other-app/sysroot\nLilyGoUI_DIR:PATH=/selected/appkit\n')
        options = self.prepare()
        self.assertEqual(options, ('--fresh', '-DLilyGoUI_DIR:PATH=/selected/appkit',
                                   f'-DLILYGO_UI_DEBIAN_SYSROOT:PATH={self.root}'))
        self.assertTrue((self.root / 'usr/include/stdio.h').is_file())
        self.host.assert_called_once_with(self.project, '')
        self.archive.unlink()
        shutil.rmtree(self.root)
        self.prepare()
        self.assertTrue((self.root / 'var/lib/dpkg/status').is_file())

    def test_invalid_download_stops_even_if_project_has_helper(self):
        helper = self.project / 'cmake/k230-bsp.cmake'
        helper.parent.mkdir(); helper.touch()
        self.archive.write_bytes(b'bad')
        with self.assertRaises(LpmError):
            self.prepare()
        self.assertFalse(self.root.exists())
        self.assertFalse(list(self.root.parent.glob('*.part')))

    def test_explicit_incomplete_sysroot_aborts_before_configure(self):
        os.environ['LILYGO_UI_DEBIAN_SYSROOT'] = str(self.project / 'missing')
        with self.assertRaisesRegex(LpmError, 'incomplete K230 sysroot'):
            self.prepare()
        self.assertFalse(self.root.exists())

    def test_git_only_initializes_missing_dependencies(self):
        (self.project / '.git').mkdir(); (self.project / '.gitmodules').touch()
        existing = self.project / 'existing'; existing.mkdir(); (existing / 'local.c').touch()
        with patch.object(sdk, 'run') as run:
            run.return_value.stdout = '160000 abc 0\texisting\0' + '160000 abc 0\tmissing sdk\0'
            sdk._prepare_sources(self.project)
            self.assertEqual(run.call_args.args[0], ['git', 'submodule', 'update', '--init', '--recursive', '--', 'missing sdk'])
            self.assertEqual(run.call_count, 2)


class HostToolsTests(unittest.TestCase):
    def test_missing_linux_compiler_is_installed_once_and_rechecked(self):
        installed = False

        def which(name, **kwargs):
            if name == 'riscv64-linux-gnu-g++' and not installed:
                return None
            return '/bin/' + name

        def install(command, **kwargs):
            nonlocal installed
            self.assertEqual(command, ['sudo', 'apt-get', 'install', '-y', '--no-install-recommends', 'g++-riscv64-linux-gnu'])
            installed = True

        with patch.object(sdk.sys, 'platform', 'linux'), patch.object(sdk.shutil, 'which', side_effect=which), \
                patch.object(sdk.os, 'geteuid', return_value=1000), patch.object(sdk, 'run', side_effect=install) as run:
            sdk._prepare_host(Path.cwd(), '')
            sdk._prepare_host(Path.cwd(), '')
            run.assert_called_once()
