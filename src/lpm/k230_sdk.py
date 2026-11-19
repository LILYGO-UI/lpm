# SPDX-License-Identifier: MIT
"""Prepare the K230 Debian build environment before configuring the application."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile

from .errors import LpmError
from .process import run


def _prepare_host(project: Path, prefix: str) -> None:
    mac = sys.platform == 'darwin'
    if not mac and not sys.platform.startswith('linux'):
        raise LpmError('K230 packaging requires a Linux or macOS build host')
    tools = {'cmake': 'cmake', 'cpack': 'cmake', 'make': 'make',
             'pkg-config': 'pkgconf', 'dpkg-deb': 'dpkg', 'dpkg-query': 'dpkg',
             'dpkg-shlibdeps': 'dpkg' if mac else 'dpkg-dev'}
    hints: list[str] = []
    if mac and not prefix:
        for base in ('/opt/homebrew/opt', '/usr/local/opt'):
            hints.extend([base + '/llvm/bin', base + '/lld/bin'])
        tools.update({tool: 'llvm' for tool in ('llvm-config', 'clang', 'clang++', 'llvm-ar',
                                                'llvm-ranlib', 'llvm-strip', 'llvm-objdump')})
        tools['ld.lld'] = 'lld'
    else:
        cross = prefix or 'riscv64-linux-gnu-'
        tools[cross + 'gcc'] = 'gcc-riscv64-linux-gnu'
        tools[cross + 'g++'] = 'g++-riscv64-linux-gnu'
        for tool in ('ar', 'ranlib', 'strip', 'objdump'):
            tools[cross + tool] = 'binutils-riscv64-linux-gnu'

    def missing() -> list[str]:
        path = os.pathsep.join([os.environ.get('PATH', ''), *hints])
        return [tool for tool in tools if not shutil.which(tool, path=path)]

    absent = missing()
    if not absent:
        return
    packages = sorted({tools[name] for name in absent})
    if prefix and (mac or prefix != 'riscv64-linux-gnu-') and any(name.startswith(prefix) for name in absent):
        raise LpmError('incomplete custom cross toolchain: ' + ', '.join(absent))
    if mac and shutil.which('brew'):
        install = ['brew', 'install']
    elif not mac and shutil.which('apt-get'):
        install = ['apt-get', 'install', '-y', '--no-install-recommends']
        if os.geteuid() != 0:
            if not shutil.which('sudo'):
                raise LpmError('install these build packages as root: ' + ' '.join(packages))
            install.insert(0, 'sudo')
    else:
        raise LpmError('install the missing K230 build tools: ' + ', '.join(absent))
    print('[lpm] Preparing host tools: ' + ' '.join(packages), flush=True)
    run([*install, *packages], cwd=project)
    if absent := missing():
        raise LpmError('build tools still unavailable: ' + ', '.join(absent))


def _prepare_sources(project: Path) -> None:
    if not (project / '.gitmodules').is_file() or not (project / '.git').exists():
        return
    # Only initialize absent submodules. Keep all local revisions and edits.
    index = run(['git', 'ls-files', '--stage', '-z'], cwd=project, capture=True).stdout
    missing = []
    for entry in index.split('\0'):
        if entry.startswith('160000 '):
            name = entry.partition('\t')[2]
            directory = project / name
            if not directory.exists() or (directory.is_dir() and not any(directory.iterdir())):
                missing.append(name)
    if missing:
        run(['git', 'submodule', 'update', '--init', '--recursive', '--', *missing], cwd=project)


def _cached_settings(build_dir: Path) -> dict[str, str]:
    cache = build_dir / 'CMakeCache.txt'
    if not cache.exists():
        return {}
    return dict(line.split('=', 1) for line in cache.read_text().splitlines()
                if '=' in line and not line.startswith(('#', '//')))


def _prepare_sysroot(project: Path) -> Path:
    # Invoke the recipe shipped with LPM in script mode. Even a project without a
    # download helper gets a fully provisioned sysroot before its first configure.
    with tempfile.TemporaryDirectory() as scratch:
        result = Path(scratch) / 'sysroot.txt'
        run(['cmake', f'-DLPM_SYSROOT_RESULT={result}', '-P',
             Path(__file__).with_name('k230-bsp.cmake')], cwd=project)
        root = Path(result.read_text().strip())
    required = ('usr/include/stdio.h', 'usr/lib/riscv64-linux-gnu/crt1.o',
                'usr/lib/riscv64-linux-gnu/pkgconfig/libdrm.pc', 'var/lib/dpkg/status')
    for entry in required:
        if not (root / entry).is_file():
            raise LpmError(f'incomplete K230 sysroot: missing {root / entry}')
    return root.resolve()


def prepare_pack(project: Path, build_dir: Path) -> tuple[str, ...]:
    settings = _cached_settings(build_dir)
    prefix = os.environ.get('LILYGO_UI_DEBIAN_CROSS_PREFIX') or settings.get('LILYGO_UI_DEBIAN_CROSS_PREFIX:STRING', '')
    if settings.get('LILYGO_UI_K230_USE_CLANG:INTERNAL') == 'ON' and not os.environ.get('LILYGO_UI_DEBIAN_CROSS_PREFIX'):
        prefix = ''
    _prepare_host(project, prefix)
    _prepare_sources(project)
    root = _prepare_sysroot(project)
    previous = settings.get('LILYGO_UI_DEBIAN_SYSROOT:PATH')
    args: list[str] = []
    if previous and Path(previous).resolve() != root:
        # Imported targets and compiler flags can still refer to the previous SDK.
        args.append('--fresh')
        appkit = os.environ.get('LILYGO_UI_APPKIT_DIR') or settings.get('LilyGoUI_DIR:PATH')
        if appkit:
            args.append(f'-DLilyGoUI_DIR:PATH={appkit}')
        if prefix:
            args.append(f'-DLILYGO_UI_DEBIAN_CROSS_PREFIX:STRING={prefix}')
    args.append(f'-DLILYGO_UI_DEBIAN_SYSROOT:PATH={root}')
    print(f'[lpm] K230 Debian sysroot ready: {root}', flush=True)
    return tuple(args)
