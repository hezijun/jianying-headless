"""Pinned, locally acquired native effect resources; never fetch or license them.

The catalog describes resources selected in the real application during testing.
Copying them into a private local draft does not grant redistribution rights.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import headless_runtime as rt

HERE = Path(__file__).resolve().parent
CATALOG_SHA = '68021d765aa212436891056d06f205ef96365e1b687a3fdc28691f00a50105c5'
SHAPES = ('circle', 'rectangle', 'line', 'mirror', 'star', 'heart')
PREVIEW_ONLY_1153 = {'filter/hd-monochrome'}
CATALOG_1153_SHA = 'dbdfe8b2d3a3a1e2f661bf397b2f80cc9e45325bdbb3da6618c03755a9ce1cca'
VERIFIED_1153 = {'mask/' + shape for shape in SHAPES} | {'transition/dissolve', 'effect/light-shake', 'text-effect/yellow-retro'}


def require(value, message):
    if not value:
        raise ValueError(message)


def tree_manifest(folder):
    folder = Path(folder)
    require(folder.is_dir() and not folder.is_symlink(), 'Native resource directory missing or symlinked')
    result = {}
    for path in sorted(folder.rglob('*')):
        info = path.lstat()
        require(not path.is_symlink(), 'Native resource symlinks are not allowed')
        if stat.S_ISDIR(info.st_mode):
            continue
        require(stat.S_ISREG(info.st_mode), 'Native resource contains a nonregular file')
        result[path.relative_to(folder).as_posix()] = {'sha256': rt.digest(path), 'size': info.st_size}
    require(result, 'Native resource is empty')
    return result


def manifest_hash(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_cached_manifest(entry, actual, message):
    """Only exact captured compiler outputs may supplement unchanged sources."""
    original = entry['files']
    require(all(actual.get(name) == info for name, info in original.items()), message)
    extra = {name: info for name, info in actual.items() if name not in original}
    allowed = entry.get('native_generated_cache_files', {})
    require(all(allowed.get(name) == info for name, info in extra.items()), message)
    return sorted(extra)


def catalog():
    path = HERE / 'native-resource-catalog.json'
    require(rt.digest(path) == CATALOG_SHA, 'Native resource catalog changed; review capture provenance')
    value = json.loads(path.read_bytes())
    require(value['schema'] == 'jy14-native-resource-catalog/v1', 'Unsupported native resource catalog')
    # Only relocate the captured user's home prefix. Relative cache locations,
    # resource IDs, permissions and every resource byte hash remain unchanged.
    def expand_home(item):
        if isinstance(item, dict):
            return {key: expand_home(child) for key, child in item.items()}
        if isinstance(item, list):
            return [expand_home(child) for child in item]
        if isinstance(item, str) and item.startswith('@home/'):
            relative = Path(item[len('@home/'):])
            require(not relative.is_absolute() and '..' not in relative.parts, 'Invalid catalog home-relative path')
            return str(Path.home() / relative)
        return item
    value = expand_home(value)
    supplement = HERE / 'native-resource-catalog-11.5.3.json'
    require(rt.digest(supplement) == CATALOG_1153_SHA, 'Native 11.5.3 resource catalog changed')
    extra = json.loads(supplement.read_bytes())
    require(extra['schema'] == value['schema'] and extra['runtime_profile'] == 'jy14-headless-macos-11.5.3',
            'Unsupported native resource supplement')
    require(not set(extra['resources']).intersection(value['resources']), 'Duplicate captured resource')
    value['resources'].update(extra['resources'])
    for key, files in extra.get('native_generated_cache_files', {}).items():
        value['resources'][key].setdefault('native_generated_cache_files', {}).update(files)
    return expand_home(value)


def definition(key):
    data = catalog()
    require(key in data['resources'], 'Native resource has no verified local capture: ' + key)
    return deepcopy(data['resources'][key])


def relative_path(entry):
    fingerprint = entry['tree_sha256']
    require(len(fingerprint) == 64 and set(fingerprint) <= set('0123456789abcdef'), 'Invalid resource identity')
    return 'Resources/headless-native/' + fingerprint


def wanted_keys(plan):
    result = {'mask/' + s['mask']['shape'] for t in plan['tracks'] for s in t['segments'] if 'mask' in s}
    result.update('transition/' + s['transition_out']['name'] for t in plan['tracks'] for s in t['segments']
                  if 'transition_out' in s)
    result.update(t['type'] + '/' + s['name'] for t in plan['tracks'] if t['type'] in {'filter', 'effect'}
                  for s in t['segments'])
    result.update('text-effect/' + s['text_effect']['name'] for t in plan['tracks'] for s in t['segments']
                  if 'text_effect' in s)
    return sorted(result)


def prepare(plan, folder, runtime):
    keys = wanted_keys(plan)
    if not keys:
        return []
    data = catalog()
    require(runtime['runtime_profile'] == data['runtime_profile'] or
            (runtime['runtime_profile'] == 'jy14-headless-macos-11.5.3' and set(keys) <= (VERIFIED_1153 | PREVIEW_ONLY_1153)),
            'Native resources need their captured or separately verified runtime profile')
    records = []
    for key in keys:
        entry = definition(key)
        require(runtime['runtime_profile'] in entry.get('runtime_profiles', [data['runtime_profile'], 'jy14-headless-macos-11.5.3']),
                'Resource is not verified for this runtime profile')
        source = Path(entry['source'])
        require(source.is_absolute() and source.resolve(strict=True) == source,
                'Native resource source must be the captured canonical directory')
        generated = verify_cached_manifest(entry, tree_manifest(source), 'Native resource bytes differ from captured identity')
        relative = relative_path(entry)
        dest = Path(folder) / relative
        if not dest.exists():
            dest.mkdir(parents=True, exist_ok=False, mode=0o700)
            # Ship only the original effect package, never this machine's
            # optional GPU compiler cache. All names come from the pinned catalog.
            for name in entry['files']:
                copied = dest / name
                copied.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source / name, copied, follow_symlinks=False)
        require(tree_manifest(dest) == entry['files'], 'Native resource changed during copy')
        verify_cached_manifest(entry, tree_manifest(source), 'Native resource changed during copy')
        # Preserve resource contents and metadata; files remain local to the draft.
        for path in [dest] + list(dest.rglob('*')):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        records.append({'key': key, 'relative': relative, 'tree_sha256': entry['tree_sha256'],
                        'files': entry['files'], 'resource_id': entry['material']['resource_id'],
                        'origin': 'native-application-selection', 'redistribution_authorized': False})
        if 'usage' in entry:
            records[-1]['usage'] = deepcopy(entry['usage'])
        if generated:
            records[-1]['source_generated_cache_files_not_copied'] = generated
    return records


def material(key, target):
    entry = definition(key)
    value = deepcopy(entry['material'])
    value['path'] = str(Path(target) / relative_path(entry))
    return value


def verify_files(records, folder, plan):
    require([r['key'] for r in records] == wanted_keys(plan), 'Native resource inventory differs from plan')
    for record in records:
        entry = definition(record['key'])
        require(record['relative'] == relative_path(entry) and record['files'] == entry['files']
                and record['tree_sha256'] == entry['tree_sha256'], 'Native resource record changed')
        require(record.get('usage') == entry.get('usage'), 'Native resource usage boundary changed')
        require(tree_manifest(Path(folder) / record['relative']) == entry['files'], 'Draft-owned native resource bytes changed')


def verify_binding(key, value, target, resolve_native_path, allow_native_cache=False):
    entry = definition(key)
    actual = resolve_native_path(value, Path(target))
    if actual == (Path(target) / relative_path(entry)).resolve():
        return {'key': key, 'location': 'draft-owned'}
    # Observed native 11.4.2 save resolves known resource IDs back to its cache.
    # Accept only that exact captured directory AND its exact contents, never
    # an arbitrary same-named path. This remains an explicit cache dependency.
    require(allow_native_cache and actual == Path(entry['source']), 'Native resource path changed')
    generated = verify_cached_manifest(entry, tree_manifest(actual), 'Native cache resource bytes changed')
    result = {'key': key, 'location': 'native-cache', 'bytes_verified': True}
    if generated:
        result['native_generated_cache_files_verified'] = generated
    return result
