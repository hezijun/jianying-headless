"""Explicit runtime profiles for the headless builder, independent of legacy apply.

Only the version-neutral, hash-pinned IO/codec primitives are included. The
legacy registered-sound CLI and account-specific constants are not distributed.
Live creation is guarded by this module's own exact application profile.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

APP = Path('/Applications/VideoFusion-macOS.app')
DRAFT_ROOT = Path.home() / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / 'bridge'
# Historical blueprint/codec provenance, not a statement of the current app version.
MANIFEST_SHA = '2fea820b26d503940526c345ce8e9bd87c25f0c1c8b1c4a02aa8edba317e33dd'
IO_MANIFEST_SHA = '473b05370d77f0835e11f0fde4f3cec2eec0b00777aa0018441bc9e7b53bb300'
PINS = {
    'runtime_io.py': '487cf8c65bddad2ecdb351b16996d923bd6bcc6861dba12e801f679821b4ef70',
}
PROFILES = {
    '11.4.0': 'a1693070036a6678bb5db35f71d2105812ad24a2370e7e91c78712cc0d6455f3',
    '11.4.2': '632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1',
    '11.5.3': '9ac52035d017977eb27cf51b9468847e28cce951153502659881636598f81101',
}
CODECS = {
    '11.4.0': ('jy14_codec_hardened_11_4', 'b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d'),
    '11.4.2': ('jy14_codec_hardened_11_4', 'b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d'),
    '11.5.3': ('jy_codec_hardened_11_5_3', '0c84d54ef5e1abbd72243c96c6a441da1e1a50441b7e539da3fb63c24f53b734'),
}
BUNDLE_ID = 'com.lemon.lvpro'
TEAM = 'X2JNK7LY8J'


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def validate_timeline_version(timeline, runtime_profile):
    """Accept only captured schemas, including native 11.5.3 save migration."""
    allowed = {
        'jy14-headless-macos-11.4.0': {'185.0.0'},
        'jy14-headless-macos-11.4.2': {'185.0.0'},
        # Builds use the proven 185 blueprint; 11.5.3 saves it as 187.
        'jy14-headless-macos-11.5.3': {'185.0.0', '187.0.0'},
    }
    if (runtime_profile not in allowed or timeline.get('version') != 360000
            or timeline.get('new_version') not in allowed[runtime_profile]):
        raise ValueError('Unexpected native timeline version for runtime profile')


def fresh_directory(path):
    path = Path(path).resolve()
    if path == DRAFT_ROOT or DRAFT_ROOT in path.parents:
        raise ValueError('Build/audit directories must be outside the live draft tree')
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    return path


def doctor():
    if digest(BACKEND / 'SOURCE_MANIFEST.json') != IO_MANIFEST_SHA:
        raise ValueError('Packaged IO/codec source manifest changed')
    for name, expected in PINS.items():
        path = BACKEND / name
        if not path.is_file() or path.is_symlink():
            raise ValueError('IO/codec component unavailable: ' + name + '; build with tools/build_native_codec.py')
        if digest(path) != expected:
            raise ValueError('IO/codec component changed: ' + name)
    info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
    version = info.get('CFBundleShortVersionString')
    if version not in PROFILES or info.get('CFBundleVersion') != version or info.get('CFBundleIdentifier') != BUNDLE_ID:
        raise ValueError('Unsupported Jianying version/build/identity; stop native writes')
    library = APP / 'Contents/Frameworks/libvideoeditor.dylib'
    fingerprint = digest(library)
    if fingerprint != PROFILES[version]:
        raise ValueError('Editor library differs from its exact headless runtime profile')
    codec_name, codec_sha = CODECS[version]
    codec = BACKEND / codec_name
    if not codec.is_file() or codec.is_symlink() or not os.access(codec, os.X_OK):
        raise ValueError('Codec unavailable; build with tools/build_native_codec.py')
    if digest(codec) != codec_sha:
        raise ValueError('Codec differs from its exact runtime profile')
    if not all(shutil.which(name) for name in ('ffmpeg', 'ffprobe')):
        raise ValueError('ffmpeg and ffprobe are required')
    return {'status': 'ok', 'app_version': version, 'app_build': version,
            'bundle_id': BUNDLE_ID, 'libvideoeditor_sha256': fingerprint,
            'runtime_profile': 'jy14-headless-macos-' + version,
            'codec_name': codec_name, 'codec_sha256': codec_sha,
            'capabilities': {'draft_build': True, 'native_export': version in {'11.4.2', '11.5.3'},
                             'captured_effects': version == '11.4.2'},
            'native_export_features': (['basic', 'linear-keyframes', 'six-static-masks', 'dissolve',
                                        'light-shake', 'yellow-retro-text', 'offline-compounds']
                                       if version == '11.5.3' else []),
            'runtime_hashes_verified': True, 'network_called': False,
            'full_signature_check': 'required before live creation'}


def validate_runtime():
    before = doctor()
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'}
    result = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(APP)],
                            capture_output=True, timeout=180, env=env)
    details = subprocess.run(['/usr/bin/codesign', '-dv', '--verbose=4', str(APP)],
                             capture_output=True, text=True, timeout=30, env=env)
    if result.returncode or details.returncode or ('TeamIdentifier=' + TEAM) not in details.stderr.splitlines():
        raise ValueError('Native application signature or signing identity verification failed')
    if doctor() != before:
        raise ValueError('Native runtime changed while checking its signature')
    return dict(before, team_identifier=TEAM, full_signature_check='passed')


def helper():
    runtime = doctor()
    name = '_jy14_headless_pinned_io_' + hashlib.sha256((str(BACKEND) + runtime['app_version']).encode()).hexdigest()[:16]
    h = sys.modules.get(name)
    if h is None:
        spec = importlib.util.spec_from_file_location(name, BACKEND / 'runtime_io.py')
        h = importlib.util.module_from_spec(spec)
        sys.modules[name] = h
        spec.loader.exec_module(h)
        h.CODEC_PATH = BACKEND / runtime['codec_name']
        h.REQUIRED_CODEC_SHA256 = runtime['codec_sha256']
    names = ('_decrypt_metadata_in_memory', '_encrypt_metadata_from_memory',
             '_ensure_editor_closed', '_snapshot_file', '_parse_strict_json',
             '_revalidate_snapshot', '_acquire_directory_transaction_lock',
             '_release_directory_transaction_lock')
    return SimpleNamespace(**{n: getattr(h, n) for n in names}, _validate_runtime_environment=validate_runtime)


def validate_compiled(value):
    # This is the speech-plan validator, not the legacy native runtime wrapper.
    scripts = PROJECT_ROOT / 'skills/yichen-jianying-edit/scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from edit_plan import validate_compiled as validate
    return validate(value)
