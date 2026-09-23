"""Prepare 11.5.3 synchronous subdraft loading before each UI launch.

Only a performance flag is changed. Remote settings may re-enable it next launch.
No account, entitlement, network, application binary, or existing draft is changed.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import headless_runtime as runtime

LIB_SHA = 'd0e0021a1d3806f818442c04284a707aa908c61aa69a15b995e0be4c04e321d7'
STORE = Path.home() / 'Movies/JianyingPro/User Data/MMKV'
FILES = ('settings_json', 'settings_json.crc')
FLAG = 'draft_load_optimize_part1.sub_draft_async_load'


def checked_runtime():
    info = runtime.validate_runtime()
    if info['runtime_profile'] != 'jy14-headless-macos-11.5.3':
        raise ValueError('UI compatibility preparation requires 11.5.3')
    lib = runtime.APP / 'Contents/Frameworks/libmmkv.dylib'
    if lib.is_symlink() or runtime.digest(lib) != LIB_SHA:
        raise ValueError('Unverified native settings library')
    runtime.helper()._ensure_editor_closed(True)
    for name in FILES:
        path = STORE / name
        if not path.is_file() or path.is_symlink():
            raise ValueError('Native settings database unavailable or symlinked')
    return info


def compile_helper(out):
    exe = out / 'settings-helper'
    frameworks = runtime.APP / 'Contents/Frameworks'
    subprocess.run(['/usr/bin/xcrun', 'clang++', '-std=c++17', '-arch', 'arm64',
                    str(Path(__file__).with_name('native_ui_settings.cpp')), '-L' + str(frameworks),
                    '-lmmkv', '-Wl,-rpath,' + str(frameworks), '-o', str(exe)],
                   check=True, capture_output=True, timeout=120)
    return exe


def invoke(exe, root, mode):
    result = subprocess.run([str(exe), str(root), mode], capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError('Native settings operation failed: ' + result.stderr.decode(errors='replace'))
    data = json.loads(result.stdout)
    if type(data.get('async_enabled')) is not bool or type(data.get('previous_async')) is not bool:
        raise ValueError('Invalid settings adapter response')
    return data


def copy_store(out):
    out.mkdir(mode=0o700)
    for name in FILES:
        shutil.copy2(STORE / name, out / name)
        os.chmod(out / name, 0o600)


def state():
    checked_runtime()
    # MMKV may update bookkeeping even for reads; query a private snapshot.
    with tempfile.TemporaryDirectory(prefix='jy-settings-') as tmp:
        folder = Path(tmp)
        copy_store(folder / 'snapshot')
        return invoke(compile_helper(folder), folder / 'snapshot', 'read')


def prepare(out, restore_from=None):
    info = checked_runtime()
    out = Path(out)
    if not out.is_absolute() or 'work' not in out.parts or out.exists() or out.is_symlink():
        raise ValueError('Compatibility audit must be a new absolute work directory')
    out = runtime.fresh_directory(out)
    copy_store(out / 'backup')
    exe = compile_helper(out)
    before = invoke(exe, out / 'backup', 'read')
    mode = 'disable'
    if restore_from is not None:
        receipt = json.loads(Path(restore_from).read_text())
        if receipt.get('flag') != FLAG or type(receipt.get('previous_async')) is not bool:
            raise ValueError('Invalid compatibility receipt')
        mode = 'enable' if receipt['previous_async'] else 'disable'
    result = invoke(exe, STORE, mode)
    if result['previous_async'] != before['async_enabled']:
        raise ValueError('Settings changed during compatibility preparation')
    receipt = dict(result, flag=FLAG, runtime_profile=info['runtime_profile'],
                   library_sha256=LIB_SHA, status='prepared' if mode=='disable' else 'restored',
                   required_before_each_cold_launch=True, backup=str(out / 'backup'))
    path = out / 'result.json'
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', help='New private audit directory under work')
    p.add_argument('--check', action='store_true')
    p.add_argument('--restore-from', help='Prior result.json; restore only its original performance flag')
    a = p.parse_args()
    if a.check:
        if a.out or a.restore_from:
            p.error('--check cannot be combined with writes')
        result = state()
    else:
        if not a.out:
            p.error('--out is required')
        result = prepare(a.out, a.restore_from)
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
