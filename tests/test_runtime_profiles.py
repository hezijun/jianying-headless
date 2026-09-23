"""Profile separation, runtime identity, and Unicode sandbox regression tests."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
import headless_runtime as runtime
import native_export


class RuntimeProfiles(unittest.TestCase):
    def setUp(self):
        work = ROOT / 'work' / 'profile-tests'
        work.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='case-', dir=work))
        self.app = self.root / 'Editor.app'
        self.bridge = self.root / 'bridge'
        self.bridge.mkdir()
        (self.app / 'Contents/Frameworks').mkdir(parents=True)
        self.library = self.app / 'Contents/Frameworks/libvideoeditor.dylib'
        self.library.write_bytes(b'fixture-library')
        (self.bridge / 'SOURCE_MANIFEST.json').write_bytes(b'{}')
        self.codec = self.bridge / 'test-codec'
        self.codec.write_bytes(b'fixture-codec')
        self.codec.chmod(0o700)
        self.info = self.app / 'Contents/Info.plist'
        self.set_info()
        self.patches = [
            patch.object(runtime, 'APP', self.app),
            patch.object(runtime, 'BACKEND', self.bridge),
            patch.object(runtime, 'PINS', {}),
            patch.object(runtime, 'IO_MANIFEST_SHA', hashlib.sha256(b'{}').hexdigest()),
            patch.object(runtime, 'PROFILES', {'11.5.3': runtime.digest(self.library)}),
            patch.object(runtime, 'CODECS', {'11.5.3': ('test-codec', runtime.digest(self.codec))}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def set_info(self, **extra):
        data = dict(CFBundleShortVersionString='11.5.3', CFBundleVersion='11.5.3',
                    CFBundleIdentifier=runtime.BUNDLE_ID)
        data.update(extra)
        self.info.write_bytes(plistlib.dumps(data))

    def test_1153_enables_export_but_not_captured_effects(self):
        self.assertEqual(runtime.doctor()['capabilities'],
                         dict(draft_build=True, native_export=True, captured_effects=False))

    def test_unknown_version_rejected_even_with_matching_bytes(self):
        self.set_info(CFBundleShortVersionString='11.5.4', CFBundleVersion='11.5.4')
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            runtime.doctor()

    def test_build_and_identity_must_match(self):
        for extra in ({'CFBundleVersion': '11.5.4'}, {'CFBundleIdentifier': 'other.app'}):
            self.set_info(**extra)
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                runtime.doctor()

    def test_library_change_is_rejected(self):
        self.library.write_bytes(b'other-library')
        with self.assertRaisesRegex(ValueError, 'Editor library differs'):
            runtime.doctor()

    def test_codec_change_and_symlink_rejected(self):
        self.codec.write_bytes(b'other-codec')
        with self.assertRaisesRegex(ValueError, 'Codec differs'):
            runtime.doctor()
        moved = self.codec.with_suffix('.saved')
        self.codec.rename(moved)
        self.codec.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, 'Codec unavailable'):
            runtime.doctor()


class ProfileConsistency(unittest.TestCase):
    def test_schema_migration_is_scoped_to_1153(self):
        runtime.validate_timeline_version({'version': 360000, 'new_version': '187.0.0'},
                                          'jy14-headless-macos-11.5.3')
        for profile, numeric, version in (
                ('jy14-headless-macos-11.4.2', 360000, '187.0.0'),
                ('jy14-headless-macos-11.5.3', 360000, '188.0.0'),
                ('jy14-headless-macos-11.5.3', 360001, '187.0.0'),
                ('jy14-headless-macos-11.5.4', 360000, '187.0.0')):
            with self.assertRaisesRegex(ValueError, 'Unexpected native timeline'):
                runtime.validate_timeline_version({'version': numeric, 'new_version': version}, profile)

    def test_builder_and_runtime_profiles_agree(self):
        spec = importlib.util.spec_from_file_location('codec_builder_test', ROOT / 'tools/build_native_codec.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertEqual(builder.PROFILES, runtime.PROFILES)
        self.assertEqual(builder.CODECS, runtime.CODECS)
        self.assertEqual(native_export.PROFILES, {'jy14-headless-macos-11.4.2', 'jy14-headless-macos-11.5.3'})

    def test_export_features_fail_closed_on_1153(self):
        profile = 'jy14-headless-macos-11.5.3'
        native_export.check_profile_features({'materials': {}, 'tracks': []}, profile)
        resources = native_export.resources
        for key in ('filter/hd-monochrome', 'text-effect/orange-outline'):
            node = resources.definition(key)['material']
            node['path'] = '/local/owned/resource'
            with self.assertRaisesRegex(ValueError, 'authorization'):
                native_export.check_profile_features({'materials': {'effects': [node]}}, profile)
        node = resources.definition('text-effect/yellow-retro')['material']
        node['path'] = '/local/owned/resource'
        native_export.check_profile_features({'materials': {'effects': [node]}}, profile)
        node['resource_id'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'Unverified'):
            native_export.check_profile_features({'materials': {'effects': [node]}}, profile)
        with self.assertRaisesRegex(ValueError, 'No native export ABI'):
            native_export.check_profile_features({}, 'jy14-headless-macos-11.5.4')

    def test_native_save_default_mapping_is_narrow(self):
        import native_edit as edit
        path = '/materials/sound_channel_mappings/test'
        with patch.object(edit.j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.5.3'}):
            edit.preserved({'id': 'test', 'type': ''}, {'id': 'test', 'type': 'none'}, path)
            for bad in ({'id': 'test', 'type': 'left'}, {'id': 'test', 'type': 'none', 'mapping': [1]}):
                with self.assertRaises(ValueError):
                    edit.preserved({'id': 'test', 'type': ''}, bad, path)
        with patch.object(edit.j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.4.2'}):
            with self.assertRaises(ValueError):
                edit.preserved({'id': 'test', 'type': ''}, {'id': 'test', 'type': 'none'}, path)

    def test_inline_compounds_are_version_scoped_and_partial_paths_rejected(self):
        import native_compound as c
        owner = {'id': 'owner', 'type': 'combination', 'combination_type': 'none',
                 'combination_id': 'combo', 'draft': {'id': 'child'}}
        root = {'id': 'root', 'materials': {'drafts': [owner]}}
        with patch.object(c.j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.5.3'}):
            self.assertEqual(c.paths(owner, Path('/test')), {})
            with self.assertRaisesRegex(ValueError, 'blocked'):
                c.require_publishable(root)
            owner['draft_file_path'] = ''
            with self.assertRaises(ValueError):
                c.paths(owner, Path('/test'))
            with self.assertRaisesRegex(ValueError, 'blocked'):
                c.require_publishable(root)
            owner.pop('draft_file_path')
        with patch.object(c.j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.4.2'}):
            with self.assertRaisesRegex(ValueError, '11.5.3'):
                c.paths(owner, Path('/test'))
            with self.assertRaisesRegex(ValueError, 'blocked'):
                c.require_publishable(root)

    def test_publish_requires_sync_loader_and_external_sidecars(self):
        import native_compound as c
        import native_ui_compat as compat
        owner = {'id': 'owner', 'type': 'combination', 'combination_type': 'none',
                 'combination_id': 'combo', 'draft': {'id': 'child'},
                 'draft_file_path': '/test/subdraft/child/draft_content.json',
                 'draft_cover_path': '/test/subdraft/child/draft_cover.jpg',
                 'draft_config_path': '/test/subdraft/child/sub_draft_config.json'}
        root = {'id': 'root', 'materials': {'drafts': [owner]}}
        with patch.object(c.j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.5.3'}):
            with patch.object(compat, 'state', return_value={'async_enabled': True}):
                with self.assertRaisesRegex(ValueError, 'ui-compat'):
                    c.require_publishable(root)
            with patch.object(compat, 'state', return_value={'async_enabled': False}):
                c.require_publishable(root)

    def test_deep_ui_compound_rejected_before_settings_access(self):
        import native_compound as c
        import native_ui_compat as compat
        def owner(name, child):
            return {'id': name, 'type': 'combination', 'combination_type': 'none',
                    'combination_id': name, 'draft': child,
                    **{key: '/owned/' + file for key, file in c.SIDECARS.items()}}
        leaf = {'id': 'leaf'}
        child = {'id': 'child', 'materials': {'drafts': [owner('inner', leaf)]}}
        root = {'id': 'root', 'materials': {'drafts': [owner('outer', child)]}}
        with patch.object(c.j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.5.3'}):
            with patch.object(compat, 'state') as state:
                with self.assertRaisesRegex(ValueError, 'multiple nested levels'):
                    c.require_publishable(root)
                state.assert_not_called()

    def test_frame_exact_retry_is_bounded_and_only_for_one_missing_frame(self):
        profile = 'jy14-headless-macos-11.5.3'
        self.assertTrue(native_export.frame_complete_or_retry(profile, {'frame_delta': 0}, 1))
        for attempt in (1, 2):
            self.assertFalse(native_export.frame_complete_or_retry(profile, {'frame_delta': -1}, attempt))
        for delta, attempt in ((-1, 3), (-2, 1), (1, 1)):
            with self.assertRaisesRegex(ValueError, 'Frame-exact'):
                native_export.frame_complete_or_retry(profile, {'frame_delta': delta}, attempt)
        self.assertTrue(native_export.frame_complete_or_retry('jy14-headless-macos-11.4.2', {'frame_delta': -1}, 1))

    def test_nested_effect_cannot_bypass_runtime_authorization(self):
        node = native_export.resources.definition('filter/hd-monochrome')['material']
        node['path'] = '/owned/resource'
        child = {'id': 'child', 'materials': {'effects': [node]}}
        timeline = {'id': 'root', 'materials': {'drafts': [
            {'id': 'owner', 'type': 'combination', 'combination_type': 'none',
             'combination_id': 'combo', 'draft': child}]}}
        with self.assertRaisesRegex(ValueError, 'authorization'):
            native_export.check_profile_features(timeline, 'jy14-headless-macos-11.5.3')

    def test_saved_187_edit_schema_is_not_enabled_on_old_runtime(self):
        import native_edit as e
        timeline = {'version': 360000, 'new_version': '187.0.0', 'materials': {}, 'tracks': []}
        with patch.object(e.j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.5.3'}):
            self.assertEqual(e.basic_validation(timeline), 0)
            e.preserved(dict(timeline, new_version='185.0.0'), timeline)
            with self.assertRaises(ValueError):
                e.preserved(timeline, dict(timeline, new_version='188.0.0'))
        with patch.object(e.j.nd, 'doctor', return_value={'runtime_profile': 'jy14-headless-macos-11.4.2'}):
            with self.assertRaises(ValueError):
                e.basic_validation(timeline)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS seatbelt integration')
    def test_unicode_sandbox_allows_only_its_output_directory(self):
        work = ROOT / 'work/profile-tests'
        work.mkdir(parents=True, exist_ok=True)
        base = Path(tempfile.mkdtemp(prefix='中文-', dir=work)).resolve()
        job = base / '输出'
        job.mkdir()
        inside = job / '允许.txt'
        outside = base / '禁止.txt'
        inside.write_text('inside')
        outside.write_text('outside')
        profile = job / 'profile.sb'
        profile.write_bytes(native_export.sandbox_profile(job))
        for path, succeeds in ((inside, True), (outside, False)):
            read = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), '/bin/cat', str(path)],
                                  capture_output=True, timeout=10)
            self.assertEqual(read.returncode == 0, succeeds, read.stderr)
        write = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), '/usr/bin/touch', str(base / 'escape')],
                               capture_output=True, timeout=10)
        self.assertNotEqual(write.returncode, 0)
        self.assertFalse((base / 'escape').exists())


class RecursiveExportRegression(unittest.TestCase):
    def fixture(self):
        leaf = {'id': 'leaf', 'duration': 2000000, 'tracks': [], 'materials': {'drafts': []}}
        root = {'id': 'root', 'duration': 2000000, 'tracks': [], 'materials': {'drafts': [
            {'id': 'owner', 'type': 'combination', 'combination_type': 'none',
             'combination_id': '12345678-1234-1234-1234-123456789abc', 'draft': leaf}]}}
        return root

    def test_dropped_child_rejected_even_if_mp4_decodes(self):
        import copy
        expected = self.fixture()
        actual = copy.deepcopy(expected)
        actual['materials']['drafts'] = []
        with self.assertRaisesRegex(ValueError, 'timeline count/identity'):
            native_export.verify_runtime_graph(expected, actual)
        native_export.verify_runtime_graph(expected, copy.deepcopy(expected))

    def test_child_staged_with_integrity_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            files = native_export.stage_compound_inputs(self.fixture(), out)
            self.assertEqual(len(files), 1)
            for name, info in files.items():
                self.assertEqual(runtime.digest(out / name), info['sha256'])
                self.assertEqual(json.loads((out / name).read_text())['id'], 'leaf')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                native_export.stage_compound_inputs(self.fixture(), out)

    def test_unsafe_combination_id_rejected(self):
        value = self.fixture()
        value['materials']['drafts'][0]['combination_id'] = '../escape'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'Invalid compound identity'):
                native_export.stage_compound_inputs(value, Path(tmp))


if __name__ == '__main__':
    unittest.main()
