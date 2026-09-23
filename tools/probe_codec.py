#!/usr/bin/env python3
"""Repeat the signed-runtime codec check using synthetic JSON in a sandbox."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
import headless_runtime as runtime


def main():
    identity = runtime.validate_runtime()
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix='codec-probe-', dir=work)).resolve()
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    # The hardened codec opens every output ancestor with O_DIRECTORY. Permit
    # those exact directories, never their sibling files or recursive trees.
    profile = ('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'
               '(deny file-read-data (subpath "/Users"))\n'
               '(deny file-read-data (subpath "/Library/Keychains"))\n'
               '(allow file-read-data (subpath ' + quote(job) + '))\n'
               '(allow file-write* (subpath ' + quote(job) + '))\n'
               '(allow file-write* (literal "/dev/null"))\n'
               '(allow file-read-data (literal ' + quote(runtime.BACKEND / identity['codec_name']) + '))\n')
    profile += ''.join('(allow file-read-data (literal ' + quote(p) + '))\n' for p in job.parents)
    policy = job / 'probe.sb'
    policy.write_text(profile)
    sample = {'schema': 'synthetic-codec-probe/v1', 'text': '剪映适配🙂',
              'tracks': [], 'duration': 3000000, 'nested': {'speed': 1.5, 'enabled': True}}
    payload = json.dumps(sample, ensure_ascii=False).encode()
    (job / 'input.json').write_bytes(payload)
    codec = runtime.BACKEND / identity['codec_name']
    env = {k: v for k, v in os.environ.items() if not k.startswith('DYLD_') and k not in {'PYTHONHOME', 'PYTHONPATH'}}

    def invoke(name, mode, source, target):
        result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(policy), str(codec), mode,
                                 str(job / source), str(job / target)], capture_output=True, timeout=30, env=env)
        (job / (name + '.stdout.log')).write_bytes(result.stdout)
        (job / (name + '.stderr.log')).write_bytes(result.stderr)
        return result.returncode

    if invoke('encrypt', 'encrypt', 'input.json', 'cipher.bin'):
        raise RuntimeError('Encryption failed: ' + str(job))
    if invoke('decrypt', 'decrypt', 'cipher.bin', 'output.json'):
        raise RuntimeError('Decryption failed: ' + str(job))
    if (job / 'output.json').read_bytes() != payload or (job / 'cipher.bin').read_bytes() == payload:
        raise RuntimeError('Codec roundtrip differs: ' + str(job))
    (job / 'invalid.bin').write_bytes(b'not-an-encrypted-draft')
    rejected = invoke('invalid', 'decrypt', 'invalid.bin', 'invalid-output.json') != 0
    if not rejected or (job / 'invalid-output.json').exists():
        raise RuntimeError('Invalid ciphertext was not rejected cleanly: ' + str(job))
    if runtime.doctor()['codec_sha256'] != identity['codec_sha256']:
        raise RuntimeError('Runtime changed during probe')
    report = dict(status='codec-roundtrip-verified', runtime=identity, invalid_ciphertext_rejected=True,
                  user_media_used=False, live_draft_written=False, network_allowed=False,
                  evidence=str(job))
    (job / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
