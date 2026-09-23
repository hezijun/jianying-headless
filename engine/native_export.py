#!/usr/bin/env python3
"""Export a verified, immutable build with Jianying's local native renderer.

Only an explicit export command invokes this module. It creates a new private
job, never writes a live draft, and cannot access accounts or the network.
"""
import argparse
from copy import deepcopy
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import jy14_headless as j
import native_edit as edit
import native_motion as motion
import native_resources as resources
import native_compound as compound

HERE = Path(__file__).resolve().parent
PROFILES = {'jy14-headless-macos-11.4.2', 'jy14-headless-macos-11.5.3'}
SCHEMA = 'jy14-native-export/v1'


def captured_mask(node):
    """Only the six captured static mask identities and parameter kinds."""
    catalog = resources.catalog()['resources']
    shapes = {catalog['mask/' + shape]['material']['resource_type']: shape for shape in resources.SHAPES}
    native_type = node.get('resource_type')
    j.require(isinstance(native_type, str) and native_type in shapes, 'Unverified native mask shape')
    shape = shapes[native_type]
    entry = catalog['mask/' + shape]
    j.require(all(node.get(key) == entry['material'][key]
                  for key in ('type', 'category', 'category_id', 'resource_type', 'resource_id')),
              'Unverified native mask resource identity')
    config = node.get('config', {})
    mapping = {'centerX': 'x', 'centerY': 'y', 'width': 'width', 'height': 'height',
               'rotation': 'rotation', 'feather': 'feather', 'invert': 'invert', 'roundCorner': 'round_corner'}
    j.require(isinstance(config, dict) and not set(config) - (set(mapping) | {'aspectRatio'}),
              'Unverified native mask parameter')
    aspect = config.get('aspectRatio', entry['aspect_ratio'])
    j.number(aspect, 'Mask aspect ratio', 0.001, 100)
    j.require(math.isclose(aspect, entry['aspect_ratio'], abs_tol=1e-7), 'Unverified mask aspect ratio')
    spec = {'shape': shape, **{target: config[key] for key, target in mapping.items() if key in config
                              and not (shape == 'line' and key in {'width', 'height'})}}
    motion.validate({'mask': spec}, 'video')
    return entry


def captured_video_effect(node):
    """Only the non-member light-shake capture has isolated pixel evidence."""
    entry = resources.definition('effect/light-shake')
    j.require(entry['usage']['paid_badge_observed'] is False, 'Member effect isolation is not verified')
    template = entry['material']
    for field in ('type', 'effect_id', 'resource_id', 'source_platform', 'apply_target_type', 'category_id'):
        j.require(node.get(field) == template[field], 'Unverified native video effect identity: ' + field)
    j.require(type(node.get('value')) in (int, float) and node['value'] == 1,
              'Unverified native video effect strength')
    expected = {item['name'] for item in template['adjust_params']}
    params = node.get('adjust_params', [])
    j.require(isinstance(params, list) and len(params) == len(expected) and
              all(isinstance(p, dict) and isinstance(p.get('name'), str) and
                  set(p) <= {'name', 'value', 'default_value'} for p in params) and
              {p.get('name') for p in params} == expected, 'Unverified native video effect parameters')
    for param in params:
        j.number(param.get('value'), 'Native video effect parameter', 0, 1)
    return entry


def captured_filter_or_text_effect(node):
    """Exact local captures, with use restrictions preserved, not granted."""
    keys = {'filter': ['filter/hd-monochrome'],
            'text_effect': ['text-effect/orange-outline', 'text-effect/yellow-retro']}
    kind = node.get('type')
    j.require(isinstance(kind, str) and kind in keys, 'Unverified native filter/text effect type')
    matches = [key for key in keys[kind] if resources.definition(key)['material']['resource_id'] == node.get('resource_id')]
    j.require(len(matches) == 1, 'Unverified native filter/text effect identity')
    key = matches[0]
    entry = resources.definition(key)
    for field in ('type', 'effect_id', 'resource_id', 'third_resource_id', 'sub_type',
                  'source_platform', 'category_id'):
        j.require(node.get(field) == entry['material'][field],
                  'Unverified native filter/text effect identity: ' + field)
    j.require(isinstance(node.get('path'), str) and node['path'], 'Missing captured filter/text effect path')
    j.number(node.get('value'), 'Native filter/text effect strength', 0, 1)
    j.require(kind != 'text_effect' or node['value'] == 1, 'Unverified text effect strength')
    return key, entry


def check_filter_and_text_bindings(timeline):
    materials = timeline.get('materials', {})
    effects = materials.get('effects', [])
    filters = {n.get('id'): n for n in effects if n.get('type') == 'filter'}
    flowers = {n.get('id'): n for n in effects if n.get('type') == 'text_effect'}
    j.require(None not in filters and None not in flowers, 'Missing filter/text effect material ID')
    texts = {n.get('id'): n for n in materials.get('texts', [])}
    used_filters, used_flowers = set(), set()
    for track in timeline.get('tracks', []):
        for segment in track.get('segments', []):
            primary = segment.get('material_id')
            refs = set(segment.get('extra_material_refs') or [])
            if track.get('type') == 'filter':
                j.require(primary in filters and not refs.intersection(filters),
                          'Filter track must bind the captured filter as its primary material')
                j.require(not segment.get('common_keyframes'), 'Filter keyframes are not yet verified')
                used_filters.add(primary)
            else:
                j.require(primary not in filters and not refs.intersection(filters),
                          'Captured filter is only verified on a filter track')
            flower_refs = refs.intersection(flowers)
            j.require(primary not in flowers, 'Text effect must be an extra reference on a text segment')
            if track.get('type') != 'text':
                j.require(not flower_refs, 'Captured text effect requires a text segment')
                continue
            material = texts.get(primary)
            j.require(material is not None, 'Text segment has no text material')
            content = j.nd.helper()._parse_strict_json(material.get('content', '').encode('utf-8'), 'native text content')
            styles = content.get('styles', [])
            if not flower_refs:
                j.require(all(not s.get('effectStyle') for s in styles), 'Text effect style has no captured material binding')
                continue
            j.require(len(flower_refs) == 1 and styles, 'Ambiguous or missing captured text effect style')
            node = flowers[next(iter(flower_refs))]
            for style in styles:
                effect_style = style.get('effectStyle', {})
                j.require(effect_style == {'id': node['resource_id'], 'path': node.get('path')},
                          'Text effect style identity/path differs from its captured material')
                fill = style.get('fill', {})
                fill_content = fill.get('content', {})
                solid = fill_content.get('solid', {})
                color = solid.get('color', [])
                j.require(fill_content.get('render_type') == 'solid' and len(color) == 3
                          and all(type(v) in (int, float) and abs(v - 1) < 1e-5 for v in color)
                          and fill.get('alpha', 1) == solid.get('alpha', 1) == 1 and not style.get('strokes'),
                          'Unverified text effect base fill or stroke')
            used_flowers.update(flower_refs)
    j.require(used_filters == set(filters) and used_flowers == set(flowers),
              'Captured filter/text effect has no verified track binding')


def local_supported_features(timeline):
    """Fail closed on effects that this isolated rendering path has not passed."""
    materials = timeline.get('materials', {})
    for node in materials.get('common_mask', []):
        captured_mask(node)
    for node in materials.get('video_effects', []):
        captured_video_effect(node)
    captured_visuals = {captured_filter_or_text_effect(node)[0] for node in materials.get('effects', [])}
    check_filter_and_text_bindings(timeline)
    allowed = {'videos', 'audios', 'texts', 'canvases', 'beats', 'material_animations',
               'placeholder_infos', 'speeds', 'sound_channel_mappings', 'material_colors',
               'loudnesses', 'vocal_separations', 'transitions', 'common_mask', 'drafts', 'video_effects', 'effects'}
    unsupported = sorted(k for k, nodes in materials.items() if nodes and k not in allowed)
    j.require(not unsupported, 'Unverified native export material types: ' + ', '.join(unsupported))
    j.require(all(t.get('type') in {'video', 'text', 'audio', 'effect', 'filter'} for t in timeline.get('tracks', [])),
              'Unverified native export track type')
    effect_ids = {node.get('id') for node in materials.get('video_effects', [])}
    for track in timeline.get('tracks', []):
        if track.get('type') == 'effect':
            for segment in track.get('segments', []):
                j.require(segment.get('material_id') in effect_ids and segment.get('material_id') is not None,
                          'Effect track must bind the captured video effect')
                j.require(not segment.get('common_keyframes'), 'Video effect keyframes are not yet verified')
        else:
            for segment in track.get('segments', []):
                j.require(not effect_ids.intersection([segment.get('material_id')] + segment.get('extra_material_refs', [])),
                          'Captured video effect is only verified on an effect track')
    for node in materials.get('material_animations', []):
        j.require(not node.get('animations'), 'Text/clip animations are not yet verified for native headless export')
    for node in materials.get('speeds', []):
        j.require(not node.get('curve_speed'), 'Curve speed export is not yet verified')
    for node in materials.get('transitions', []):
        j.require(node.get('effect_id') == '6724845717472416269', 'Only the captured dissolve transition is verified')
    for node in materials.get('canvases', []):
        j.require(node.get('type') == 'canvas_color', 'Only solid canvas backgrounds are verified')
    compound_ids = {node['id'] for node in materials.get('drafts', [])}
    compound_refs = {ref for track in timeline.get('tracks', []) for segment in track.get('segments', [])
                     if compound_ids.intersection(segment.get('extra_material_refs', []))
                     for ref in segment.get('extra_material_refs', [])}
    for node in materials.get('sound_channel_mappings', []):
        default_compound = node['id'] in compound_refs and node.get('type') == '' and not any(
            value for key, value in node.items() if key not in {'id', 'type'})
        j.require(node.get('type') == 'none' or default_compound, 'Custom audio channel mappings are not yet verified')
    warnings = []
    if materials.get('transitions'):
        warnings.append({
            'code': 'native-dissolve-audio-overlap',
            'message': 'Dissolve video is verified, but source audio can sum across the overlap; '
                       'two same-phase source tones measured +6.03 dB. Review output audio; no gain correction is applied.',
            'native_ui_audio_comparison': 'matched on the same synthetic timeline; +6.019 dB UI vs +6.030 dB headless',
        })
    if materials.get('video_effects'):
        warnings.append({'code': 'native-resource-use-limits',
                         'resource': 'effect/light-shake',
                         'message': 'Isolated rendering is verified on the captured non-member sample; '
                                    'this does not grant redistribution or establish commercial rights.'})
    for key in sorted(captured_visuals):
        usage = resources.definition(key)['usage']
        warnings.append({'code': 'native-resource-use-limits', 'resource': key, 'usage': usage,
                         'message': 'Technical rendering verified on the same-machine local sample after successful native UI export. '
                                    'This does not acquire or prove ongoing account entitlement, commercial rights, or redistribution rights. '
                                    'Use only within existing native authorization; account data is not read.'})
    return {'content_scope': 'local single-timeline video, text, audio, linear keyframes, captured dissolve, six static geometric masks and three captured visual resources',
            'mask_export': 'six captured shapes verified on the synthetic sample; inspect each actual output',
            'visual_acceptance': 'requires viewing the exported output',
            'audio_acceptance': 'requires checking the exported audio; stream presence is not audio quality acceptance',
            'warnings': warnings}


def check_profile_features(timeline, profile):
    """Scope new ABI support to the local multitrack sample actually rendered."""
    j.require(profile in PROFILES, 'No native export ABI for this runtime')
    if profile != 'jy14-headless-macos-11.5.3':
        return
    rows = compound.graph(timeline) if timeline.get('materials', {}).get('drafts') else [(None, timeline)]
    for _, child in rows:
        for node in child.get('materials', {}).get('effects', []):
            key, _ = captured_filter_or_text_effect(node)
            j.require(key == 'text-effect/yellow-retro',
                      'Native export on 11.5.3 requires verified native resource authorization: ' + key)


def supported_features(timeline):
    # This feature-only helper also accepts partial noncompound fixtures. Full
    # structural validation remains mandatory in verified_build before export.
    rows = compound.validate(timeline, edit.basic_validation) if timeline.get('materials', {}).get('drafts') else [(None, timeline)]
    result = local_supported_features(timeline)
    for owner, child in rows[1:]:
        child_result = local_supported_features(child)
        result['warnings'].extend({**warning, 'timeline_id': child['id']} for warning in child_result['warnings'])
    result['nested_timelines'] = len(rows) - 1
    if len(rows) > 1:
        result['content_scope'] += '; captured editable local combination compounds, with every child checked'
    return result


def verified_build(path):
    path = Path(path).resolve(strict=True)
    record = j.read_json(path / 'build.json')
    if record.get('schema') == 'jy14-headless-build/v1':
        record = j.verify_build(path)
    elif record.get('schema') == edit.BUILD_SCHEMA:
        record = edit.verify_build(path)
    else:
        raise ValueError('Export requires a supported verified headless/edit build')
    j.require(record['runtime_profile'] in PROFILES, 'No native export ABI for this build profile')
    timeline = j.nd.helper()._decrypt_metadata_in_memory(path / 'draft/draft_info.json')
    compound.validate(timeline, edit.basic_validation)
    j.require(timeline.get('duration', 0) > 0 and timeline.get('tracks'), 'Cannot export an empty timeline')
    return path, record, timeline


def source_in_build(raw, target, folder):
    """Map only draft-owned resources into the immutable build snapshot."""
    j.require(isinstance(raw, str) and raw, 'Missing export dependency path')
    resolved = j.native_media_path(raw, target)
    j.require(resolved.is_relative_to(target), 'Export dependency is not draft-owned; materialize it first')
    relative = resolved.relative_to(target)
    j.require(relative.parts and relative.parts[0] == 'Resources', 'Export dependencies must be in Resources')
    source = folder / relative
    j.require(source.exists() and not source.is_symlink() and source.resolve().is_relative_to(folder),
              'Export dependency is missing, symlinked, or outside the build')
    return source, relative


def stage_timeline(timeline, record, folder, out):
    value = deepcopy(timeline)
    target = Path(record['target']).resolve()
    files, mapping, canonical_mapping = {}, {}, {}
    sidecars = []

    def copy_file(relative, info):
        copied = out / relative
        source = folder / relative
        j.require(record['files'].get(str(relative)) == info, 'Export resource differs from the verified build manifest')
        if not copied.exists():
            copied.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with source.open('rb') as src, copied.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            os.chmod(copied, 0o600)
        j.require(j.nd.digest(copied) == info['sha256'] == j.nd.digest(source), 'Export dependency changed while copying')
        files[str(relative)] = info

    graph = list(compound.graph(value)) if value.get('materials', {}).get('drafts') else [(None, value)]
    nodes = [(bucket, node) for _, timeline_node in graph
             for bucket in ('videos', 'audios', 'common_mask', 'transitions', 'video_effects', 'audio_effects', 'effects')
             for node in timeline_node.get('materials', {}).get(bucket, [])]
    for bucket, node in nodes:
        raw = node.get('path')
        if not raw:
            continue
        source, relative = source_in_build(raw, target, folder)
        destination = out / relative
        if source.is_dir():
            manifest = resources.tree_manifest(source)
            if bucket == 'common_mask':
                j.require(manifest == captured_mask(node)['files'],
                          'Mask export resource bytes differ from the captured native resource')
            if bucket == 'video_effects':
                j.require(manifest == captured_video_effect(node)['files'],
                          'Video effect export resource bytes differ from the captured native resource')
            if bucket == 'effects':
                j.require(manifest == captured_filter_or_text_effect(node)[1]['files'],
                          'Filter/text effect export resource bytes differ from the captured native resource')
            expected = {str(relative / name): info for name, info in manifest.items()}
        else:
            j.require(bucket not in {'common_mask', 'video_effects', 'effects'},
                      'Native visual resources must be captured directories')
            expected = {str(relative): {'sha256': j.nd.digest(source), 'size': source.stat().st_size}}
        j.require(all(record['files'].get(name) == info for name, info in expected.items()),
                  'Export resource differs from the verified build manifest')
        for name, info in expected.items():
            copy_file(Path(name), info)
        mapping[raw] = str(destination)
        canonical_mapping[str(j.native_media_path(raw, target))] = str(destination)

    for owner, child in graph:
        if owner is None:
            continue
        for key, source_path in compound.paths(owner, target).items():
            relative = source_path.relative_to(target)
            source = folder / relative
            j.require(source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(folder.resolve()),
                      'Missing or unsafe compound export sidecar')
            info = {'sha256': j.nd.digest(source), 'size': source.stat().st_size}
            j.require(record['files'].get(str(relative)) == info, 'Compound sidecar differs from the verified build')
            mapping[owner[key]] = str(out / relative)
            canonical_mapping[str(source_path)] = str(out / relative)
            if key == 'draft_cover_path':
                copy_file(relative, info)
            else:
                sidecars.append((relative, j.read_json(source), info))

    def replace(item, key='', sidecar_base=None):
        if isinstance(item, dict):
            result = {k: replace(v, k, sidecar_base) for k, v in item.items()}
            if item.get('type') == 'text' and isinstance(item.get('content'), str):
                content = j.nd.helper()._parse_strict_json(item['content'].encode('utf-8'), 'native text content')
                j.require(isinstance(content, dict) and isinstance(content.get('styles'), list),
                          'Unsupported native text content')
                # Native effectStyle/font paths are inside serialized JSON,
                # not direct material fields. Keep its literal text unchanged.
                result['content'] = json.dumps(replace(content, sidecar_base=sidecar_base),
                                               ensure_ascii=False, separators=(',', ':'))
            return result
        if isinstance(item, list):
            return [replace(v, key, sidecar_base) for v in item]
        if isinstance(item, str):
            if key == 'text':
                return item
            if item in mapping:
                return mapping[item]
            # Text itself can contain URLs; do not confuse it with a dependency.
            if key.endswith('path') and item:
                if item.startswith(compound.SUBDRAFT_TOKEN):
                    filename = item[len(compound.SUBDRAFT_TOKEN):]
                    j.require(sidecar_base is not None and filename in compound.SIDECARS.values(), 'Unsafe subdraft placeholder')
                    return str(sidecar_base / filename)
                if sidecar_base is not None and item in compound.SIDECARS.values():
                    return str(sidecar_base / item)
                canonical = str(j.native_media_path(item, target))
                if canonical in canonical_mapping:
                    return canonical_mapping[canonical]
                path = Path(item)
                j.require(path.is_absolute() and path.is_relative_to(j.nd.APP) and path.is_file(),
                          'Unstaged/unsupported export resource path: ' + key)
            if key.endswith('url') and item:
                raise ValueError('Online dependency is not supported by isolated export')
        return item

    for relative, sidecar, info in sidecars:
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        j.write(destination, replace(sidecar, sidecar_base=destination.parent))
        files[str(relative)] = {'sha256': j.nd.digest(destination), 'size': destination.stat().st_size}
        j.require(j.nd.digest(folder / relative) == info['sha256'], 'Compound source sidecar changed while staging')
    return replace(value), files


def settings_for(timeline, bitrate, timeout):
    width, height = (timeline['canvas_config'][key] for key in ('width', 'height'))
    fps = timeline.get('fps', 30)
    for dimension in (width, height):
        j.require(type(dimension) is int and 16 <= dimension <= 7680 and dimension % 2 == 0,
                  'Native H.264 export requires even canvas dimensions, 16..7680')
    j.number(fps, 'Export fps', 1, 120)
    j.require(type(bitrate) is int and 100_000 <= bitrate <= 200_000_000, 'Bitrate must be 100000..200000000')
    j.require(type(timeout) is int and 5 <= timeout <= 43200, 'Timeout must be 5..43200 seconds')
    return dict(width=width, height=height, fps=fps, bitrate=bitrate, timeout_seconds=timeout)


def sandbox_profile(out):
    literal = json.dumps(str(out), ensure_ascii=False)
    return ('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'
            '(deny file-read-data (subpath "/Users"))\n'
            '(deny file-read-data (subpath "/Library/Keychains"))\n'
            f'(allow file-read-data (subpath {literal}))\n'
            f'(allow file-write* (subpath {literal}))\n'
            '(allow file-write* (literal "/dev/null"))\n').encode()


def validate_probe(info, settings, duration_us, audio_expected):
    fmt = info.get('format', {})
    j.require(fmt.get('tags', {}).get('major_brand') in ('isom', 'mp41', 'mp42'),
              'Native output is not a standard MP4 container')
    video = [s for s in info.get('streams', []) if s.get('codec_type') == 'video']
    audio = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
    j.require(len(video) == 1 and video[0].get('codec_name') == 'h264', 'Expected one H.264 stream')
    v = video[0]
    j.require((v.get('width'), v.get('height')) == (settings['width'], settings['height']), 'Export canvas changed')
    actual_fps = float(Fraction(v['r_frame_rate']))
    j.require(math.isclose(actual_fps, settings['fps'], abs_tol=0.001), 'Export frame rate changed')
    expected_frames = round(duration_us / 1_000_000 * settings['fps'])
    j.require(abs(int(v.get('nb_frames', -999)) - expected_frames) <= 1, 'Export frame count is incomplete')
    actual_duration = float(fmt.get('duration', 0))
    j.require(math.isfinite(actual_duration) and abs(actual_duration - duration_us / 1_000_000) <=
              max(0.05, 1 / settings['fps']), 'Export duration changed')
    j.require(len(audio) <= 1 and (not audio_expected or len(audio) == 1), 'Expected native audio stream is missing')
    j.require(all(a.get('codec_name') == 'aac' for a in audio), 'Unexpected export audio codec')
    return {'duration_seconds': actual_duration, 'frames': int(v['nb_frames']),
            'expected_frames': expected_frames, 'frame_delta': int(v['nb_frames']) - expected_frames,
            'duration_delta_seconds': round(actual_duration - duration_us / 1_000_000, 6),
            'width': v['width'], 'height': v['height'], 'fps': actual_fps,
            'video_codec': 'h264', 'audio_codec': 'aac' if audio else None,
            'major_brand': fmt['tags']['major_brand']}


def stage_compound_inputs(timeline, out):
    """Supply each embedded child to 11.5.3's shallow runtime deserializer."""
    files = {}
    for owner, child in compound.graph(timeline):
        if owner is None:
            continue
        identity = owner['combination_id']
        j.require(len(identity) == 36 and all(c in '0123456789abcdefABCDEF-' for c in identity),
                  'Invalid compound identity for export')
        name = 'compound-' + identity + '.json'
        j.require(name not in files, 'Duplicate compound export identity')
        path = out / name
        j.require(not path.exists() and not path.is_symlink(), 'Compound staged input already exists')
        j.write(path, child)
        files[name] = {'sha256': j.nd.digest(path), 'size': path.stat().st_size}
    return files


def verify_runtime_graph(expected, actual):
    # A decodable MP4 can be black if GetDraftFromJson drops deeper children.
    compound.check_order(expected, actual)
    expected_rows = {child['id']: child for _, child in compound.graph(expected)}
    for _, child in compound.graph(actual):
        wanted = expected_rows[child['id']]
        j.require(child.get('duration') == wanted.get('duration'), 'Runtime timeline duration changed')
        for left, right in zip(wanted.get('tracks', []), child.get('tracks', [])):
            for source, rendered in zip(left.get('segments', []), right.get('segments', [])):
                for key in ('material_id', 'source_timerange', 'target_timerange'):
                    left_value, right_value = source.get(key), rendered.get(key)
                    if key.endswith('_timerange'):
                        left_value = {'start': 0, **left_value} if isinstance(left_value, dict) else left_value
                        right_value = {'start': 0, **right_value} if isinstance(right_value, dict) else right_value
                    j.require(left_value == right_value, 'Runtime clip source/timing changed')


def frame_complete_or_retry(profile, media, attempt):
    if profile != 'jy14-headless-macos-11.5.3' or media['frame_delta'] == 0:
        return True
    j.require(media['frame_delta'] == -1 and attempt < 3,
              'Frame-exact native export failed; incomplete attempts retained')
    return False


def run(build, out, bitrate=4_000_000, timeout=600):
    build, record, timeline = verified_build(build)
    check_profile_features(timeline, record['runtime_profile'])
    capabilities = supported_features(timeline)
    if record['runtime_profile'] == 'jy14-headless-macos-11.5.3':
        capabilities.update(content_scope='local video, source audio, plain text, cuts, constant speed, picture-in-picture, linear keyframes, six static masks, dissolve, light-shake, yellow-retro text and offline compounds',
                            mask_export='six shapes rendered and inspected on 11.5.3')
    settings = settings_for(timeline, bitrate, timeout)
    job = Path(out)
    j.require(job.is_absolute() and not job.exists() and not job.is_symlink(), 'Export job must be a new absolute directory')
    j.require('work' in job.parts, 'Export jobs must be under the task work directory')
    j.require(not job.resolve().is_relative_to(build) and not job.resolve().is_relative_to(j.nd.DRAFT_ROOT),
              'Export job cannot be inside a build or live draft')
    runtime = j.nd.validate_runtime()
    j.require(runtime['runtime_profile'] in PROFILES and runtime['runtime_profile'] == record['runtime_profile'],
              'Native export runtime must match the verified build profile')
    job = j.nd.fresh_directory(job)
    started = time.monotonic()
    evidence = {'schema': SCHEMA, 'status': 'preparing', 'build': str(build),
                'build_sha256': j.nd.digest(build / 'build.json'), 'runtime': runtime,
                'settings': settings, 'ui_used': False, 'network_allowed': False,
                'live_draft_written': False, 'external_ffmpeg_composition': False,
                'renderer': 'Jianying native ExportService', 'container_writer': 'native MP4 writer',
                'acceptance_boundaries': capabilities}
    try:
        staged, files = stage_timeline(timeline, record, build / 'draft', job)
        if runtime['runtime_profile'] == 'jy14-headless-macos-11.5.3':
            files.update(stage_compound_inputs(staged, job))
        j.write(job / 'timeline.json', staged)
        timeline_hash = j.nd.digest(job / 'timeline.json')
        j.write(job / 'inputs.json', {'files': files, 'timeline_sha256': timeline_hash})
        j.write(job / 'export.sb', sandbox_profile(job))
        helper = job / 'native-export-helper'
        frameworks = j.nd.APP / 'Contents/Frameworks'
        command = ['/usr/bin/xcrun', 'clang++', '-std=c++17', '-arch', 'arm64', '-O2',
                   '-Wno-deprecated-declarations', str(HERE / 'native_export.cpp'),
                   '-L' + str(frameworks), '-lvideoeditor', '-Wl,-rpath,' + str(frameworks), '-o', str(helper)]
        compiler_tmp = job / 'compiler-tmp'
        compiler_tmp.mkdir(mode=0o700)
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C',
               'TMPDIR': str(compiler_tmp) + '/',
               'CLANG_MODULE_CACHE_PATH': str(compiler_tmp / 'modules')}
        compile_result = subprocess.run(command, capture_output=True, timeout=120, env=env)
        j.write(job / 'compiler.stderr.log', compile_result.stderr)
        j.require(compile_result.returncode == 0, 'Native export helper compilation failed')
        os.chmod(helper, 0o700)
        evidence['helper_sha256'] = j.nd.digest(helper)
        evidence['adapter_source_sha256'] = j.nd.digest(HERE / 'native_export.cpp')
        output = job / 'render.mp4'
        has_masks = any(child.get('materials', {}).get('common_mask') for _, child in compound.graph(timeline))
        command = ['/usr/bin/sandbox-exec', '-f', str(job / 'export.sb'), str(helper),
                   str(job / 'timeline.json'), str(output), str(settings['width']), str(settings['height']),
                   str(settings['fps']), str(bitrate), str(timeout),
                   '1' if has_masks else '0']
        if has_masks:
            root = j.nd.APP / 'Contents/Resources/lumi_js_resources_video'
            evidence['built_in_mask_runtime'] = {
                'root': str(root), 'from_signed_app_bundle': True, 'account_data_used': False,
                'config_sha256': j.nd.digest(root / 'config.json'),
                'javascript_sha256': j.nd.digest(root / 'js/video/video.js')}
        evidence['native_attempts'] = []
        for attempt in range(1, 4):
            with (job / 'native.stdout.log').open('xb') as stdout, (job / 'native.stderr.log').open('xb') as stderr:
                os.chmod(stdout.name, 0o600); os.chmod(stderr.name, 0o600)
                try:
                    result = subprocess.run(command, cwd=job, stdout=stdout, stderr=stderr, env=env, timeout=timeout + 30)
                except subprocess.TimeoutExpired as error:
                    raise ValueError('Native export timed out; partial output retained') from error
            evidence['native_returncode'] = result.returncode
            evidence['native_completion_event'] = 'JY_NATIVE_EXPORT_DONE' in (job / 'native.stdout.log').read_text()
            evidence['native_async_completion_event'] = 'JY_NATIVE_COMPLETION_DONE' in (job / 'native.stderr.log').read_text()
            if runtime['runtime_profile'] == 'jy14-headless-macos-11.5.3':
                j.require(evidence['native_async_completion_event'], 'Native asynchronous completion was not observed')
            evidence['native_restore_completion_event'] = 'JY_NATIVE_RESTORE_DONE' in (job / 'native.stderr.log').read_text()
            j.require(result.returncode == 0 and evidence['native_completion_event'] and evidence['native_restore_completion_event'],
                      'Native restoration/export did not complete successfully')
            if runtime['runtime_profile'] == 'jy14-headless-macos-11.5.3':
                verify_runtime_graph(staged, j.read_json(job / 'runtime-timeline.json'))
                evidence['runtime_timeline_structure_verified'] = True
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                                        '-of', 'json', str(output)], timeout=60))
            j.write(job / 'ffprobe.json', probe)
            audio_expected = any(bool(child['materials'].get('audios')) or any(
                v.get('has_audio', True) for v in child['materials'].get('videos', [])
                if v.get('type') == 'video' and v.get('path')) for _, child in compound.graph(timeline))
            evidence['media'] = validate_probe(probe, settings, timeline['duration'], audio_expected)
            media = evidence['media']
            evidence['native_attempts'].append({'attempt': attempt, 'frames': media['frames'],
                                               'expected_frames': media['expected_frames'],
                                               'frame_delta': media['frame_delta']})
            if frame_complete_or_retry(runtime['runtime_profile'], media, attempt):
                break
            # Same immutable input, fresh native process. Never pad/duplicate frames.
            archived = job / ('incomplete-attempt-' + str(attempt))
            archived.mkdir(mode=0o700)
            for name in ('native.stdout.log', 'native.stderr.log', 'runtime-timeline.json', 'ffprobe.json', 'render.mp4'):
                (job / name).rename(archived / name)
            j.require(all(j.nd.digest(job / name) == item['sha256'] for name, item in files.items()) and
                      j.nd.digest(job / 'timeline.json') == timeline_hash, 'Input changed before native retry')
        evidence['frame_exact'] = evidence['media']['frame_delta'] == 0
        if not evidence['frame_exact']:
            capabilities['warnings'].append({'code': 'legacy-one-frame-tolerance',
                                            'message': 'Legacy native output is not frame-exact'})
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(output), '-f', 'null', '-'],
                                 capture_output=True, timeout=timeout)
        j.write(job / 'decode.stderr.log', decoded.stderr)
        j.require(decoded.returncode == 0, 'Native MP4 did not fully decode')
        evidence['full_decode_passed'] = True
        j.require(all(j.nd.digest(job / name) == item['sha256'] for name, item in files.items()),
                  'Native export modified an input resource')
        j.require(j.nd.digest(job / 'timeline.json') == timeline_hash, 'Native export modified its timeline input')
        j.require(j.files_manifest(build / 'draft') == record['files'], 'Source build changed during export')
        evidence.update(status='encoded-and-decoded', output=str(output), output_sha256=j.nd.digest(output),
                        output_bytes=output.stat().st_size, source_build_unchanged=True,
                        visual_content_acceptance='requires viewing the actual output',
                        elapsed_seconds=round(time.monotonic() - started, 3))
        os.chmod(output, 0o600)
        j.write(job / 'result.json', evidence)
        return evidence
    except Exception as error:
        partial = job / 'render.mp4'
        if partial.is_file() and not partial.is_symlink():
            partial.rename(job / 'partial-render.mp4')
        evidence.update(status='failed', error=str(error), partial_artifacts_retained=True)
        j.write(job / 'result.json', evidence)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, help='Verified headless/edit build snapshot, not the live editor state')
    parser.add_argument('--out', required=True, help='New absolute work directory; output is render.mp4')
    parser.add_argument('--bitrate', type=int, default=4_000_000)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    try:
        result = run(args.build, args.out, args.bitrate, args.timeout)
        print(json.dumps({k: result[k] for k in ('status', 'output', 'media', 'full_decode_passed',
                                                'source_build_unchanged', 'elapsed_seconds', 'frame_exact', 'native_attempts',
                                                'acceptance_boundaries')}, ensure_ascii=False))
    except Exception as error:
        print('Native export failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
