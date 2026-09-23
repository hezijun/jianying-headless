#!/usr/bin/env python3
"""Pinned Skill entrypoint for a separately checked-out Jianying Headless project."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

def project_root():
    configured = os.environ.get('JIANYING_HEADLESS_ROOT')
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise SystemExit('JIANYING_HEADLESS_ROOT must be an absolute checkout path')
        candidates = [path.resolve()]
    else:
        candidates = list(Path(__file__).resolve().parents)
    for path in candidates:
        marker = path / 'project.json'
        if marker.is_file() and not marker.is_symlink():
            try:
                identity = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if identity.get('id') == 'jianying-headless' and identity.get('schema') == 'jianying-headless-project/v1':
                return path
    raise SystemExit('Jianying Headless checkout unavailable. Clone the project with authorized GitHub access, '
                     'then set JIANYING_HEADLESS_ROOT to its absolute path. The Skill alone does not include the engine.')


PROJECT_ROOT = project_root()
BACKEND = PROJECT_ROOT / 'engine'
PINS = {
    'native_ui_compat.py': 'e16d91ef56a216bd42f20274770de5dcaab36b701a3b20b6d459dbda8d1da4e0',
    'native_ui_settings.cpp': '5c1bc9febf9a7c921b8b5e4c30f3901ae5f82ce2f93ed48d67695e57266c1ade',
    'jy14_headless.py': '18d27abebb380e5bf69467d037cbf7ae3876782f46cd8421d274b5a1a0b4b5c0',
    'native_motion.py': '5d743caaa38c921779166e5663d36f72a0c3fdb130a690ac3942a7adcf62d6c2',
    'native_effects.py': 'c46b2fc9221dd613f220564b752e532f8f3753dd5595aaffc24f41d5236e4e97',
    'native_resources.py': 'b70333bef48bfe4f9152c623265dcfa4860c62b76a80a47a87447955b22ce61e',
    'native_visual_effects.py': 'f652ba781979dc8d64f61b064d258351f529573fd07aa85d573625656a3e6cea',
    'native-resource-catalog.json': '68021d765aa212436891056d06f205ef96365e1b687a3fdc28691f00a50105c5',
    'native_compound.py': 'b054dd8b5a3f8d0211185bc0564bb18da8b04a40844d872db50fc3b0050729b4',
    'compound-blueprint.json': '9cba9435053280abf9072d5eaccb8586c841b11dac6854b32daf9cbdba76af8e',
    'native_edit.py': 'c75652ab89ba829619889b34c40b6e8fd0cc1b558988c86a21134722c7912b55',
    'native_export.py': 'dddd779f19c7e23432d607f605ae8e922a7fec1a67a3e576606c2a4e83abdb00',
    'native_export.cpp': 'e7502e45ee09016e486256b682c5163c6f4e8a5b5a0b10d9f623325f9ca8d2fa',
    'headless_runtime.py': '48786d05df45d07808ea6a125cfbb1c0b839ca4e32aa8371a50ea93c6942b476',
    'blueprint.json': '91f7eddad5bff9af23eb88b53713c180e3e3d4054edd469140cfa9aa56bc1dc9',
}

for name, expected in PINS.items():
    path = BACKEND / name
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit('Headless component changed or unavailable; reverify before updating the pin: ' + name)

sys.path.insert(0, str(BACKEND))
entrypoint = 'jy14_headless.py'
if len(sys.argv) > 1 and sys.argv[1] == 'ui-compat':
    entrypoint = 'native_ui_compat.py'
    del sys.argv[1]
if len(sys.argv) > 1 and sys.argv[1] == 'edit':
    entrypoint = 'native_edit.py'
    del sys.argv[1]
elif len(sys.argv) > 1 and sys.argv[1] == 'export':
    entrypoint = 'native_export.py'
    del sys.argv[1]
runpy.run_path(str(BACKEND / entrypoint), run_name='__main__')
