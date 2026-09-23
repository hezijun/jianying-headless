#!/usr/bin/env python3
"""Synthetic combined 11.5.3 regression, using the normal pinned CLI.

Requires the captured resources to have been downloaded in the native app.
Creates a private work job; never registers a draft or reads user media.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'skills/yichen-jianying-edit/scripts/headless_draft.py'


def main():
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix='advanced-smoke-', dir=work))

    def run(name, command):
        result = subprocess.run(command, capture_output=True, timeout=180)
        (job / (name + '.stdout.log')).write_bytes(result.stdout)
        (job / (name + '.stderr.log')).write_bytes(result.stderr)
        if result.returncode:
            raise RuntimeError(name + ' failed; evidence: ' + str(job))
        return result.stdout

    doctor = json.loads(run('doctor', [sys.executable, str(ENTRY), 'doctor']))
    if doctor['runtime_profile'] != 'jy14-headless-macos-11.5.3':
        raise RuntimeError('This regression targets the pinned 11.5.3 runtime')
    for color in ('red', 'blue'):
        run(color, ['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                    'color=c=' + color + ':size=640x360:rate=30:duration=8',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(job / (color + '.mp4'))])
    run('tone', ['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                 'sine=frequency=440:sample_rate=48000:duration=6', str(job / 'tone.wav')])

    def points(a, b):
        return [{'at_us': 0, 'value': a}, {'at_us': 6_000_000, 'value': b}]

    plan = {'schema': 'jy14-headless-plan/v1', 'name': job.name,
            'canvas': {'width': 640, 'height': 360, 'fps': 30}, 'tracks': [
        {'type': 'video', 'segments': [
            {'source': str(job / 'red.mp4'), 'source_start_us': 1_000_000, 'start_us': 0,
             'duration_us': 3_000_000, 'transition_out': {'name': 'dissolve', 'duration_us': 400_000}},
            {'source': str(job / 'blue.mp4'), 'source_start_us': 1_000_000,
             'start_us': 3_000_000, 'duration_us': 3_000_000}]},
        {'type': 'video', 'segments': [
            {'source': str(job / 'blue.mp4'), 'start_us': 0, 'duration_us': 6_000_000,
             'mask': {'shape': 'star', 'width': .6, 'height': .6},
             'keyframes': {'x': points(-.5, .5), 'rotation': points(0, 60), 'scale': points(.4, .6)}}]},
        {'type': 'text', 'segments': [
            {'text': '11.5.3 进阶适配验收', 'start_us': 0, 'duration_us': 6_000_000, 'size': 7,
             'text_effect': {'name': 'yellow-retro'},
             'keyframes': {'x': points(-.1, .1), 'y': points(-.5, -.3),
                           'scale': points(.8, 1.2), 'rotation': points(-5, 5)}}]},
        {'type': 'audio', 'segments': [
            {'source': str(job / 'tone.wav'), 'start_us': 0, 'duration_us': 6_000_000,
             'keyframes': {'volume': points(.1, .5)}}]},
        {'type': 'effect', 'segments': [
            {'name': 'light-shake', 'start_us': 0, 'duration_us': 6_000_000}]}]}
    path = job / 'plan.json'
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    run('build', [sys.executable, str(ENTRY), 'build', '--plan', str(path), '--out', str(job / 'build')])
    run('export', [sys.executable, str(ENTRY), 'export', '--build', str(job / 'build'), '--out', str(job / 'export')])
    result = json.loads((job / 'export/result.json').read_bytes())
    if result['media']['frame_delta'] != 0 or not result['native_async_completion_event']:
        raise RuntimeError('Incomplete frame/completion regression: ' + str(job))
    print(json.dumps({'status': result['status'], 'media': result['media'], 'audit': str(job)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
