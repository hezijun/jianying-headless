#!/usr/bin/env python3
"""Read-only source/pin checks; not a complete secret or legal audit."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIRS = {'.git', 'work', '__pycache__', '.pytest_cache', '.venv'}
LOCAL_CODEC = 'bridge/jy14_codec_hardened_11_4'
LOCAL_CODECS = {LOCAL_CODEC, 'bridge/jy_codec_hardened_11_5_3'}
SUFFIXES = {'.py', '.cpp', '.h', '.json', '.md', '.yaml', '.txt'}
SPECIAL = {'.gitignore', 'NOTICE', 'LICENSE'}
PATTERNS = {
    'personal-home-path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    'signed-url': re.compile(r'https?://[^\s<>"\x27]+[?&](?:Signature|X-Amz-Signature|auth_key)=', re.I),
}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def literal(path, name):
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing literal: ' + name)


def source_files():
    files = []
    for folder, directories, names in os.walk(ROOT, followlinks=False):
        for name in directories:
            require(not (Path(folder) / name).is_symlink(), 'Symlinked directory: ' + name)
        directories[:] = [name for name in directories if name not in LOCAL_DIRS]
        for name in names:
            path = Path(folder) / name
            relative = path.relative_to(ROOT).as_posix()
            if relative in LOCAL_CODECS or relative == '.git':
                continue
            require(path.is_file() and not path.is_symlink(), 'Nonregular source: ' + relative)
            require(path.suffix in SUFFIXES or name in SPECIAL, 'Unexpected source type: ' + relative)
            files.append(path)
    return sorted(files)


def main():
    files = source_files()
    inventory = {}
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        raw = path.read_bytes()
        require(b'\x00' not in raw, 'Binary content in source: ' + relative)
        content = raw.decode('utf-8-sig')
        for label, pattern in PATTERNS.items():
            require(not pattern.search(content), label + ' in ' + relative)
        if path.suffix == '.py':
            ast.parse(content, filename=relative)
        elif path.suffix == '.json':
            json.loads(content)
        elif path.suffix == '.md':
            for target in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', content):
                if '://' in target or target.startswith('#'):
                    continue
                target = target.split('#', 1)[0]
                require((path.parent / target).is_file(), 'Broken relative link in ' + relative + ': ' + target)
        inventory[relative] = hashlib.sha256(raw).hexdigest()

    wrapper = ROOT / 'skills/yichen-jianying-edit/scripts/headless_draft.py'
    for name, expected in literal(wrapper, 'PINS').items():
        require(digest(ROOT / 'engine' / name) == expected, 'Skill pin differs: ' + name)
    runtime = ROOT / 'engine/headless_runtime.py'
    manifest_path = ROOT / 'bridge/SOURCE_MANIFEST.json'
    require(digest(manifest_path) == literal(runtime, 'IO_MANIFEST_SHA'), 'IO manifest pin differs')
    manifest = json.loads(manifest_path.read_bytes())
    for name, expected in manifest['source_files'].items():
        require(Path(name).name == name and digest(ROOT / 'bridge' / name) == expected, 'Bridge source pin differs: ' + name)
    for name, expected in literal(runtime, 'PINS').items():
        if name == Path(LOCAL_CODEC).name and not (ROOT / LOCAL_CODEC).exists():
            continue
        require(digest(ROOT / 'bridge' / name) == expected, 'Runtime IO/codec pin differs: ' + name)
    for name, expected in set(literal(runtime, 'CODECS').values()):
        codec = ROOT / 'bridge' / name
        if codec.exists():
            require(not codec.is_symlink() and digest(codec) == expected, 'Profile codec differs: ' + name)
    require(digest(ROOT / 'engine/native-resource-catalog.json') ==
            literal(ROOT / 'engine/native_resources.py', 'CATALOG_SHA'), 'Resource catalog pin differs')

    if (ROOT / '.git').exists():
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True).stdout
        names = {name.decode() for name in tracked.split(b'\x00') if name}
        require(names <= set(inventory), 'Git includes a file outside the checked source inventory')
    fingerprint = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    print(json.dumps({'status': 'source-checks-passed', 'files': len(files), 'inventory_sha256': fingerprint,
                      'official_binaries_in_source_inventory': False, 'network_called': False,
                      'scope': 'syntax, known privacy patterns, relative links, source pins and tracked-file boundary'}))


if __name__ == '__main__':
    main()
